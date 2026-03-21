"""
Visual Director — Claude picks transitions + sticker overlays for reels.

Used in both storyboard and talking_head modes.
Single Claude API call produces a visual blueprint for the entire reel.
"""

import json
import logging
import re

from anthropic import AsyncAnthropic

from app.config import settings

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

ALLOWED_TRANSITION_TYPES = {
    "fade", "slide", "wipe", "circular-wipe", "color-wipe",
    "film-roll", "squash", "rotate-slide", "shift",
}

DIRECTION_TYPES = {
    "slide", "film-roll", "squash", "rotate-slide", "shift", "color-wipe",
}

ALLOWED_DIRECTIONS = {"left", "right", "up", "down"}

MAX_STICKERS = 3

ALLOWED_STICKER_ANIMATIONS = {
    "fade", "wipe", "slide", "flip", "bounce",
    "circular-wipe", "film-roll", "shift", "squash", "rotate-slide",
}

SYSTEM_PROMPT = """\
You are a JSON-only API. Respond with a single JSON object. No text before or after. No analysis, no markdown, no explanation. First character of your response must be {{

Ты — визуальный режиссёр Instagram Reels для косметолога-блогера. Аудитория — женщины 25-45.

Тебе дан сценарий, клипы с длительностями и режим.
Создай визуальный план для рилса.

РЕЖИМ: {render_mode}
- "talking_head": спикер говорит в камеру, звук из видео. \
Transitions СЪЕДАЮТ часть звука (overlap) — используй только короткие \
и только на длинных клипах (>3с). Sticker overlays добавляют динамику.
- "storyboard": voiceover отдельно, видео без звука. \
Transitions свободно по настроению. Sticker overlays по теме сценария.

=== ИНСТРУМЕНТ 1: TRANSITIONS ===

Переход между клипами на одном видео-треке.

Доступные типы: fade, slide, wipe, circular-wipe, color-wipe, film-roll, squash, rotate-slide, shift

Запрещены: scale (артефакты рендеринга), spin, flip (не подходят бренду)

Для slide/film-roll/squash/rotate-slide/shift/color-wipe указывай direction: "left"|"right"|"up"|"down"

НЕ указывай duration — вычисляется в коде.

=== ИНСТРУМЕНТ 2: STICKER OVERLAYS ===

Маленькая AI-сгенерированная картинка поверх видео. \
Круглый стикер в углу экрана, тематически связанный со сценарием. \
Добавляет визуальный интерес без отвлечения от основного контента.

Стикеры привязаны к СЦЕНАРИЮ, не к клипам. \
start_second и end_second — время на финальном таймлайне рилса. \
Стикер живёт пока тема актуальна. \
Общий таймлайн = сумма всех clip durations (указан ниже).

Если render_mode = "talking_head":
Спикер в камеру — картинка всегда одинаковая. \
Стикеры НУЖНЫ для визуального разнообразия. \
Показывай объекты связанные с темой разговора.

Если render_mode = "storyboard":
СТИКЕРЫ ПОЧТИ НИКОГДА НЕ НУЖНЫ. \
Видеоряд уже рассказывает историю.

Ставь стикер ТОЛЬКО если ОБА условия:
1. Речь про конкретный объект/тему
2. Видео показывает что-то СОВЕРШЕННО ДРУГОЕ

Примеры когда НЕ ставить:
- Речь про паспорт, видео = руки с паспортом → НЕТ
- Речь про кофе, видео = кафе → НЕТ
- Речь про переезд, видео = чемодан → НЕТ
- Речь про новую жизнь, видео = улица города → НЕТ

Примеры когда ставить:
- Речь про Сахару, видео = женщина на улице → ДА
- Речь про детство в деревне, видео = крупный план лица → ДА
- Речь про океан, видео = офисный интерьер → ДА

Стикер нужен только когда разрыв между видео \
и речью ЭКЗОТИЧЕСКИЙ — зритель слышит что-то \
яркое и необычное, но видит обыденное. \
Стикер привлекает внимание к тому что нельзя \
показать видеорядом.

Если видео хоть отдалённо связано с речью — НЕТ. \
Максимум 1 стикер на весь storyboard рилс. \
В большинстве случаев — 0 стикеров. \
Если сомневаешься — НЕ СТАВЬ.
{clip_descriptions_block}

Указывай:
- image_prompt: описание объекта на АНГЛИЙСКОМ. \
ВСЕГДА добавляй "isolated object, sticker style, no background" в конец промпта.
- start_second: целое число, начало стикера на таймлайне.
- end_second: целое число, конец стикера на таймлайне.

Примеры:
"A coffee cup with steam, isolated object, sticker style, no background"
"A passport with boarding pass, isolated object, sticker style, no background"
"A small cosmetic jar, isolated object, sticker style, no background"

=== ПОЗИЦИЯ СТИКЕРОВ ===

Для каждого стикера укажи x, y, width, height в процентах.

ПРИОРИТЕТ КОНТЕКСТА: если доступны clip_descriptions \
(что визуально в кадре каждого клипа) — ориентируйся на НИХ. \
scenario_text может быть кратким и недостаточным.

Правила:
- clip_description содержит "лицо"/"спикер"/"говорит в камеру" → \
лицо в зоне y: 15-50%. Стикер: y: 55-70%. НИКОГДА на лицо.
- clip_description содержит "еда"/"пейзаж"/"детали"/"руки" (нет лица) → \
стикер свободнее: y: 15-70%.
- Нет clip_descriptions → ориентируйся на render_mode: \
talking_head → предполагай лицо вверху, стикер y: 55-70%. \
storyboard → свободнее, y: 15-70%.
- Субтитры на y: 78%. Стикер не ниже y: 72%.
- x: чередуй стороны.
- Размер: width 15-25%, height 15-25%.
- Несколько стикеров — разноси по экрану.

=== КРЕАТИВНЫЕ ПРАВИЛА СТИКЕРОВ ===

Стикер — это НЕ иллюстрация к кадру. Стикер визуализирует \
ОЩУЩЕНИЕ зрителя от того что он видит.

Принцип: посмотри на кадр глазами зрителя. Что он думает? \
Что чувствует? Какая ассоциация возникает? ЭТО и рисуй. \
Удиви — покажи реакцию зрителя, не содержание кадра. \
Как будто зритель шлёт подруге стикер в чат со словами \
"ну ты видела это?!"

ЗАПРЕЩЕНО: рисовать то что уже в кадре или предмет из той же \
категории. В кадре лапша — рисуешь стикер лапши? Зритель и так \
её видит! Покажи то чего он НЕ видит но чувствует.

Калибровочные примеры (уровень креативности который нужен):

- Острая еда крупным планом, спикер реагирует → \
"A tiny cartoon dragon breathing fire from its nostrils, \
isolated object, sticker style, no background"

- Аэропорт ранним утром, уставший спикер → \
"A sleepy owl clutching a tiny coffee cup, \
isolated object, sticker style, no background"

- Красивый закат над городом → \
"A vintage instant camera with a photo sliding out, \
isolated object, sticker style, no background"

- Результат косметической процедуры → \
"A sparkling diamond with rainbow light reflections, \
isolated object, sticker style, no background"

- Долгая очередь в бюрократическом офисе → \
"A tiny snail wearing round reading glasses, \
isolated object, sticker style, no background"

Заметь паттерн: дракон для остроты (а не перец!), \
сова для усталости (а не подушка!), \
улитка для медленности (а не часы!). \
Стикер — это ХАРАКТЕР и МЕТАФОРА. Персонаж с настроением, \
объект с неожиданной ассоциацией, предмет который рассказывает историю.

Где уместно — добавляй юмор и абсурд. Смешной стикер запоминается. \
Но не форсируй: красивый момент → красивый стикер (бриллиант, камера), \
смешной момент → смешной стикер (улитка в очках, сова с кофе). \
clean настроение → стикер сдержанный и элегантный. Никакого юмора \
и абсурда. Примеры для clean: блеск звёздочек, кристалл, золотая \
капля, жемчужина.

Рисуй маленькие детальные объекты с характером. Не плоские иконки, \
не эмодзи, не логотипы. Живые, тёплые, с personality.

Промпт стикера ВСЕГДА заканчивается на \
"isolated object, sticker style, no background".

Если clip_description слишком общий ("женщина говорит", \
"человек в кадре") — ориентируйся на scenario_text. \
Если и scenario_text краткий — ориентируйся на mood: \
dynamic = яркий и смешной, soft = тёплый и нежный, \
clean = элегантный.

=== АНИМАЦИИ СТИКЕРОВ ===

Для каждого стикера выбери тип анимации появления и исчезновения.

Доступные типы:
- fade: плавное появление/исчезновение (самый мягкий)
- wipe: шторка (мягкий, элегантный)
- slide: выезд снизу (плавный, направленный)
- flip: переворот 3D (яркий, эффектный)
- bounce: подпрыгивание (весёлый, энергичный)
- circular-wipe: круговая шторка (эффектный reveal)
- film-roll: плёночный ролл (ретро, стильный)
- shift: графичный сдвиг (резкий, современный)
- squash: сжатие-растяжение (мультяшный, динамичный)
- rotate-slide: поворот со сдвигом (сложный, впечатляющий)

Гайд по mood:
- clean: ТОЛЬКО fade
- soft: fade, wipe, slide
- dynamic: ВСЕ типы — чем разнообразнее тем лучше. \
Не повторяй одну и ту же анимацию дважды!
- mixed: по секциям — спокойные секции = soft палитра, \
энергичные секции = dynamic палитра

Enter и exit МОГУТ быть разными типами (например flip enter + fade exit). \
Если несколько стикеров — используй разные комбинации для каждого!

=== РЕЖИМЫ НАСТРОЕНИЯ ===

- "clean": только hard cuts, максимум 1 стикер. \
Для серьёзного, грустного, медицинского контента.
- "soft": fade/wipe transitions + 1-2 стикера. \
Для личного, эмоционального контента.
- "dynamic": разнообразные transitions + 2-3 стикера. \
Для lifestyle, советов, мотивации.
- "mixed": разные стили для разных частей рилса. \
Типично: hook=dynamic, story=soft, closing=soft/clean.

=== ПРАВИЛА ===

Transitions:
1. Клип index 0 — ВСЕГДА transition: null (первый клип).
2. Не повторять один тип transition два раза подряд.
3. Клип < 2.5с → transition: null.
4. talking_head + клип < 3с → transition: null.
5. Медицинский контент → "clean".
6. Темы болезнь/потеря/боль/одиночество → "clean".

Sticker overlays:
7. НЕ в первые 2с и НЕ в последние 2с рилса.
8. Стикеры НЕ должны пересекаться по времени.
9. Минимальная длительность стикера: 4с (end_second - start_second >= 4).
10. image_prompt СВЯЗАН с темой сценария в этом месте.
11. Максимум 3 стикера на весь рилс.
12. Если сомневаешься — не ставь. Меньше лучше.

=== ФОРМАТ ОТВЕТА (JSON) ===

{{
  "overall_style": "clean|soft|dynamic|mixed",
  "reasoning": "одно предложение почему выбран этот стиль",
  "clips": [
    {{
      "index": 0,
      "transition": null
    }},
    {{
      "index": 1,
      "transition": {{"type": "fade"}}
    }},
    {{
      "index": 2,
      "transition": {{"type": "slide", "direction": "left"}}
    }}
  ],
  "overlays": [
    {{
      "type": "ai_image",
      "image_prompt": "A skincare serum bottle, isolated object, sticker style, no background",
      "start_second": 5,
      "end_second": 14,
      "sticker_enter_animation": "wipe",
      "sticker_exit_animation": "fade",
      "x": "75%",
      "y": "60%",
      "width": "22%",
      "height": "22%"
    }}
  ]
}}

Количество объектов в clips ДОЛЖНО равняться количеству входных клипов.
overlays — отдельный массив, НЕ внутри clips. Может быть пустым []."""


