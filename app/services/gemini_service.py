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

import logging

logger = logging.getLogger(__name__)


class GeminiSafetyError(Exception):
    """Raised when Gemini response is blocked by safety filters."""
    pass


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
    matched_voiceover_segment: int | None = Field(
        default=None,
        description="Для broll: 0-based индекс voiceover сегмента который этот клип визуально иллюстрирует. Для speech: всегда null.",
    )
    match_reason: str | None = Field(
        default=None,
        description="Почему этот broll подходит к этому сегменту voiceover (для дебага)",
    )
    unmatched_text_overlay: str | None = Field(
        default=None,
        description="Для broll БЕЗ matched voiceover: короткая игривая фраза 3-5 слов с юмором или вопросом. Для matched broll: null.",
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
        self._last_cost_usd: float = 0.0
        try:
            self.client = genai.Client(
                vertexai=True,
                project=project_id,
                location=location,
            )
        except Exception as e:
            logger.error("Vertex AI init failed: %s", e)
            self.client = None

    def _extract_cost(self, response) -> None:
        """Extract cost from Gemini response usage_metadata.
        Gemini 2.5 Flash pricing: ~$0.15/1M input, ~$0.60/1M output tokens.
        """
        try:
            meta = getattr(response, "usage_metadata", None)
            if meta:
                prompt_tokens = getattr(meta, "prompt_token_count", 0) or 0
                output_tokens = getattr(meta, "candidates_token_count", 0) or 0
                # Gemini 2.5 Flash: $0.15/$0.60 per 1M tokens (Pro is ~10x more)
                cost_usd = (prompt_tokens * 0.15 + output_tokens * 0.60) / 1_000_000
                self._last_cost_usd = cost_usd
                logger.info("Gemini cost: $%.4f (in=%d, out=%d tokens)", cost_usd, prompt_tokens, output_tokens)
            else:
                self._last_cost_usd = 0.0
        except Exception:
            self._last_cost_usd = 0.0

    @retry(
        retry=retry_if_exception(
            lambda e: isinstance(e, (ServerError, GeminiSafetyError))
            or (isinstance(e, ClientError) and getattr(e, "code", 0) == 429)
            or (isinstance(e, ClientError) and "SAFETY" in str(e).upper())
        ),
        wait=wait_exponential(multiplier=3, min=10, max=60),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def discover_story(
        self,
        gcs_uris: list[str] | str,
        voiceover_text: str | None = None,
        model: str = "gemini-2.5-flash",
        video_names: list[str] | None = None,
    ) -> str | None:
        """
        Pass 1: Watch all videos, describe what happens, identify story arc.
        Returns free-text story analysis. No clip selection — pure understanding.
        """
        if not self.client:
            return None

        if isinstance(gcs_uris, str):
            gcs_uris = [gcs_uris]

        # Build video label list
        video_label_block = ""
        if video_names:
            video_label_block = "НУМЕРАЦИЯ ВИДЕО (используй ТОЛЬКО эти номера):\n"
            video_label_block += "\n".join(video_names) + "\n\n"

        prompt = (
            f"Тебе передано {len(gcs_uris)} видео В УКАЗАННОМ ПОРЯДКЕ.\n\n"
            f"{video_label_block}"
            "ЗАДАЧА: Опиши каждое видео и найди историю.\n\n"
            "ШАГ 1 — Для КАЖДОГО видео (строго по номерам выше):\n"
            "- Номер видео и название файла\n"
            "- Что происходит с таймкодами (ММ:СС)\n"
            "- Моменты взаимодействия между людьми\n"
            "- Самые визуально красивые кадры\n\n"
            "ШАГ 2 — Найди ИСТОРИЮ через все видео:\n"
            "- ЗАВЯЗКА: Что привлекает внимание?\n"
            "- РАЗВИТИЕ: Что происходит дальше?\n"
            "- КУЛЬМИНАЦИЯ: Эмоциональный пик (момент между людьми, реакция). "
            "Это САМЫЙ ВАЖНЫЙ момент!\n"
            "- РЕЗУЛЬТАТ: Красивый финал\n\n"
            "ШАГ 3 — Итог:\n"
            "Кратко: какая история, какой эмоциональный пик, какое настроение.\n"
            "НЕ создавай план клипов — это сделает следующий этап.\n"
        )

        if voiceover_text:
            prompt += (
                f"\nОЗВУЧКА (играет поверх видео): «{voiceover_text}»\n"
                "Учти тему озвучки при описании.\n"
            )

        try:
            logger.info("[Gemini] Pass 1: Discovering story in %d videos...", len(gcs_uris))
            import asyncio

            contents = []
            for uri in gcs_uris:
                contents.append(
                    types.Part.from_uri(file_uri=uri, mime_type="video/mp4")
                )
            contents.append(prompt)

            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(temperature=0),
            )

            if not response or not response.text:
                logger.warning("[Gemini] Pass 1: Empty response")
                return None

            self._extract_cost(response)
            story = response.text.strip()
            logger.info("[Gemini] Pass 1: Story discovered (%d chars)", len(story))
            logger.debug("[Gemini] Story: %s", story[:500])
            return story

        except Exception as e:
            logger.error("[Gemini] Pass 1 error: %s", e)
            return None

    @retry(
        retry=retry_if_exception(
            lambda e: isinstance(e, (ServerError, GeminiSafetyError))
            or (isinstance(e, ClientError) and getattr(e, "code", 0) == 429)
            or (isinstance(e, ClientError) and "SAFETY" in str(e).upper())
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
        voiceover_data: dict | None = None,
        analysis_mode: str | None = None,
        story_context: str | None = None,
    ) -> Optional[VideoAnalysis]:
        """
        Analyze one or multiple videos directly from GCS via gs:// URIs.
        Returns structured VideoAnalysis or None on failure.

        If audio_map is provided (from analyze_silence), Gemini receives
        speech/silence segment data and assigns clip_type per clip.
        """
        if not self.client:
            logger.error("Gemini client not configured")
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

        # ── Inject voiceover transcript if provided ──
        if voiceover_data:
            vo_duration = voiceover_data["voiceover_duration"]
            segments_formatted = "\n".join(
                f'[Сегмент {idx}] [{s["start"]:.1f} - {s["end"]:.1f}с] "{s["text"]}"'
                for idx, s in enumerate(voiceover_data["segments"])
            )
            prompt += (
                "\n\n=== ЗАКАДРОВЫЙ ГОЛОС (VOICEOVER) ===\n"
                "К этим видео есть отдельная озвучка! Это набор коротких независимых фраз.\n"
                f"Общая длительность: {vo_duration:.1f} секунд.\n\n"
                "СЕГМЕНТЫ ОЗВУЧКИ (пронумерованные):\n"
                f"{segments_formatted}\n\n"
                "ПРАВИЛА:\n"
            )
            if analysis_mode in ("smart", "smart_montage"):
                # Smart storyboard: voiceover plays over everything, clips are visual montage
                max_reel = min(20.0, max(10.0, vo_duration * 2))
                prompt += (
                    f"1. ОЗВУЧКА: {vo_duration:.0f}с — играет поверх ВСЕХ клипов. "
                    "НЕ привязывай сегменты к клипам.\n"
                    f"   Максимум рилса ≈ {max_reel:.0f}с. "
                    "Историю определяет видео, не озвучка.\n\n"
                )

                if story_context:
                    # Pass 2: story already discovered — just select clips
                    prompt += (
                        "2. ИСТОРИЯ УЖЕ НАЙДЕНА (анализ ниже).\n\n"
                        f"=== АНАЛИЗ ВИДЕО ===\n{story_context}\n"
                        "=== КОНЕЦ АНАЛИЗА ===\n\n"
                        "ТВОЯ ЗАДАЧА: выбрать точные клипы, следуя этой истории.\n\n"
                        "3. ПОРЯДОК = ИСТОРИЯ:\n"
                        "   — Расположи клипы СТРОГО в порядке сюжета: "
                        "завязка → развитие → кульминация → результат.\n"
                        "   — НЕ располагай клипы по номерам видео! "
                        "Если кульминация в Видео 4, а подготовка в Видео 5 — "
                        "кульминация идёт ПЕРЕД подготовкой, если так по сюжету.\n\n"
                        "4. ВЫБОР МОМЕНТОВ:\n"
                        "   — Для каждого этапа найди ЛУЧШИЙ момент по всей длине видео.\n"
                        "   — Если в анализе написано, что в видео есть интересный "
                        "момент на определённом таймкоде — бери ИМЕННО его, "
                        "а не начало видео.\n"
                        "   — НЕ включай клип, если единственное что в нём — "
                        "ноги, спины, тротуар, пустой коридор. "
                        "Перечитай анализ: если красивых кадров в видео нет — "
                        "ПРОПУСТИ это видео.\n\n"
                        "5. ЧЕЛОВЕЧЕСКИЕ МОМЕНТЫ — ядро истории. "
                        "НЕ пропускай взаимодействие между людьми.\n\n"
                        "6. video_index ТОЧНО = номерам видео из анализа.\n\n"
                    )
                else:
                    # No story context (fallback) — single-pass mode
                    prompt += (
                        "2. ПРОСМОТРИ ВСЕ ВИДЕО ЦЕЛИКОМ.\n\n"
                        "3. ПОПРОБУЙ СОБРАТЬ ИСТОРИЮ из видео. "
                        "Для каждого этапа найди конкретный момент и вырежи его.\n"
                        "   — НЕ пропускай человеческие моменты.\n"
                        "   — Заканчивай красивым финальным кадром.\n"
                        "   — Если истории нет — креативный монтаж.\n\n"
                    )

                prompt += (
                    "4. КАЧЕСТВО: Никаких скучных кадров.\n\n"
                    "5. matched_voiceover_segment: null для ВСЕХ клипов.\n"
                )
            elif analysis_mode == "smart_narrative":
                # Smart storyboard NARRATIVE: voiceover drives clip selection and order
                prompt += (
                    "1. КОЛИЧЕСТВО BROLL = КОЛИЧЕСТВО СЕГМЕНТОВ: Создай РОВНО столько broll клипов, "
                    "сколько сегментов озвучки. Каждый broll ОБЯЗАН быть привязан к сегменту. "
                    "Лишних broll без матча быть НЕ ДОЛЖНО.\n"
                    "2. MATCHING: Для каждого broll клипа найди подходящий сегмент озвучки по СМЫСЛУ. "
                    "Вырезай конкретный момент из видео, визуально иллюстрирующий фразу сегмента. "
                    "Укажи matched_voiceover_segment и match_reason.\n"
                    "3. ОДИН СЕГМЕНТ = ОДИН BROLL: Не назначай один сегмент на несколько broll клипов.\n"
                    "4. КАЧЕСТВО КАДРА: Каждый клип должен быть осмысленным — "
                    "показывать объект, действие или эмоцию. Избегай скучных кадров.\n"
                    "5. ПОРЯДОК: Следуй порядку сегментов озвучки.\n"
                )
            else:
                # Hybrid (talking_head + voiceover): strict 1:1 matching
                prompt += (
                    "1. КОЛИЧЕСТВО BROLL = КОЛИЧЕСТВО СЕГМЕНТОВ: Создай РОВНО столько broll клипов, "
                    "сколько сегментов озвучки. Каждый broll ОБЯЗАН быть привязан к сегменту. "
                    "Лишних broll без матча быть НЕ ДОЛЖНО.\n"
                    "2. MATCHING: Для каждого broll клипа найди подходящий сегмент озвучки по СМЫСЛУ. "
                    "Если broll показывает пельмени, а сегмент 0 говорит про пельмени — "
                    "укажи matched_voiceover_segment: 0 и match_reason.\n"
                    "3. ОДИН СЕГМЕНТ = ОДИН BROLL: Не назначай один сегмент на несколько broll клипов.\n"
                    "4. ПОРЯДОК КЛИПОВ: На твоё усмотрение. Python автоматически привяжет аудио к каждому broll.\n"
                )

        try:
            logger.info("[Gemini] Analyzing %d videos...", len(gcs_uris))

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

            # Check for safety filter rejection
            if (hasattr(response, "candidates") and response.candidates
                    and hasattr(response.candidates[0], "finish_reason")
                    and str(response.candidates[0].finish_reason).upper() == "SAFETY"):
                raise GeminiSafetyError("Gemini response blocked by safety filter")

            # ── Cost extraction ──
            self._extract_cost(response)

            if response.parsed:
                logger.info("[Gemini] Analysis parsed successfully")
                return response.parsed

            # Fallback: manual JSON parse
            logger.warning("[Gemini] Fallback to manual JSON parsing")
            return VideoAnalysis.model_validate_json(response.text)

        except (ClientError, ServerError, GeminiSafetyError):
            raise  # let @retry handle 429 / 5xx / safety
        except Exception as e:
            logger.error("[Gemini] Error: %s", e)
            return None

    @retry(
        retry=retry_if_exception(
            lambda e: isinstance(e, (ServerError, GeminiSafetyError))
            or (isinstance(e, ClientError) and getattr(e, "code", 0) == 429)
            or (isinstance(e, ClientError) and "SAFETY" in str(e).upper())
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
            logger.error("Gemini client not configured")
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
            logger.info("[Gemini] Analyzing storyboard: %d videos + audio...", num_videos)

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

            # Check for safety filter rejection
            if (hasattr(response, "candidates") and response.candidates
                    and hasattr(response.candidates[0], "finish_reason")
                    and str(response.candidates[0].finish_reason).upper() == "SAFETY"):
                raise GeminiSafetyError("Gemini storyboard response blocked by safety filter")

            # ── Cost extraction ──
            self._extract_cost(response)

            if response.parsed:
                logger.info("[Gemini] Storyboard analysis parsed successfully")
                return response.parsed

            logger.warning("[Gemini] Fallback to manual JSON parsing")
            return StoryboardAnalysis.model_validate_json(response.text)

        except (ClientError, ServerError, GeminiSafetyError):
            raise
        except Exception as e:
            logger.error("[Gemini] Storyboard error: %s", e)
            return None
