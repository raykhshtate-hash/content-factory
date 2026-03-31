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


# ── Voiceover Segment Refinement ──────────────────────────────────

def refine_voiceover_segments(
    segments: list[dict],
    words: list[dict],
    min_duration: float = 3.0,
    max_duration: float = 12.0,
) -> list[dict]:
    """Re-segment voiceover using word-level phrase boundary detection.

    Whisper segments rely on long pauses that may not exist in
    fast-paced speech.  This detects phrase boundaries via two signals:

    1. **Real gaps** between words (actual silence in the audio).
    2. **Hidden pauses** — Whisper absorbs trailing silence into the
       preceding word's duration, making it abnormally long.  Words
       whose duration exceeds the median by a large margin signal a
       natural phrase end.

    Short resulting segments (< *min_duration*) are merged forward
    into the next segment (transition phrases usually introduce the
    next topic).  A *max_duration* fallback splits any remaining
    overlong segments at the best word boundary.
    """
    if not words or not segments:
        return segments

    # Filter zero-duration Whisper glitches
    valid = [w for w in words if w["end"] > w["start"]]
    if len(valid) < 2:
        return segments

    # ── Step 1: detect phrase boundaries ──
    durations = sorted(w["end"] - w["start"] for w in valid)
    median_dur = durations[len(durations) // 2]

    HIDDEN_COEFF = 0.4  # fraction of excess word-duration treated as pause
    GAP_THRESHOLD = 0.35

    split_indices: list[int] = []  # index into *valid* — split BEFORE this word
    for i in range(len(valid) - 1):
        actual_gap = valid[i + 1]["start"] - valid[i]["end"]
        hidden_pause = max(0.0, (valid[i]["end"] - valid[i]["start"]) - median_dur) * HIDDEN_COEFF
        if actual_gap + hidden_pause >= GAP_THRESHOLD:
            split_indices.append(i + 1)

    if not split_indices:
        return segments  # no phrase boundaries detected

    # ── Step 2: build raw segments from split points ──
    raw: list[dict] = []
    prev = 0
    for sp in split_indices:
        chunk = valid[prev:sp]
        if chunk:
            raw.append({
                "start": chunk[0]["start"],
                "end": chunk[-1]["end"],
                "text": " ".join(w["word"] for w in chunk).strip(),
            })
        prev = sp
    chunk = valid[prev:]
    if chunk:
        raw.append({
            "start": chunk[0]["start"],
            "end": chunk[-1]["end"],
            "text": " ".join(w["word"] for w in chunk).strip(),
        })

    if not raw:
        return segments

    # ── Step 3: merge short segments forward ──
    merged: list[dict] = []
    i = 0
    while i < len(raw):
        seg = raw[i]
        dur = seg["end"] - seg["start"]
        if dur < min_duration and i + 1 < len(raw):
            nxt = raw[i + 1]
            merged.append({
                "start": seg["start"],
                "end": nxt["end"],
                "text": (seg["text"] + " " + nxt["text"]).strip(),
            })
            i += 2
        else:
            merged.append(seg)
            i += 1

    # Last segment too short → merge backward
    if len(merged) > 1 and merged[-1]["end"] - merged[-1]["start"] < min_duration:
        merged[-2]["end"] = merged[-1]["end"]
        merged[-2]["text"] = (merged[-2]["text"] + " " + merged[-1]["text"]).strip()
        merged.pop()

    # ── Step 4: fallback — split any remaining overlong segment at midpoint ──
    final: list[dict] = []
    for seg in merged:
        seg_dur = seg["end"] - seg["start"]
        if seg_dur <= max_duration:
            final.append(seg)
            continue
        seg_words = [w for w in valid if w["start"] >= seg["start"] - 0.01 and w["end"] <= seg["end"] + 0.01]
        if len(seg_words) < 2:
            final.append(seg)
            continue
        mid = (seg["start"] + seg["end"]) / 2
        best_idx = min(range(1, len(seg_words)), key=lambda j: abs(seg_words[j]["start"] - mid))
        left = seg_words[:best_idx]
        right = seg_words[best_idx:]
        if left and right:
            final.append({"start": left[0]["start"], "end": left[-1]["end"],
                          "text": " ".join(w["word"] for w in left).strip()})
            final.append({"start": right[0]["start"], "end": right[-1]["end"],
                          "text": " ".join(w["word"] for w in right).strip()})
        else:
            final.append(seg)

    return final if final else segments


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
        self._last_cost_usd: float = 0.0

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
    async def _do_transcribe(self, tmp_path: str, granularities: list[str] | None = None, language: str | None = None):
        if granularities is None:
            granularities = ["word"]
        with open(tmp_path, "rb") as audio_file:
            kwargs = {
                "model": self.model,
                "file": audio_file,
                "response_format": "verbose_json",
                "timestamp_granularities": granularities,
            }
            if language is not None:
                kwargs["language"] = language
            return await self.client.audio.transcriptions.create(**kwargs)

    async def _download_and_convert(
        self, url: str, trim_start: int = 0, trim_duration: int | None = None
    ) -> tuple[str, str]:
        """Download media from URL and convert to mp3 via ffmpeg.
        Returns (input_path, output_mp3_path). Caller must clean up both files.
        """
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_in:
            tmp_in_path = tmp_in.name

        async with httpx.AsyncClient(timeout=120) as http:
            async with http.stream("GET", url) as r:
                r.raise_for_status()
                with open(tmp_in_path, "wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)

        tmp_out_path = tmp_in_path.replace(".mp4", "_trimmed.mp3")

        cmd = ["ffmpeg", "-y", "-i", tmp_in_path]
        if trim_start > 0:
            cmd.extend(["-ss", str(trim_start)])
        if trim_duration:
            cmd.extend(["-t", str(trim_duration)])
        cmd.extend([
            "-vn",
            "-acodec", "libmp3lame",
            "-ab", "128k",
            "-ar", "44100",
            "-ac", "2",
            tmp_out_path,
        ])

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return tmp_in_path, tmp_out_path

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
            tmp_in_path, tmp_out_path = await self._download_and_convert(
                video_url, trim_start, trim_duration
            )

            transcription = await self._do_transcribe(tmp_out_path)

            # ── Cost extraction: $0.006/min of audio ──
            duration_sec = getattr(transcription, "duration", None)
            if duration_sec:
                cost_usd = (float(duration_sec) / 60.0) * 0.006
                self._last_cost_usd = cost_usd
                logger.info("Whisper cost: $%.4f (%.1fs audio)", cost_usd, float(duration_sec))

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
            return []

        finally:
            if tmp_in_path and os.path.exists(tmp_in_path):
                os.remove(tmp_in_path)
            if tmp_out_path and os.path.exists(tmp_out_path):
                os.remove(tmp_out_path)

    async def transcribe_url_with_segments(self, url: str) -> dict | None:
        """Transcribe audio URL and return segment-level timestamps.
        For voiceover awareness in Gemini prompt.

        Returns {"voiceover_duration": float, "segments": [{"start", "end", "text"}, ...]}
        or None on failure.
        """
        tmp_in_path = None
        tmp_out_path = None
        try:
            tmp_in_path, tmp_out_path = await self._download_and_convert(url)

            transcription = await self._do_transcribe(
                tmp_out_path, granularities=["word", "segment"], language="ru"
            )

            # ── Cost extraction: $0.006/min of audio ──
            duration_sec = getattr(transcription, "duration", None)
            if duration_sec:
                cost_usd = (float(duration_sec) / 60.0) * 0.006
                self._last_cost_usd = cost_usd
                logger.info("Whisper cost: $%.4f (%.1fs audio)", cost_usd, float(duration_sec))

            segments_raw = getattr(transcription, "segments", None)
            if not segments_raw:
                logger.warning("Voiceover transcription returned no segments")
                return None

            segments = []
            for seg in segments_raw:
                start = getattr(seg, "start", None)
                if start is None and isinstance(seg, dict):
                    start = seg.get("start")
                end = getattr(seg, "end", None)
                if end is None and isinstance(seg, dict):
                    end = seg.get("end")
                text = getattr(seg, "text", None)
                if text is None and isinstance(seg, dict):
                    text = seg.get("text", "")

                if start is not None and end is not None:
                    segments.append({
                        "start": float(start),
                        "end": float(end),
                        "text": str(text).strip(),
                    })

            if not segments:
                logger.warning("Voiceover transcription: no valid segments extracted")
                return None

            vo_duration = segments[-1]["end"]
            if vo_duration < 3.0:
                logger.warning("Voiceover too short (%.1fs), skipping", vo_duration)
                return None

            # Extract word-level timestamps for broll popup karaoke
            words = []
            words_raw = getattr(transcription, "words", None)
            if words_raw:
                for w in words_raw:
                    w_start = getattr(w, "start", None)
                    if w_start is None and isinstance(w, dict):
                        w_start = w.get("start")
                    w_end = getattr(w, "end", None)
                    if w_end is None and isinstance(w, dict):
                        w_end = w.get("end")
                    w_word = getattr(w, "word", None)
                    if w_word is None and isinstance(w, dict):
                        w_word = w.get("word", "")
                    if w_start is not None and w_end is not None:
                        words.append({
                            "word": str(w_word),
                            "start": float(w_start),
                            "end": float(w_end),
                        })

            # Refine: detect phrase boundaries via hidden micro-pauses
            raw_count = len(segments)
            segments = refine_voiceover_segments(segments, words)
            if len(segments) != raw_count:
                logger.info("Voiceover segments refined: %d → %d", raw_count, len(segments))
                for si, s in enumerate(segments):
                    logger.info("  seg %d: [%.2f-%.2f] %s", si, s["start"], s["end"], s["text"][:50])

            logger.info("Voiceover transcribed: %d segments, %d words, %.1fs duration",
                        len(segments), len(words), vo_duration)
            return {"voiceover_duration": vo_duration, "segments": segments, "words": words}

        except Exception as e:
            logger.warning("Voiceover segment transcription failed: %s", e)
            return None

        finally:
            if tmp_in_path and os.path.exists(tmp_in_path):
                os.remove(tmp_in_path)
            if tmp_out_path and os.path.exists(tmp_out_path):
                os.remove(tmp_out_path)