def _make_fallback(num_clips: int) -> dict:
    return {
        "overall_style": "clean",
        "clips": [
            {"index": i, "transition": None}
            for i in range(num_clips)
        ],
        "overlays": [],
    }


def _parse_pct(val: str | int | float, lo: int, hi: int, default: str) -> str:
    """Parse '75%' → 75, clamp to [lo, hi], return as 'N%'. Fallback to default."""
    try:
        n = int(str(val).replace("%", "").strip())
        if lo <= n <= hi:
            return f"{n}%"
    except (ValueError, TypeError):
        pass
    return default


def _validate_blueprint(
    blueprint: dict,
    num_clips: int,
    clip_durations: list[float],
) -> dict | None:
    """Validate blueprint. Returns cleaned blueprint or None if invalid."""
    clips = blueprint.get("clips")
    if not isinstance(clips, list) or len(clips) != num_clips:
        return None

    if num_clips > 0 and clips[0].get("transition") is not None:
        return None

    total_duration = sum(clip_durations)

    # ── Validate clips (transitions only) ──
    cleaned_clips = []
    for i, clip in enumerate(clips):
        idx = clip.get("index", i)

        t = clip.get("transition")
        clean_t = None
        if t is not None and isinstance(t, dict) and t.get("type") in ALLOWED_TRANSITION_TYPES:
            clean_t = {"type": t["type"]}
            if t["type"] in DIRECTION_TYPES:
                d = t.get("direction")
                if d in ALLOWED_DIRECTIONS:
                    clean_t["direction"] = d

        cleaned_clips.append({
            "index": idx,
            "transition": clean_t,
        })

    # ── Validate overlays (top-level, timeline-based) ──
    raw_overlays = blueprint.get("overlays") or []
    clean_overlays = []
    used_ranges: list[tuple[int, int]] = []

    for ov_i, ov in enumerate(raw_overlays):
        if not isinstance(ov, dict):
            logger.debug("Overlay %d rejected: not a dict (%s)", ov_i, type(ov).__name__)
            continue
        if ov.get("type") != "ai_image":
            logger.debug("Overlay %d rejected: type=%r (expected 'ai_image')", ov_i, ov.get("type"))
            continue
        prompt = ov.get("image_prompt", "")
        if not prompt:
            logger.debug("Overlay %d rejected: empty image_prompt", ov_i)
            continue
        start = ov.get("start_second")
        end = ov.get("end_second")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            logger.debug("Overlay %d rejected: bad timing types start=%r end=%r", ov_i, start, end)
            continue
        start, end = int(start), int(end)
        # Rule: start < end, min 4s duration
        if end - start < 4:
            logger.debug("Overlay %d rejected: duration %ds < 4s (start=%d end=%d)", ov_i, end - start, start, end)
            continue
        # Rule: not in first/last safe zone (dynamic based on duration)
        safe_margin = 1 if total_duration < 10 else 2
        if start < safe_margin or end > total_duration - safe_margin:
            logger.debug("Overlay %d rejected: out of safe zone (start=%d end=%d total=%.1f margin=%d)", ov_i, start, end, total_duration, safe_margin)
            continue
        # Rule: max 3 total
        if len(clean_overlays) >= MAX_STICKERS:
            logger.debug("Overlay %d rejected: max stickers (%d) reached", ov_i, MAX_STICKERS)
            continue
        # Rule: no overlapping
        overlap = False
        for rs, re in used_ranges:
            if start < re and end > rs:
                overlap = True
                break
        if overlap:
            logger.debug("Overlay %d rejected: overlaps with existing range", ov_i)
            continue
        logger.debug("Overlay %d accepted: prompt=%r start=%d end=%d", ov_i, prompt[:50], start, end)

        used_ranges.append((start, end))
        enter_anim = ov.get("sticker_enter_animation", "fade")
        exit_anim = ov.get("sticker_exit_animation", "fade")
        if enter_anim not in ALLOWED_STICKER_ANIMATIONS:
            enter_anim = "fade"
        if exit_anim not in ALLOWED_STICKER_ANIMATIONS:
            exit_anim = "fade"
        # Validate position fields
        idx = len(clean_overlays)
        default_x = "75%" if idx % 2 == 0 else "25%"
        x_val = _parse_pct(ov.get("x", default_x), 15, 85, default_x)
        y_val = _parse_pct(ov.get("y", "60%"), 10, 90, "60%")
        w_val = _parse_pct(ov.get("width", "25%"), 10, 40, "25%")
        h_val = _parse_pct(ov.get("height", "25%"), 10, 40, "25%")

        clean_overlays.append({
            "type": "ai_image",
            "image_prompt": prompt[:200],
            "start_second": start,
            "end_second": end,
            "sticker_enter_animation": enter_anim,
            "sticker_exit_animation": exit_anim,
            "x": x_val,
            "y": y_val,
            "width": w_val,
            "height": h_val,
        })

    return {
        "overall_style": blueprint.get("overall_style", "clean"),
        "reasoning": blueprint.get("reasoning", ""),
        "clips": cleaned_clips,
        "overlays": clean_overlays,
    }


