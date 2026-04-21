import json
import logging
import re
from pathlib import Path
from anthropic import AsyncAnthropic

from app.config import settings

logger = logging.getLogger(__name__)

# Load system prompt once at module level
_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "claude_system.txt"
SYSTEM_PROMPT = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()

MODEL = "claude-sonnet-4-6"

FORMAT_DESCRIPTIONS = {
    "reels":    "вертикальное видео 60 сек (Reels / TikTok / Shorts)",
    "post":     "текстовый пост для Instagram / ВКонтакте",
    "carousel": "карусель 7–10 слайдов с заголовками и подписями",
    "stories":  "серия вертикальных Stories 5–7 экранов",
}


class ClaudeService:
    def __init__(self, api_key: str):
        self.client = AsyncAnthropic(api_key=api_key)

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    async def _chat(self, user_prompt: str, max_tokens: int = 2048) -> str:
        """Send a single-turn message and return the assistant's text."""
        message = await self.client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text.strip()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_script(self, format: str, idea_text: str) -> str:
        """
        Generate a content script for the given format.

        :param format: One of 'reels', 'post', 'carousel', 'stories'.
        :param idea_text: Raw idea or voice transcription from the doctor.
        :return: Ready-to-use script in Russian.
        """
        fmt_desc = FORMAT_DESCRIPTIONS.get(format, format)
        prompt = (
            f"Формат контента: {fmt_desc}\n\n"
            f"Идея врача:\n{idea_text}\n\n"
            "Напиши готовый сценарий на русском языке для этого формата. "
            "Соблюдай структуру, характерную для формата. "
            "Используй простой, но профессиональный язык. "
            "Добавь хук в начале и призыв к действию в конце."
        )
        return await self._chat(prompt, max_tokens=2048)

    async def refine_script(self, history: list[dict], user_prompt: str) -> str:
        """
        Refine an existing script based on conversational history.
        
        :param history: List of dicts [{"role": "user"|"assistant", "content": str}]
        :param user_prompt: The newest requested edit.
        :return: Updated script text.
        """
        messages = history.copy()
        messages.append({"role": "user", "content": user_prompt})
        
        response = await self.client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        return response.content[0].text.strip()

    async def check_compliance(self, caption: str, visual_risks: list[str]) -> dict:
        """
        Check a caption (and visual risks) for medical compliance.

        :param caption: The text of the post / caption to verify.
        :param visual_risks: List of potential visual compliance risks (may be empty).
        :return: {
            "ok": bool,
            "issues": list[str],   # empty if ok
            "fixed_caption": str   # corrected version, or original if ok
          }
        """
        risks_block = ""
        if visual_risks:
            risks_block = (
                "\n\nВизуальные риски, выявленные на видео:\n"
                + "\n".join(f"- {r}" for r in visual_risks)
            )

        prompt = (
            "Проверь следующий текст на соответствие медицинскому комплаенсу "
            "(российское законодательство о рекламе медицинских услуг, ФЗ-38):\n\n"
            f"«{caption}»"
            f"{risks_block}\n\n"
            "Ответь строго в формате JSON (без markdown-обёртки):\n"
            '{\n'
            '  "ok": true | false,\n'
            '  "issues": ["<issue1>", ...],\n'
            '  "fixed_caption": "<исправленный текст или оригинал если ok>"\n'
            '}'
        )
        raw = await self._chat(prompt, max_tokens=1024)

        # Strip accidental markdown code fences if the model adds them
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE).strip()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: return raw text as a non-ok result so caller never crashes
            result = {
                "ok": False,
                "issues": ["Не удалось разобрать ответ модели: " + raw[:200]],
                "fixed_caption": caption,
            }
        return result

    async def generate_overlays(self, script: str, total_duration: float) -> list[dict]:
        """
        Claude as director: reads the script and generates pop-up overlay
        instructions (emoji + optional short text) with precise timing.
        Each overlay gets a mood that determines its pill background color.
        """
        prompt = (
            f"Ты — креативный режиссёр монтажа Instagram Reels.\n\n"
            f"Вот сценарий видео (длительность {total_duration:.1f} сек):\n\n"
            f"{script}\n\n"
            f"ЗАДАЧА: Придумай 3-5 pop-up элементов (эмодзи или эмодзи + короткий текст до 3 слов), "
            f"которые появляются поверх видео в ключевые моменты.\n\n"
            f"ПРАВИЛА:\n"
            f"- Каждый pop-up длится 2.5-3.5 секунды\n"
            f"- НЕ перекрывай первые 1.5 секунды (хук) и последнюю секунду\n"
            f"- Минимум 3 секунды между pop-ups (они не должны перекрываться!)\n"
            f"- Используй эмодзи как основу: 🤔 💡 ✨ 🧴 💆‍♀️ 🪞 ❤️ 👀 🔥 и т.д.\n"
            f"- Можно добавить максимум 2 коротких слова к эмодзи. Текст должен быть ОЧЕНЬ коротким: '✨ Секрет', '🧴 3 средства', '💡 Факт'\n"
            f"- position: 'top-left', 'top-right', 'center', 'bottom-left', 'bottom-right'\n"
            f"- НЕ ставь в bottom-center — там субтитры\n"
            f"- Чередуй позиции (не ставь все в одно место)\n"
            f"- size: 'small' (акцент), 'medium' (основной), 'large' (wow-момент)\n"
            f"- mood: 'question' (вопрос/сомнение), 'insight' (инсайт/совет), 'positive' (позитив/результат), 'warning' (важно/осторожно), 'default'\n"
            f"- Привязывай mood к контексту: вопрос → question, совет → insight, результат → positive\n\n"
            f"Ответь ТОЛЬКО валидным JSON массивом, без markdown:\n"
            f'[{{"time": 3.0, "duration": 3.0, "content": "🧴 Совет", "position": "top-right", "size": "medium", "mood": "insight"}}]'
        )

        raw = await self._chat(prompt, max_tokens=1024)
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE).strip()

        VALID_MOODS = {"default", "question", "insight", "positive", "warning"}
        VALID_POSITIONS = {"top-left", "top-right", "center", "bottom-left", "bottom-right"}
        VALID_SIZES = {"small", "medium", "large"}

        try:
            overlays = json.loads(raw)
            valid = []
            last_end = 0.0
            for o in overlays:
                t = float(o.get("time", 0))
                d = float(o.get("duration", 3.0))
                d = max(min(d, 3.5), 2.5)  # clamp 2.5–3.5s

                # Skip if out of bounds or overlapping with previous
                if t < 1.5 or t + d > total_duration - 1.0:
                    continue
                if t < last_end + 1.0:  # at least 1s gap between pop-ups
                    continue

                pos = o.get("position", "top-right")
                size = o.get("size", "medium")
                mood = o.get("mood", "default")

                valid.append({
                    "time": t,
                    "duration": d,
                    "content": str(o.get("content", "✨"))[:15],
                    "position": pos if pos in VALID_POSITIONS else "top-right",
                    "size": size if size in VALID_SIZES else "medium",
                    "mood": mood if mood in VALID_MOODS else "default",
                })
                last_end = t + d
            return valid[:5]
        except (json.JSONDecodeError, KeyError, TypeError):
            return []

    async def generate_broll_prompts(
        self,
        script: str,
        clip_candidates: list[dict],
    ) -> list[dict]:
        """
        For each clip candidate, generate an AI image generation prompt.

        :param script: The Russian content script.
        :param clip_candidates: List of dicts from Gemini with keys:
            video_index, start_sec, end_sec, reason (and optionally scene_topic).
        :return: List of dicts:
            {video_index, start_sec, end_sec, broll_keyword}
            broll_keyword contains the AI image prompt
        """
        clips_block = "\n".join(
            f"- Клип {c['video_index']} [{c['start_sec']:.1f}s–{c['end_sec']:.1f}s]: {c.get('reason', '')}"
            for c in clip_candidates
        )
        prompt = (
            "Ты — режиссёр монтажа Instagram Reels для дерматолога.\n\n"
            "Сценарий видео (на русском):\n"
            f"{script}\n\n"
            "Выбранные клипы (таймкоды и описание сцены от AI-анализа):\n"
            f"{clips_block}\n\n"
            "ЗАДАЧА: Для каждого клипа создай текстовый prompt на английском для генерации AI-изображения (sticker overlay).\n\n"
            "ПРАВИЛА для broll_keyword (prompt):\n"
            "- Опиши конкретный предмет или объект, связанный с дерматологией/косметологией\n"
            "- ОБЯЗАТЕЛЬНО добавь в конец каждого промпта: ', isolated object, sticker style, no background, white background'\n"
            "- Примеры ХОРОШИХ промптов:\n"
            "  'skincare serum bottle with dropper, isolated object, sticker style, no background, white background'\n"
            "  'hyaluronic acid syringe, isolated object, sticker style, no background, white background'\n"
            "  'face cream jar with lid open, isolated object, sticker style, no background, white background'\n"
            "  'dermatologist examining skin with magnifying glass, isolated object, sticker style, no background, white background'\n"
            "- Используй медицинские термины для точности: serum, dermal filler, chemical peel, LED therapy, etc.\n"
            "- Если не уверен — используй универсальный фоллбэк: 'skincare product, isolated object, sticker style, no background, white background'\n\n"
            "ВАЖНО: Сгенерируй prompt для КАЖДОЙ сцены из списка, не пропускай ни одну.\n\n"
            "Ответь ТОЛЬКО валидным JSON массивом, без markdown:\n"
            '[{"video_index": 1, "start_sec": 0.0, "end_sec": 5.0, '
            '"broll_keyword": "hyaluronic acid syringe, isolated object, sticker style, no background, white background"}]'
        )

        raw = await self._chat(prompt, max_tokens=1024)
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE).strip()

        try:
            items = json.loads(raw)
            result = []
            for item, clip in zip(items, clip_candidates):
                prompt_text = str(item.get("broll_keyword", "skincare product"))
                # Ensure the suffix is present
                if "isolated object, sticker style" not in prompt_text:
                    prompt_text += ", isolated object, sticker style, no background, white background"
                result.append({
                    "video_index": clip["video_index"],
                    "start_sec": clip["start_sec"],
                    "end_sec": clip["end_sec"],
                    "broll_keyword": prompt_text[:200],  # Increased limit for longer prompts
                    "overlay_type": "sticker",
                })
            return result
        except (json.JSONDecodeError, KeyError, TypeError):
            return [
                {
                    "video_index": c["video_index"],
                    "start_sec": c["start_sec"],
                    "end_sec": c["end_sec"],
                    "broll_keyword": "skincare product, isolated object, sticker style, no background, white background",
                    "overlay_type": "sticker",
                }
                for c in clip_candidates
            ]

    async def suggest_formats(self, idea_text: str) -> list[str]:
        """
        Suggest the three best content formats for a given idea.

        :param idea_text: Raw idea or voice transcription from the doctor.
        :return: List of exactly 3 format strings from
                 ['reels', 'post', 'carousel', 'stories'].
        """
        available = ", ".join(FORMAT_DESCRIPTIONS.keys())
        prompt = (
            f"Доступные форматы контента: {available}\n\n"
            f"Идея врача:\n{idea_text}\n\n"
            "Выбери три наиболее подходящих формата для этой идеи и объясни кратко "
            "почему каждый подходит.\n\n"
            "Ответь строго в формате JSON (без markdown-обёртки):\n"
            '[\n'
            '  {"format": "<название>", "reason": "<одно предложение>"},\n'
            '  {"format": "<название>", "reason": "<одно предложение>"},\n'
            '  {"format": "<название>", "reason": "<одно предложение>"}\n'
            ']'
        )
        raw = await self._chat(prompt, max_tokens=512)

        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE).strip()

        try:
            items = json.loads(raw)
            # Return just the format names; caller can inspect reasons if needed
            return [item["format"] for item in items[:3]]
        except (json.JSONDecodeError, KeyError):
            # Graceful fallback to all formats
            return list(FORMAT_DESCRIPTIONS.keys())[:3]


