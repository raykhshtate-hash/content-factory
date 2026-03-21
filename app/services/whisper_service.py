import io
import logging
import os
import subprocess
import tempfile

import httpx
import openai
from openai import APIError, APIConnectionError, RateLimitError
from tenacity import retry, retry_if_exception_type, wait_exponential, stop_after_attempt

logger = logging.getLogger(__name__)

# ── Silence Map Generator (Step 1 of Hybrid Mode) ──────────────────

FILLER_WORDS = {"э", "эм", "ум"}


def analyze_silence(
    word_timestamps: list[dict],
    gap_threshold: float = 1.0,
    min_speech_duration: float = 0.8,
    filler_gap_threshold: float = 1.0,
) -> list[dict]:
    """
    Build a silence map from Whisper word-level timestamps.

    Input:  [{"word": "привет", "start": 0.5, "end": 0.9}, ...]
    Output: sorted list of {"type": "speech"|"silence", "start", "end", "words"?}
    """
    if not word_timestamps:
        return []

    # ── Step 1: Group words into raw speech blocks ──
    blocks: list[list[dict]] = []
    current: list[dict] = [word_timestamps[0]]

    for w in word_timestamps[1:]:
        prev_end = current[-1]["end"]
        if w["start"] - prev_end > gap_threshold:
            blocks.append(current)
            current = [w]
        else:
            current.append(w)
    blocks.append(current)

    # ── Step 2: Min duration filter ──
    blocks = [
        b for b in blocks
        if b[-1]["end"] - b[0]["start"] >= min_speech_duration
    ]

    # ── Step 3: Filler filter on single-word blocks ──
    filtered: list[list[dict]] = []
    for i, b in enumerate(blocks):
        if len(b) == 1 and b[0]["word"].strip().lower() in FILLER_WORDS:
            # Check gaps to previous and next remaining speech blocks
            prev_end = filtered[-1][-1]["end"] if filtered else None
            next_start = None
            for j in range(i + 1, len(blocks)):
                # Skip future single-word fillers that might also be removed —
                # conservative: check against all remaining blocks.
                next_start = blocks[j][0]["start"]
                break

            gap_before = (b[0]["start"] - prev_end) if prev_end is not None else float("inf")
            gap_after = (next_start - b[0]["end"]) if next_start is not None else float("inf")

            if gap_before > filler_gap_threshold and gap_after > filler_gap_threshold:
                continue  # drop isolated filler
        filtered.append(b)
    blocks = filtered

    if not blocks:
        return []

    # ── Step 4: Safety margin (+0.2s, capped by next word in original) ──
    # Build lookup: for each word end time, find next word's start
    next_word_start: dict[float, float] = {}
    for idx in range(len(word_timestamps) - 1):
        next_word_start[word_timestamps[idx]["end"]] = word_timestamps[idx + 1]["start"]

    for b in blocks:
        last_end = b[-1]["end"]
        cap = next_word_start.get(last_end)
        margin = last_end + 0.2
        if cap is not None:
            b[-1] = {**b[-1], "end": min(margin, cap)}
        else:
            b[-1] = {**b[-1], "end": margin}

    # ── Step 5: Assemble speech + silence segments ──
    segments: list[dict] = []
    first_start = blocks[0][0]["start"]

    # Leading silence
    if first_start > 0:
        segments.append({"type": "silence", "start": 0, "end": first_start})

    for i, b in enumerate(blocks):
        segments.append({
            "type": "speech",
            "start": b[0]["start"],
            "end": b[-1]["end"],
            "words": b,
        })
        # Gap to next block → silence
        if i + 1 < len(blocks):
            gap_start = b[-1]["end"]
            gap_end = blocks[i + 1][0]["start"]
            if gap_end > gap_start:
                segments.append({"type": "silence", "start": gap_start, "end": gap_end})

    return segments


class WhisperService:
    def __init__(self, api_key: str):
        self.client = openai.AsyncOpenAI(api_key=api_key)
        self.model = "whisper-1"

    async def transcribe(self, audio_bytes: bytes, filename: str = "voice.ogg") -> str:
        """
        Transcribes audio bytes using OpenAI Whisper API.
        """
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename

        transcription = await self.client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="ru",
        )
        return transcription.text

    @retry(
        retry=retry_if_exception_type((APIError, APIConnectionError, RateLimitError)),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    async def _do_transcribe(self, tmp_path: str):
        with open(tmp_path, "rb") as audio_file:
            return await self.client.audio.transcriptions.create(
                model=self.model,
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["word"]
            )

    async def transcribe_url_with_timestamps(
        self, video_url: str, trim_start: int = 0, trim_duration: int | None = None
    ) -> list[dict]:
        """
        Download video from a public URL and transcribe with word-level timestamps.
        Returns list of {text, start, end} dicts (times relative to trim_start=0).
        """
        tmp_in_path = None
        tmp_out_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_in:
                tmp_in_path = tmp_in.name
                
            async with httpx.AsyncClient(timeout=120) as http:
                async with http.stream("GET", video_url) as r:
                    r.raise_for_status()
                    with open(tmp_in_path, "wb") as f:
                        async for chunk in r.aiter_bytes(chunk_size=1024 * 1024):
                            f.write(chunk)

            # ffmpeg command...
            tmp_out_path = tmp_in_path.replace(".mp4", "_trimmed.mp3")
            
            cmd = ["ffmpeg", "-y", "-i", tmp_in_path]
            if trim_start > 0:
                cmd.extend(["-ss", str(trim_start)])
            if trim_duration:
                cmd.extend(["-t", str(trim_duration)])
                
            cmd.extend([
                "-vn",             # No video
                "-acodec", "libmp3lame",
                "-ab", "128k",
                "-ar", "44100",
                "-ac", "2",
                tmp_out_path
            ])

            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # Run API call via Tenacity wrapped function
            transcription = await self._do_transcribe(tmp_out_path)

            if not hasattr(transcription, "words") or not transcription.words:
                return []
                
            result = []
            for word_info in transcription.words:
                result.append({
                    "word": word_info.word,   # key must match handlers.py w["word"]
                    "start": word_info.start,
                    "end": word_info.end
                })
                
            return result

        except Exception as e:
            logger.error(f"Whisper transcription failed for {video_url}: {e}")
            return [] # Non-fatal, just return no subtitles if it's completely dead

        finally:
            if tmp_in_path and os.path.exists(tmp_in_path):
                os.remove(tmp_in_path)
            if tmp_out_path and os.path.exists(tmp_out_path):
                os.remove(tmp_out_path)