async def get_visual_blueprint(
    scenario_text: str,
    clips: list[dict],
    render_mode: str,
    clip_descriptions: list[str] | None = None,
) -> dict:
    """
    Call Claude to choose transitions + sticker overlays.

    :param scenario_text: Script/scenario text (Russian). Can be empty for talking_head.
    :param clips: [{"index": 0, "duration": 4.1}, ...]
    :param render_mode: "talking_head" or "storyboard"
    :param clip_descriptions: Optional list of what's visually on screen per clip.
    :return: Blueprint dict with overall_style, reasoning, clips[].transition, overlays[]
    """
    num_clips = len(clips)
    if num_clips == 0:
        return _make_fallback(0)

    clip_durations = [c["duration"] for c in clips]

    # Build clip descriptions block (both modes)
    if clip_descriptions:
        desc_lines = "\n".join(
            f"- Клип {i}: {desc}" for i, desc in enumerate(clip_descriptions)
        )
        desc_block = f"\nВИДЕОРЯД (что на экране в каждом клипе):\n{desc_lines}"
    else:
        desc_block = ""

    prompt_text = SYSTEM_PROMPT.replace("{render_mode}", render_mode)
    prompt_text = prompt_text.replace("{clip_descriptions_block}", desc_block)

    clips_block = "\n".join(
        f"- Клип {c['index']}: {c['duration']:.1f} сек"
        for c in clips
    )

    total_duration = sum(clip_durations)
    scenario_part = f"Сценарий:\n{scenario_text}\n\n" if scenario_text else ""
    user_prompt = (
        f"{scenario_part}"
        f"Режим: {render_mode}\n\n"
        f"Клипы ({num_clips} шт.):\n{clips_block}\n\n"
        f"Общий таймлайн: {total_duration:.0f} сек"
    )

    try:
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=30.0)
        message = await client.messages.create(
            model=MODEL,
            max_tokens=1024,
            temperature=0,
            system=prompt_text,
            messages=[{"role": "user", "content": user_prompt}],
        )
        response_text = message.content[0].text
        logger.debug(f"Visual director raw: {response_text[:500]}")

        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        # Extract JSON object if surrounded by text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

        blueprint = json.loads(text)
        validated = _validate_blueprint(blueprint, num_clips, clip_durations)
        if validated is None:
            logger.warning("Visual blueprint validation failed, using fallback")
            return _make_fallback(num_clips)

        return validated

    except Exception as e:
        logger.warning("Visual director failed (%s), using fallback", e)
        return _make_fallback(num_clips)
