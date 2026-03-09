"""
Gemini Video Analysis Service — Vertex AI edition.

Uses Vertex AI backend so videos stored in GCS can be passed
directly via gs:// URI without downloading to the server.
Zero memory footprint, zero File API, zero cleanup needed.
"""

from typing import Optional
from pydantic import BaseModel, Field

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError
from tenacity import retry, retry_if_exception, wait_exponential, stop_after_attempt


# ── Structured Output Schema ────────────────────────────────────

class ClipCandidate(BaseModel):
    video_index: int = Field(description="Порядковый номер видео (начиная с 1)", default=1)
    start_time: str = Field(description="Время начала клипа в формате MM:SS.s (с десятыми секунды), например 01:23.5")
    end_time: str = Field(description="Время окончания клипа в формате MM:SS.s (с десятыми секунды), например 01:37.0. Включи запас 0.5с после конца фразы")
    reason: str = Field(description="Почему этот момент потенциально виральный")


class VideoAnalysis(BaseModel):
    clip_candidates: list[ClipCandidate] = Field(
        description="Список лучших моментов из видео с таймкодами"
    )
    hook_score: int = Field(
        description="Оценка хука в начале видео (от 1 до 10)", ge=1, le=10
    )
    confidence: float = Field(
        description="Уверенность AI в анализе (от 0.0 до 1.0)", ge=0.0, le=1.0
    )
    visual_risk: str = Field(
        description="Визуальные риски (кровь, бренды, nudity и т.д.). Если нет — 'none'"
    )
    suggested_music_mood: str = Field(
        description="Рекомендуемое настроение фоновой музыки: upbeat, chill, dramatic, funny",
        default="chill"
    )


# ── Service ─────────────────────────────────────────────────────

class GeminiService:
    def __init__(
        self,
        project_id: str = "romina-content-factory-489121",
        location: str = "europe-west1",
    ):
        try:
            self.client = genai.Client(
                vertexai=True,
                project=project_id,
                location=location,
            )
        except Exception as e:
            print(f"⚠️ Vertex AI init failed: {e}")
            self.client = None

    @retry(
        retry=retry_if_exception(
            lambda e: isinstance(e, (ServerError,))
            or (isinstance(e, ClientError) and getattr(e, "code", 0) == 429)
        ),
        wait=wait_exponential(multiplier=3, min=10, max=60),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def analyze_video(
        self,
        gcs_uris: list[str] | str,
        prompt: str,
        model: str = "gemini-2.5-flash",
    ) -> Optional[VideoAnalysis]:
        """
        Analyze one or multiple videos directly from GCS via gs:// URIs.
        Returns structured VideoAnalysis or None on failure.
        """
        if not self.client:
            print("❌ Gemini client not configured.")
            return None

        if isinstance(gcs_uris, str):
            gcs_uris = [gcs_uris]

        try:
            print(f"🧠 [Gemini] Analyzing {len(gcs_uris)} videos...")

            contents = []
            for uri in gcs_uris:
                contents.append(
                    types.Part.from_uri(
                        file_uri=uri,
                        mime_type="video/mp4",
                    )
                )
            # Add the prompt at the end
            contents.append(prompt)

            import asyncio
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=VideoAnalysis,
                    temperature=0,
                ),
            )

            if response.parsed:
                print("✅ [Gemini] Analysis parsed successfully.")
                return response.parsed

            # Fallback: manual JSON parse
            print("⚠️ [Gemini] Fallback to manual JSON parsing.")
            return VideoAnalysis.model_validate_json(response.text)

        except (ClientError, ServerError):
            raise  # let @retry handle 429 / 5xx
        except Exception as e:
            print(f"❌ [Gemini] Error: {e}")
            return None