# ── Standalone feedback classifier (Haiku — cheapest model) ──────────────


async def classify_feedback(feedback_text: str, api_key: str) -> dict:
    """
    Classify user feedback using Claude Haiku into pipeline routing categories.
    Returns: {"gemini_instruction": str|None, "director_instruction": str|None}
    Per D-21: ~$0.001/call using Haiku.
    """
    client = AsyncAnthropic(api_key=api_key)

    system_prompt = (
        "Ты — классификатор обратной связи для видео-продакшн пайплайна.\n"
        "Пользователь оставляет замечание на готовое видео. "
        "Определи, к чему относится замечание:\n\n"
        "1. gemini_instruction — выбор кадров, длительность клипов, порядок сцен, "
        "какие моменты показать, слишком много/мало крупных планов, "
        "скучный выбор, однообразные кадры.\n"
        "2. director_instruction — переходы, эффекты, стикеры, стиль монтажа, "
        "цвета субтитров, скорость переходов, визуальный стиль.\n\n"
        "Замечание может относиться к ОБЕИМ категориям одновременно.\n"
        "Для каждой категории верни краткую инструкцию на русском "
        "(что именно изменить) или null если не относится.\n\n"
        "Отвечай СТРОГО в JSON формате:\n"
        '{"gemini_instruction": "строка или null", "director_instruction": "строка или null"}'
    )

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=system_prompt,
        messages=[{"role": "user", "content": feedback_text}],
    )

    try:
        raw = response.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0].strip()
        result = json.loads(raw)
        return {
            "gemini_instruction": result.get("gemini_instruction"),
            "director_instruction": result.get("director_instruction"),
        }
    except (json.JSONDecodeError, IndexError, KeyError) as e:
        logger.warning("Failed to parse Haiku classification: %s | raw: %s", e, response.content[0].text[:200])
        return {"gemini_instruction": feedback_text, "director_instruction": None}


