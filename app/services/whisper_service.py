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
