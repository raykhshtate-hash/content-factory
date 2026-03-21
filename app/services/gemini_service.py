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
    scene_label: str = Field(description="К какой сцене сценария относится этот клип: HOOK, STORY, PIVOT, CLOSING", default="")
    clip_type: str = Field(
        description="'speech' if clip contains spoken words (from audio map), 'broll' if silence/ambient only",
        default="speech",
    )
    visual_description: str = Field(
        default="",
        description="Кратко что визуально в кадре: лицо спикера крупным планом, еда на столе, пейзаж города, руки делают процедуру — 5-10 слов",
    )


class StoryboardScene(BaseModel):
    scene_id: int = Field(description="Номер сцены, начиная с 1")
    audio_start: float = Field(description="Начало аудиосегмента в секундах (в обработанном аудио)")
    audio_end: float = Field(description="Конец аудиосегмента в секундах")
    video_index: int = Field(description="Номер видеоклипа (начиная с 1)")
    video_trim_start: float = Field(description="С какой секунды начать обрезку видео", default=0.0)
    video_trim_duration: float = Field(description="Длительность фрагмента видео в секундах")
    transition_type: str = Field(
        description="Тип перехода К этой сцене: 'cut', 'crossfade', 'slide-left', 'slide-right', 'wipe', 'circular-wipe'. Для первой сцены всегда 'cut'.",
        default="cut",
    )
    visual_description: str = Field(
        description="Кратко что визуально происходит в этом клипе, 5-10 слов. Например: 'женщина идёт по улице', 'крупный план лица', 'руки держат документы'",
        default="",
    )