# ── Phase 07: Instagram Carousel brief → slides JSON ──────────────────────────

_CAROUSEL_MODEL = "claude-sonnet-4-6"
_carousel_client: AsyncAnthropic | None = None


def _get_carousel_client() -> AsyncAnthropic:
    global _carousel_client
    if _carousel_client is None:
        _carousel_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _carousel_client


async def _chat(prompt: str, system: str | None = None, max_tokens: int = 2048) -> str:
    """Module-level single-turn chat using carousel client (reused by carousel functions)."""
    client = _get_carousel_client()
    kwargs: dict = {
        "model": _CAROUSEL_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system is not None:
        kwargs["system"] = system
    message = await client.messages.create(**kwargs)
    return message.content[0].text.strip()


CAROUSEL_SYSTEM = (
    "Ты помогаешь Ромине (врач-косметолог) делать Instagram-карусели на русском. "
    "Возвращаешь ТОЛЬКО JSON без markdown-обёртки."
)

CAROUSEL_PROMPT = """Из бриф-сообщения Ромины сгенерируй JSON со структурой:

{{
  "slides": [
    {{"order": 1, "text_title": "...", "text_body": "..."}},
    ...
  ],
  "caption_telegram": "...",
  "caption_instagram": "..."
}}

Правила:
- Количество слайдов: {slides_rule}
- Заголовок слайда (text_title): ≤60 символов (БЕЗ emoji — PIL не рендерит)
- Подпись слайда (text_body): ≤180 символов (БЕЗ emoji)
- caption_telegram: ≤1024 символа (короткое превью для Telegram)
- caption_instagram: ≤2200 символов (полный текст, emoji и хэштеги ОК)
- Русский язык, клиентский тон Ромины
- Не выдумывай медицинских фактов; если бриф короткий — формулируй нейтрально

БРИФ: {brief}
"""

CAROUSEL_CAPTIONS_PROMPT = """По готовым слайдам Instagram-карусели сгенерируй две подписи.
Верни ТОЛЬКО JSON:

{{
  "caption_telegram": "...",
  "caption_instagram": "..."
}}

Правила:
- caption_telegram: ≤1024 символа, короткое превью для Telegram
- caption_instagram: ≤2200 символов, полный текст, emoji и хэштеги ОК, клиентский тон Ромины
- Русский язык

СЛАЙДЫ:
{slides_block}
"""

CAROUSEL_REGEN_PROMPT = """Пересобери ТОЛЬКО слайд #{slide_order} для Instagram-карусели
на русском. Бриф и остальные слайды даны для контекста. Верни JSON одного слайда:

{{"order": {slide_order}, "text_title": "...", "text_body": "..."}}

Правила:
- text_title: ≤60 символов, БЕЗ emoji
- text_body: ≤180 символов, БЕЗ emoji
- Не повторяй формулировки других слайдов
{edit_hint_line}

БРИФ: {brief}

СОСЕДНИЕ СЛАЙДЫ:
{sibling_block}
"""


def _strip_json_fence(raw: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", raw.strip(), flags=re.MULTILINE)


def _validate_carousel_payload(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Claude вернул не JSON-объект")
    slides = payload.get("slides")
    if not isinstance(slides, list) or not (3 <= len(slides) <= 20):
        raise ValueError(
            f"Ожидается 3-20 слайдов, получено "
            f"{len(slides) if isinstance(slides, list) else 'не список'}"
        )
    seen_orders: set[int] = set()
    for i, s in enumerate(slides, start=1):
        if not isinstance(s, dict):
            raise ValueError(f"Слайд #{i} не dict")
        order = s.get("order")
        title = s.get("text_title", "")
        body = s.get("text_body", "")
        if not isinstance(order, int):
            raise ValueError(f"Слайд #{i}: order должен быть int")
        if order in seen_orders:
            raise ValueError(f"Слайд #{i}: повторяющийся order={order}")
        seen_orders.add(order)
        if not isinstance(title, str) or len(title) > 60:
            raise ValueError(f"Слайд #{i}: text_title >60 символов или не строка")
        if not isinstance(body, str) or len(body) > 180:
            raise ValueError(f"Слайд #{i}: text_body >180 символов или не строка")
    cap_tg = payload.get("caption_telegram", "")
    cap_ig = payload.get("caption_instagram", "")
    if not isinstance(cap_tg, str) or len(cap_tg) > 1024:
        raise ValueError(f"caption_telegram >1024 символов ({len(cap_tg)})")
    if not isinstance(cap_ig, str) or len(cap_ig) > 2200:
        raise ValueError(f"caption_instagram >2200 символов ({len(cap_ig)})")


async def generate_carousel_slides(brief: str, target_slides: int | None = None) -> dict:
    """Claude brief → carousel_slides JSON. Raises ValueError on malformed/invalid.

    target_slides: if provided, Claude generates EXACTLY this many slides (used when
    the user has already uploaded media and slide count must match file count).
    When None, Claude picks a logical count between 3 and 20.
    """
    if target_slides is not None:
        if not (3 <= target_slides <= 20):
            raise ValueError(f"target_slides должен быть от 3 до 20, получено {target_slides}")
        slides_rule = f"РОВНО {target_slides} слайдов, ни больше, ни меньше"
    else:
        slides_rule = "от 3 до 20, сколько логично раскрывает тему"

    prompt = CAROUSEL_PROMPT.format(brief=brief.strip(), slides_rule=slides_rule)
    raw = await _chat(prompt, system=CAROUSEL_SYSTEM, max_tokens=8192)
    cleaned = _strip_json_fence(raw)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("Claude carousel JSON parse failed: %s\nRaw: %s", e, cleaned[:500])
        raise ValueError("Claude вернул невалидный JSON — попробуй упростить бриф") from e
    _validate_carousel_payload(payload)
    if target_slides is not None and len(payload["slides"]) != target_slides:
        raise ValueError(
            f"Claude вернул {len(payload['slides'])} слайдов, "
            f"ожидалось {target_slides} — попробуй ещё раз"
        )
    return payload


async def generate_captions_for_slides(slides: list[dict]) -> dict:
    """Generate only caption_telegram + caption_instagram for already-authored slides.

    Used in ready-texts mode where Romina supplies slide texts directly and we
    still need two captions.
    """
    slides_block = "\n".join(
        f"#{s.get('order', i+1)}: {s.get('text_title', '')} — {s.get('text_body', '')}"
        for i, s in enumerate(slides)
    )
    prompt = CAROUSEL_CAPTIONS_PROMPT.format(slides_block=slides_block)
    raw = await _chat(prompt, system=CAROUSEL_SYSTEM, max_tokens=4096)
    cleaned = _strip_json_fence(raw)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("Claude captions JSON parse failed: %s\nRaw: %s", e, cleaned[:500])
        raise ValueError("Claude вернул невалидный JSON для подписей") from e
    cap_tg = payload.get("caption_telegram", "")
    cap_ig = payload.get("caption_instagram", "")
    if not isinstance(cap_tg, str):
        cap_tg = ""
    if not isinstance(cap_ig, str):
        cap_ig = ""
    return {
        "caption_telegram": cap_tg[:1024],
        "caption_instagram": cap_ig[:2200],
    }


async def regenerate_single_slide(
    brief: str,
    slides: list[dict],
    slide_index: int,
    edit_hint: str | None = None,
) -> dict:
    """Regenerate a single slide by index. Returns replacement slide dict."""
    if not (0 <= slide_index < len(slides)):
        raise ValueError(f"slide_index={slide_index} вне диапазона")
    target = slides[slide_index]
    siblings = [s for i, s in enumerate(slides) if i != slide_index]
    sibling_block = "\n".join(
        f"#{s['order']}: {s['text_title']} — {s['text_body']}" for s in siblings
    )
    edit_hint_line = f"- УКАЗАНИЕ ОТ РОМИНЫ: {edit_hint}" if edit_hint else ""
    prompt = CAROUSEL_REGEN_PROMPT.format(
        slide_order=target["order"],
        brief=brief.strip(),
        sibling_block=sibling_block,
        edit_hint_line=edit_hint_line,
    )
    raw = await _chat(prompt, system=CAROUSEL_SYSTEM, max_tokens=512)
    cleaned = _strip_json_fence(raw)
    try:
        new_slide = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError("Claude вернул невалидный JSON для пересборки слайда") from e
    if not isinstance(new_slide, dict):
        raise ValueError("Пересобранный слайд не dict")
    if new_slide.get("order") != target["order"]:
        raise ValueError(
            f"Claude вернул слайд с order={new_slide.get('order')}, "
            f"ожидался {target['order']}"
        )
    title = new_slide.get("text_title", "")
    body = new_slide.get("text_body", "")
    if not isinstance(title, str) or len(title) > 60:
        raise ValueError(f"text_title >60 символов ({len(title)})")
    if not isinstance(body, str) or len(body) > 180:
        raise ValueError(f"text_body >180 символов ({len(body)})")
    return new_slide
