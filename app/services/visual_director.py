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

Позицию и размер код определит сам.

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
      "sticker_exit_animation": "fade"
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

    for ov in raw_overlays:
        if not isinstance(ov, dict):
            continue
        if ov.get("type") != "ai_image":
            continue
        prompt = ov.get("image_prompt", "")
        if not prompt:
            continue
        start = ov.get("start_second")
        end = ov.get("end_second")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        start, end = int(start), int(end)
        # Rule: start < end, min 4s duration
        if end - start < 4:
            continue
        # Rule: not in first/last 2s
        if start < 2 or end > total_duration - 2:
            continue
        # Rule: max 3 total
        if len(clean_overlays) >= MAX_STICKERS:
            continue
        # Rule: no overlapping
        overlap = False
        for rs, re in used_ranges:
            if start < re and end > rs:
                overlap = True
                break
        if overlap:
            continue

        used_ranges.append((start, end))
        enter_anim = ov.get("sticker_enter_animation", "fade")
        exit_anim = ov.get("sticker_exit_animation", "fade")
        if enter_anim not in ALLOWED_STICKER_ANIMATIONS:
            enter_anim = "fade"
        if exit_anim not in ALLOWED_STICKER_ANIMATIONS:
            exit_anim = "fade"
        clean_overlays.append({
            "type": "ai_image",
            "image_prompt": prompt[:200],
            "start_second": start,
            "end_second": end,
            "sticker_enter_animation": enter_anim,
            "sticker_exit_animation": exit_anim,
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

    # Build clip descriptions block for storyboard mode
    if clip_descriptions and render_mode == "storyboard":
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
