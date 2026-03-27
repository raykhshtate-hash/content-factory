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


ALLOWED_TRANSITION_TYPES = {
    "fade", "slide", "wipe", "circular-wipe", "color-wipe",
    "film-roll", "squash", "rotate-slide", "shift",
}

DIRECTION_TYPES = {
    "slide", "film-roll", "squash", "rotate-slide", "shift", "color-wipe",
}

ALLOWED_DIRECTIONS = {"left", "right", "up", "down"}

DEFAULT_MAX_STICKERS = 2
SAFETY_MAX_STICKERS = 5

ALLOWED_FONTS = {"Montserrat", "Poppins", "Raleway", "Comfortaa"}

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

Стикер УСИЛИВАЕТ момент. Он берёт чувство из кадра и делает его \
ярче, кинематографичнее, крупнее чем жизнь.

Принцип: представь самый ЯРКИЙ образ который мгновенно передаёт \
суть момента. Как кадр из фильма, который все помнят. \
Не описание — а усиление.

### ТРИ ЗОНЫ (КРИТИЧНО)

СКУЧНОЕ (отвергай): банальные иконки без характера. \
Часы, лампочка, палец вверх, вопросительный знак, сердечко, \
стрелка, галочка. Это эмодзи, не стикеры.

ЯРКОЕ (цель): конкретный, узнаваемый образ с характером и \
эмоцией. Мгновенно понятно к чему. Может быть метафора, \
гипербола, культурная отсылка — но всегда СВЯЗАН с темой.

ОТОРВАННОЕ (отвергай): абстрактные символы без связи с темой. \
Если зритель не поймёт почему этот стикер здесь — он плохой. \
Хрустальный шар для темы зубов? Нет. Зубная фея? Да.

### АЛГОРИТМ
1. Пойми СУТЬ момента (не отдельное слово — весь контекст)
2. Найди самый ЯРКИЙ образ который усиливает эту суть
3. Проверь: зритель за 1 секунду поймёт связь? Да → бери. Нет → ищи другой.
4. НЕ рисуй то что уже видно в кадре (дублирование)
5. Чередуй типы: метафора, гипербола, культура, персонаж, ирония

ПРИМЕРЫ:
- В Дубае жарко и всегда хочется пить → \
"A tiny desert oasis with a single palm tree and sparkling water, \
isolated object, sticker style, no background" (гипербола места)
- Зубы могут рассказать о человеке → \
"A sparkling tooth wearing a tiny golden crown, \
isolated object, sticker style, no background" (персонификация)
- Острая еда → \
"A fire-breathing dragon with smoke from nostrils, \
isolated object, sticker style, no background" (метафора)
- "Душ из ботокса" → \
"A garden watering can pouring golden glitter, \
isolated object, sticker style, no background" (ирония)
- Бруксизм в Германии → \
"A steel vise crushing a walnut, \
isolated object, sticker style, no background" (метафора)
- Усталость в аэропорту → \
"A sleepy owl hugging a giant coffee cup, \
isolated object, sticker style, no background" (персонаж)
- Бюрократическая очередь → \
"A tiny snail wearing round reading glasses, \
isolated object, sticker style, no background" (абсурд)
- Любовь к сладкому в Израиле → \
"A piece of baklava dripping with thick golden honey, \
isolated object, sticker style, no background" (культура)

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
11. Максимум 2 стикера на весь рилс (если не указано иное выше).
12. Если сомневаешься — не ставь. Меньше лучше.

=== ШРИФТ СУБТИТРОВ ===

Выбери шрифт для караоке-субтитров на основе контента и настроения:
- Montserrat — чистый, современный (default)
- Poppins — мягкий, округлый, дружелюбный
- Raleway — тонкий, элегантный, premium
- Comfortaa — casual, игривый, округлый

Верни выбранный шрифт в поле font_family.

=== ЦВЕТ СУБТИТРОВ ===

Выбери цвет активного текста субтитров. Не повторяй один цвет — \
чередуй палитру от видео к видео. Контраст с видео важнее красоты.

Палитра:
- #FFFFFF — чистый белый (clean, professional)
- #FFE600 — жёлтый (humor, funny — НЕ используй по умолчанию)
- #FF6B9D — тёплый розовый (upbeat, женственный)
- #00E5FF — свежий голубой (свежий, современный)
- #E0B0FF — лавандовый (soft, нежный)
- #FF3232 — красный акцент (dramatic, energetic)
- #7BFFB2 — мятный (chill, свежий)
- #FFA54F — тёплый оранж (warm, уютный)