class StoryboardAnalysis(BaseModel):
    scenes: list[StoryboardScene] = Field(description="Маппинг сцен: аудио → видео")
    total_duration: float = Field(description="Общая длительность рилса в секундах")
    suggested_music_mood: str = Field(
        description="Настроение: upbeat, chill, dramatic, funny, professional",
        default="professional",
    )


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
        audio_map: list[dict] | None = None,
    ) -> Optional[VideoAnalysis]:
        """
        Analyze one or multiple videos directly from GCS via gs:// URIs.
        Returns structured VideoAnalysis or None on failure.

        If audio_map is provided (from analyze_silence), Gemini receives
        speech/silence segment data and assigns clip_type per clip.
        """
        if not self.client:
            print("❌ Gemini client not configured.")
            return None

        if isinstance(gcs_uris, str):
            gcs_uris = [gcs_uris]

        # ── Inject audio map into prompt if provided ──
        if audio_map:
            import json
            audio_map_json = json.dumps(audio_map, ensure_ascii=False, indent=2)
            prompt += (
                "\n\nАУДИО-КАРТА ВИДЕО (данные от Whisper):\n"
                f"{audio_map_json}\n\n"
                "ПРАВИЛА clip_type (ОБЯЗАТЕЛЬНЫ при наличии аудио-карты):\n"
                "- Присваивай clip_type=\"speech\" ТОЛЬКО клипам, попадающим в интервалы "
                "type=\"speech\" по аудио-карте.\n"
                "- Интервалы type=\"silence\" можно использовать ТОЛЬКО как clip_type=\"broll\".\n"
                "- Если на видео крупный план лица с артикуляцией губами, но по аудио-карте "
                "это silence — КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать как broll. "
                "Пропусти этот сегмент.\n"
                "- B-roll клипы должны быть визуально интересными: еда, пейзаж, детали, движение.\n"
                "- Чередуй speech и broll для динамичного монтажа.\n"
            )

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

    @retry(
        retry=retry_if_exception(
            lambda e: isinstance(e, (ServerError,))
            or (isinstance(e, ClientError) and getattr(e, "code", 0) == 429)
        ),
        wait=wait_exponential(multiplier=3, min=10, max=60),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def analyze_storyboard(
        self,
        video_gcs_uris: list[str],
        audio_gcs_uri: str,
        scenario_text: str,
        video_durations: list[float] | None = None,
        audio_duration: float = 0.0,
        model: str = "gemini-2.5-flash",
    ) -> Optional[StoryboardAnalysis]:
        """
        Analyze storyboard: map processed voiceover segments to video clips.

        Gemini receives:
        - N video files (one per scene, in order)
        - 1 processed audio file (voiceover with silences removed + speedup)
        - scenario text

        Returns: StoryboardAnalysis with per-scene audio↔video mapping.
        """
        if not self.client:
            print("❌ Gemini client not configured.")
            return None

        num_videos = len(video_gcs_uris)

        video_info = ""
        if video_durations:
            lines = [f"  Видео {i+1}: {d:.1f}с" for i, d in enumerate(video_durations)]
            video_info = (
                f"ДЛИТЕЛЬНОСТИ ВИДЕОКЛИПОВ (измерено инструментально):\n"
                + "\n".join(lines)
                + f"\nОбщая длительность видеоряда: {sum(video_durations):.1f}с\n\n"
            )

        prompt = (
            f"Ты — режиссёр монтажа Instagram Reels.\n\n"
            f"Тебе даны {num_videos} видеоклипов и 1 аудиофайл озвучки.\n"
            f"Видеоклипы пронумерованы от 1 до {num_videos} в порядке загрузки.\n"
            f"Каждый видеоклип = одна сцена из раскадровки.\n\n"
            f"ТОЧНАЯ ДЛИТЕЛЬНОСТЬ ОЗВУЧКИ: {audio_duration:.1f} секунд.\n"
            f"Это измерено инструментально — не пытайся определить длительность сам.\n\n"
            f"{video_info}"
            f"Сценарий:\n{scenario_text}\n\n"
            f"ЗАДАЧА:\n"
            f"1. Прослушай аудиофайл озвучки.\n"
            f"2. Раздели озвучку РОВНО на {num_videos} сегментов.\n"
            f"   Границы — по естественным паузам между фразами.\n"
            f"3. Распредели сегменты по сценарной логике:\n"
            f"   - Видео 1 (HOOK/вступление): дай достаточно времени чтобы зритель\n"
            f"     вовлёкся. Не обрезай hook слишком коротко.\n"
            f"   - Средние видео: распредели ПРОПОРЦИОНАЛЬНО длительности клипов.\n"
            f"     Длинный клип = длинный аудиосегмент.\n"
            f"   - Последнее видео (CLOSING/заключение): дай достаточно времени для\n"
            f"     завершающей мысли и call to action. Не обрезай коротко.\n"
            f"4. Для каждого сегмента: audio_start, audio_end.\n"
            f"5. video_index = scene_id (1-to-1).\n"
            f"6. video_trim_start = 0.0 для всех.\n"
            f"7. video_trim_duration = audio_end - audio_start.\n"
            f"8. Для каждой сцены опиши что визуально происходит в клипе (visual_description), 5-10 слов.\n"
            f"9. Для каждой сцены выбери transition_type — тип перехода К этой сцене:\n"
            f"   - 'cut': резкий монтаж (стандарт рилсов, быстро и энергично)\n"
            f"   - 'crossfade': плавный переход между сценами (для смены настроения)\n"
            f"   - 'slide-left': свайп влево (энергичная смена, действие)\n"
            f"   - 'slide-right': свайп вправо (возврат, рефлексия)\n"
            f"   - 'wipe': шторка (новая тема, контраст)\n"
            f"   - 'circular-wipe': круговая шторка (вау-момент)\n"
            f"   Для scene_id=1 ВСЕГДА 'cut'.\n"
            f"   Чередуй типы, не ставь одинаковые подряд. 60% должны быть 'cut'.\n\n"
            f"ЖЁСТКИЕ ОГРАНИЧЕНИЯ:\n"
            f"- Ровно {num_videos} сцен, scene_id от 1 до {num_videos}.\n"
            f"- audio_start первой сцены = 0.0\n"
            f"- audio_end последней сцены = {audio_duration:.1f}\n"
            f"- Сегменты подряд без пропусков: audio_end[N] = audio_start[N+1].\n"
            f"- total_duration = {audio_duration:.1f}\n"
            f"- video_trim_duration каждой сцены НЕ ПРЕВЫШАЕТ длительность "
            f"соответствующего видеоклипа.\n"
        )

        try:
            print(f"🧠 [Gemini] Analyzing storyboard: {num_videos} videos + audio...")

            contents = []
            for uri in video_gcs_uris:
                contents.append(
                    types.Part.from_uri(file_uri=uri, mime_type="video/mp4")
                )
            # Audio — determine mime type from URI
            audio_mime = "audio/mpeg"
            if audio_gcs_uri.lower().endswith(".wav"):
                audio_mime = "audio/wav"
            elif audio_gcs_uri.lower().endswith(".m4a"):
                audio_mime = "audio/mp4"
            elif audio_gcs_uri.lower().endswith(".ogg"):
                audio_mime = "audio/ogg"
            contents.append(
                types.Part.from_uri(file_uri=audio_gcs_uri, mime_type=audio_mime)
            )
            contents.append(prompt)

            import asyncio as _asyncio
            response = await _asyncio.to_thread(
                self.client.models.generate_content,
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=StoryboardAnalysis,
                    temperature=0,
                ),
            )

            if response.parsed:
                print("✅ [Gemini] Storyboard analysis parsed successfully.")
                return response.parsed

            print("⚠️ [Gemini] Fallback to manual JSON parsing.")
            return StoryboardAnalysis.model_validate_json(response.text)

        except (ClientError, ServerError):
            raise
        except Exception as e:
            print(f"❌ [Gemini] Storyboard error: {e}")
            return None