Правило: если настроение dynamic/energetic — НЕ бери жёлтый автоматически. \
Посмотри на контент и выбери цвет который усиливает настроение И \
отличается от предыдущих видео.

Верни выбранный цвет в поле subtitle_color.
{style_params_block}
=== ФОРМАТ ОТВЕТА (JSON) ===

{{
  "overall_style": "clean|soft|dynamic|mixed",
  "font_family": "Montserrat|Poppins|Raleway|Comfortaa",
  "subtitle_color": "#FFFFFF",
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
        "font_family": "Montserrat",
        "subtitle_color": "#FFFFFF",
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
    max_stickers: int = DEFAULT_MAX_STICKERS,
    anchors: list[dict] | None = None,
    candidate_spans: list[dict] | None = None,
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

    # ── Validate overlays ──
    raw_overlays = blueprint.get("overlays") or []
    clean_overlays = []

    if candidate_spans is not None:
        # Candidate-based validation: Claude selected span IDs from candidates
        valid_ids = {s["id"] for s in candidate_spans}
        span_map = {s["id"]: s for s in candidate_spans}
        seen_ids: set[int] = set()

        for ov_i, ov in enumerate(raw_overlays):
            if not isinstance(ov, dict):
                continue
            raw_id = ov.get("anchor_id")
            # Parse as int — Director returns int IDs
            if isinstance(raw_id, str) and raw_id.isdigit():
                raw_id = int(raw_id)
            if not raw_id or raw_id not in valid_ids:
                logger.debug("Overlay %d rejected: unknown span id=%r", ov_i, raw_id)
                continue
            if raw_id in seen_ids:
                logger.debug("Overlay %d rejected: duplicate span id=%r", ov_i, raw_id)
                continue
            prompt = ov.get("image_prompt", "")
            if not prompt:
                continue
            if len(clean_overlays) >= max_stickers:
                break

            # Clamp duration 2-8s (tighter than legacy anchor mode)
            dur = ov.get("duration_seconds", 4)
            dur = max(2, min(8, int(dur) if isinstance(dur, (int, float)) else 4))

            enter_anim = ov.get("sticker_enter_animation", "fade")
            exit_anim = ov.get("sticker_exit_animation", "fade")
            if enter_anim not in ALLOWED_STICKER_ANIMATIONS:
                enter_anim = "fade"
            if exit_anim not in ALLOWED_STICKER_ANIMATIONS:
                exit_anim = "fade"

            idx = len(clean_overlays)
            default_x = "75%" if idx % 2 == 0 else "25%"
            x_val = _parse_pct(ov.get("x", default_x), 15, 85, default_x)
            y_val = _parse_pct(ov.get("y", "60%"), 10, 90, "60%")
            w_val = _parse_pct(ov.get("width", "25%"), 10, 40, "25%")
            h_val = _parse_pct(ov.get("height", "25%"), 10, 40, "25%")

            # Merge span timing data for downstream use
            span_data = span_map[raw_id]
            clean_overlays.append({
                "type": "ai_image",
                "anchor_id": raw_id,
                "image_prompt": prompt[:200],
                "duration_seconds": dur,
                "audio_time": span_data["start"],
                "clip_index": span_data["clip_id"],
                "span_end": span_data["end"],
                "trigger_phrase": ov.get("trigger_phrase", ""),
                "sticker_enter_animation": enter_anim,
                "sticker_exit_animation": exit_anim,
                "x": x_val,
                "y": y_val,
                "width": w_val,
                "height": h_val,
            })
            seen_ids.add(raw_id)
            logger.debug("Overlay %d accepted (candidate): id=%d prompt=%r dur=%d", ov_i, raw_id, prompt[:50], dur)

    elif anchors is not None:
        # Anchor-based validation: Claude returns anchor_id + image_prompt + duration_seconds
        valid_ids = {a["anchor_id"] for a in anchors}
        anchor_map = {a["anchor_id"]: a for a in anchors}
        seen_ids_str: set[str] = set()

        for ov_i, ov in enumerate(raw_overlays):
            if not isinstance(ov, dict):
                continue
            anchor_id = ov.get("anchor_id")
            if not anchor_id or anchor_id not in valid_ids:
                logger.debug("Overlay %d rejected: unknown anchor_id=%r", ov_i, anchor_id)
                continue
            if anchor_id in seen_ids_str:
                logger.debug("Overlay %d rejected: duplicate anchor_id=%r", ov_i, anchor_id)
                continue
            prompt = ov.get("image_prompt", "")
            if not prompt:
                continue
            if len(clean_overlays) >= max_stickers:
                break

            # Clamp duration 4-10s
            dur = ov.get("duration_seconds", 6)
            dur = max(2, min(10, int(dur) if isinstance(dur, (int, float)) else 6))

            enter_anim = ov.get("sticker_enter_animation", "fade")
            exit_anim = ov.get("sticker_exit_animation", "fade")
            if enter_anim not in ALLOWED_STICKER_ANIMATIONS:
                enter_anim = "fade"
            if exit_anim not in ALLOWED_STICKER_ANIMATIONS:
                exit_anim = "fade"

            idx = len(clean_overlays)
            default_x = "75%" if idx % 2 == 0 else "25%"
            x_val = _parse_pct(ov.get("x", default_x), 15, 85, default_x)
            y_val = _parse_pct(ov.get("y", "60%"), 10, 90, "60%")
            w_val = _parse_pct(ov.get("width", "25%"), 10, 40, "25%")
            h_val = _parse_pct(ov.get("height", "25%"), 10, 40, "25%")

            # Merge anchor timing data for downstream use
            anchor_data = anchor_map[anchor_id]
            clean_overlays.append({
                "type": "ai_image",
                "anchor_id": anchor_id,
                "image_prompt": prompt[:200],
                "duration_seconds": dur,
                "audio_time": anchor_data["audio_time"],
                "clip_index": anchor_data["clip_index"],
                "sticker_enter_animation": enter_anim,
                "sticker_exit_animation": exit_anim,
                "x": x_val,
                "y": y_val,
                "width": w_val,
                "height": h_val,
            })
            seen_ids_str.add(anchor_id)
            logger.debug("Overlay %d accepted (anchor): id=%s prompt=%r dur=%d", ov_i, anchor_id, prompt[:50], dur)
    else:
        # Legacy timeline-based validation
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
            # Rule: max stickers (configurable, capped at SAFETY_MAX_STICKERS)
            if len(clean_overlays) >= max_stickers:
                logger.debug("Overlay %d rejected: max stickers (%d) reached", ov_i, max_stickers)
                continue
            # Rule: no overlapping
            overlap = False
            for rs, rend in used_ranges:
                if start < rend and end > rs:
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

    # Validate font_family
    font = blueprint.get("font_family", "Montserrat")
    if font not in ALLOWED_FONTS:
        font = "Montserrat"

    # Validate subtitle_color
    sub_color = blueprint.get("subtitle_color", "#FFFFFF")
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", sub_color):
        sub_color = "#FFFFFF"

    result = {
        "overall_style": blueprint.get("overall_style", "clean"),
        "font_family": font,
        "subtitle_color": sub_color,
        "reasoning": blueprint.get("reasoning", ""),
        "clips": cleaned_clips,
        "overlays": clean_overlays,
    }

    return result


async def get_visual_blueprint(
    scenario_text: str,
    clips: list[dict],
    render_mode: str,
    clip_descriptions: list[str] | None = None,
    clip_contexts: list[dict] | None = None,
    style_params: dict | None = None,
    anchors: list[dict] | None = None,
    candidate_spans: list[dict] | None = None,
    model_name: str = "claude-sonnet-4-6",
) -> dict:
    """
    Call Claude to choose transitions + sticker overlays + font.

    :param scenario_text: Script/scenario text (Russian). Can be empty for talking_head.
    :param clips: [{"index": 0, "duration": 4.1}, ...]
    :param render_mode: "talking_head" or "storyboard"
    :param clip_descriptions: Optional list of what's visually on screen per clip.
    :param clip_contexts: Optional per-clip context with speech_text (Whisper) and clip_type.
    :param style_params: Optional dict with sticker_style, sticker_count overrides.
    :param anchors: Optional anchor points [{anchor_id, phrase, clip_index, audio_time}].
                    When provided, Claude receives anchors instead of choosing timing.
    :param candidate_spans: Optional candidate spans for semantic sticker selection.
                           When provided, Claude selects best spans from candidates.
    :return: Blueprint dict with overall_style, font_family, reasoning, clips[].transition, overlays[]
    """
    num_clips = len(clips)
    if num_clips == 0:
        return _make_fallback(0)

    clip_durations = [c["duration"] for c in clips]

    # Build per-clip context block: prefer clip_contexts (with Whisper speech), fallback to clip_descriptions
    if clip_contexts:
        ctx_lines = []
        timeline_pos = 0.0
        for i, ctx in enumerate(clip_contexts):
            dur = clip_durations[i] if i < len(clip_durations) else 0
            end_pos = timeline_pos + dur
            ctype = ctx.get("clip_type", "broll")
            speech_text = ctx.get("speech_text")
            desc = ctx.get("clip_description", "")
            if ctype == "speech" and speech_text:
                ctx_lines.append(
                    f"- Клип {i} (speech, {timeline_pos:.0f}-{end_pos:.0f}s): "
                    f"спикер говорит: '{speech_text}'"
                )
            else:
                label = f"broll, {timeline_pos:.0f}-{end_pos:.0f}s"
                ctx_lines.append(f"- Клип {i} ({label}): {desc or 'нет описания'}")
            timeline_pos = end_pos
        desc_block = "\nКОНТЕКСТ КЛИПОВ (что происходит в каждом клипе):\n" + "\n".join(ctx_lines)
    elif clip_descriptions:
        desc_lines = "\n".join(
            f"- Клип {i}: {desc}" for i, desc in enumerate(clip_descriptions)
        )
        desc_block = f"\nВИДЕОРЯД (что на экране в каждом клипе):\n{desc_lines}"
    else:
        desc_block = ""

    # Build style_params injection block
    style_lines = []
    params = style_params or {}
    max_stickers = DEFAULT_MAX_STICKERS

    sticker_style = params.get("sticker_style")
    if sticker_style:
        style_hint = ""
        if sticker_style in ("realistic", "photorealistic"):
            style_hint = (
                " Описывай объект как реальную фотографию: материалы, освещение, текстуры. "
                "Пиши \"real photo of...\", \"close-up photograph of...\". "
                "Стикер = вырезанное фото реального предмета."
            )
        style_lines.append(
            f"\nСТИЛЬ СТИКЕРОВ: используй стиль \"{sticker_style}\" для image_prompt. "
            f"Например: \"A coffee cup with steam, isolated object, {sticker_style} style, no background\""
            f"{style_hint}"
        )

    sticker_count = params.get("sticker_count")
    if sticker_count is not None:
        max_stickers = min(int(sticker_count), SAFETY_MAX_STICKERS)
        style_lines.append(
            f"\nКОЛИЧЕСТВО СТИКЕРОВ: используй ровно {max_stickers} стикеров, распредели их равномерно по таймлайну."
        )

    style_params_block = "\n".join(style_lines) if style_lines else ""

    prompt_text = SYSTEM_PROMPT.replace("{render_mode}", render_mode)
    prompt_text = prompt_text.replace("{clip_descriptions_block}", desc_block)
    prompt_text = prompt_text.replace("{style_params_block}", style_params_block)

    clips_block = "\n".join(
        f"- Клип {c['index']}: {c['duration']:.1f} сек"
        for c in clips
    )

    total_duration = sum(clip_durations)
    scenario_part = f"Сценарий:\n{scenario_text}\n\n" if scenario_text else ""

    # Build anchor/candidate block for user prompt
    anchor_block = ""
    use_candidate_mode = candidate_spans is not None and len(candidate_spans) > 0

    if use_candidate_mode:
        # Candidate-based mode: Claude selects best spans by semantics
        span_lines = []
        for s in candidate_spans:
            span_lines.append(f'- ID {s["id"]}: [{s["start"]:.1f}-{s["end"]:.1f}s] Клип {s["clip_id"]}: "{s["text"]}"')

        target_count = max_stickers

        anchor_block = (
            f"\n\nCANDIDATE SPANS ({len(candidate_spans)} кандидатов для стикеров):\n"
            + "\n".join(span_lines)
            + f"\n\nВыбери ровно {target_count} лучших моментов для стикеров из списка выше."
            "\nВерни ТОЛЬКО существующие id из candidates. Запрещено придумывать новые timestamps или фразы."
            "\nВыбирай КРЮЧКИ, ПАНЧЛАЙНЫ и ЭМОЦИОНАЛЬНЫЕ ПИКИ — не длинные описательные фразы."
            "\nВыбирай моменты с пиковой энергией: хук (интрига в начале), контраст (сравнение стран/культур), "
            "конкретная рекомендация (совет зрителю), punchline (шутка, неожиданный вывод)."
            "\nПропускай долины: переходы между темами, самопрезентация, обоснования, бэкграунд."
            "\nЕсли есть рекомендация или punchline — не трать стикер на bridge."
            "\nДля каждого верни: anchor_id (= ID кандидата), image_prompt, duration_seconds (3-5с), trigger_phrase, _reasoning."
            "\ntrigger_phrase = точная цитата 2-6 ПОСЛЕДОВАТЕЛЬНЫХ слов из текста этого span. "
            "Это момент ВНУТРИ span когда стикер должен появиться — крючок, панч или пик. "
            "Пример: span 'Особенно напряжённым можно сделать ботокс фулл фейс а некоторым целый душ из ботокса' "
            "→ trigger_phrase 'душ из ботокса'."
            "\nВ _reasoning напиши: роль фразы, отвергнутый образ, стилистическая линза, почему выбрал эту длительность."
        )

        # Replace overlay response format for candidate mode
        prompt_text = prompt_text.replace(
            '"start_second": 5,\n      "end_second": 14,',
            '"duration_seconds": 4,\n      "trigger_phrase": "душ из ботокса",\n      "_reasoning": "крючок — вызывает интерес, отвергнут шприц (банально), стиль: юмор",',
        )
        prompt_text = prompt_text.replace(
            '      "type": "ai_image",\n'
            '      "image_prompt": "A skincare serum bottle, isolated object, sticker style, no background",\n'
            '      "start_second": 5,\n'
            '      "end_second": 14,',
            '      "anchor_id": 1,\n'
            '      "type": "ai_image",\n'
            '      "image_prompt": "A skincare serum bottle, isolated object, sticker style, no background",\n'
            '      "duration_seconds": 4,\n'
            '      "trigger_phrase": "душ из ботокса",\n'
            '      "_reasoning": "крючок — вызывает интерес, отвергнут шприц (банально), стиль: юмор",',
        )

    elif anchors:
        # Legacy anchor-based mode
        anchor_lines = []
        for a in anchors:
            clip_idx = a["clip_index"]
            phrase = a.get("phrase", "")
            anchor_lines.append(f"- {a['anchor_id']}: Клип {clip_idx}, фраза: \"{phrase}\"")
        anchor_block = (
            "\n\nANCHOR POINTS (моменты для стикеров — выбраны бэкендом):\n"
            + "\n".join(anchor_lines)
            + "\n\nДля каждого anchor_id верни image_prompt и duration_seconds (4-8с). "
            "Ты можешь пропустить anchor, не включив его в overlays. "
            "НЕ возвращай start_second/end_second — тайминг рассчитывается бэкендом."
        )

        # Replace overlay response format in system prompt for anchor mode
        prompt_text = prompt_text.replace(
            '"start_second": 5,\n      "end_second": 14,',
            '"duration_seconds": 6,',
        )
        prompt_text = prompt_text.replace(
            '      "type": "ai_image",\n'
            '      "image_prompt": "A skincare serum bottle, isolated object, sticker style, no background",\n'
            '      "start_second": 5,\n'
            '      "end_second": 14,',
            '      "anchor_id": "anchor_0",\n'
            '      "type": "ai_image",\n'
            '      "image_prompt": "A skincare serum bottle, isolated object, sticker style, no background",\n'
            '      "duration_seconds": 6,',
        )

    user_prompt = (
        f"{scenario_part}"
        f"Режим: {render_mode}\n\n"
        f"Клипы ({num_clips} шт.):\n{clips_block}\n\n"
        f"Общий таймлайн: {total_duration:.0f} сек"
        f"{anchor_block}"
    )

    try:
        logger.info("Visual Director model: %s", model_name)
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=60.0)
        message = await client.messages.create(
            model=model_name,
            max_tokens=2048,
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
        validated = _validate_blueprint(
            blueprint, num_clips, clip_durations, max_stickers,
            anchors=anchors, candidate_spans=candidate_spans if use_candidate_mode else None,
        )
        if validated is None:
            logger.warning("Visual blueprint validation failed, using fallback")
            return _make_fallback(num_clips)

        return validated

    except Exception as e:
        logger.warning("Visual director failed (%s), using fallback", e)
        return _make_fallback(num_clips)
