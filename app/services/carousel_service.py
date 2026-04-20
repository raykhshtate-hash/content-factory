"""Instagram Carousel slide rendering (Phase 7).

Photo path: PIL → JPEG 1080x1350 with semi-transparent plashka overlay.
Video path: ffmpeg filter_complex (NOT in this plan — lands in Plan 07-03).

All PIL work is sync; wrap caller in asyncio.to_thread when invoking from
aiogram handlers. The public `render_photo_slide` does this already.

Design tokens (font sizes, plashka opacity, margins, colors) live in
`assets/carousel_tokens.py` — no magic numbers here. Per 07-RESEARCH.md §7.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import pillow_heif
from PIL import Image, ImageDraw, ImageFont

from assets.carousel_tokens import (
    CANVAS_H,
    CANVAS_W,
    FONT_BOLD_PATH,
    FONT_REGULAR_PATH,
    FONT_SIZE_BODY,
    FONT_SIZE_TITLE,
    LINE_HEIGHT_BODY,
    LINE_HEIGHT_TITLE,
    PHOTO_JPEG_QUALITY,
    PLASHKA_BG_DARK,
    PLASHKA_BG_LIGHT,
    PLASHKA_LUMA_THRESHOLD,
    PLASHKA_MARGIN_BOTTOM,
    PLASHKA_MARGIN_X,
    PLASHKA_OPACITY,
    PLASHKA_PADDING_X,
    PLASHKA_PADDING_Y,
    PLASHKA_RADIUS,
    PLASHKA_TEXT_DARK,
    PLASHKA_TEXT_LIGHT,
)

logger = logging.getLogger(__name__)

# CRITICAL: register HEIC opener at import time so Image.open() handles iPhone
# HEIC inputs transparently. Per 07-RESEARCH.md pitfall #3 — if this ran inside
# a function, Image.open() on a .heic would raise UnidentifiedImageError.
pillow_heif.register_heif_opener()

# Module-level font cache. ImageFont.truetype() is expensive (~5ms per call).
# Safe under CPython GIL; asyncio.to_thread shares the same dict across
# threads via GIL-protected access. Per 07-RESEARCH.md pattern #2.
_FONTS: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

# Emoji regex: broad Unicode ranges covering color emoji that PIL cannot render
# with regular TTF fonts (would raise or fall back to glyph .notdef).
# Per 07-RESEARCH.md pitfall #5.
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002600-\U000027BF"  # misc symbols, dingbats
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA70-\U0001FAFF"  # symbols & pictographs extended-A
    "\U0001F018-\U0001F270"  # various
    "]+",
    flags=re.UNICODE,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _get_font(path: str | Path, size: int) -> ImageFont.FreeTypeFont:
    """Module-level font cache. Identity-stable per (path, size) tuple."""
    key = (str(path), size)
    if key not in _FONTS:
        _FONTS[key] = ImageFont.truetype(str(path), size)
    return _FONTS[key]


def _strip_emoji(text: str) -> str:
    """Remove color emoji before PIL render. Preserves Cyrillic + punctuation."""
    return _EMOJI_RE.sub("", text)


def _wrap_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """Greedy word-boundary wrap.

    Contract: a single token wider than `max_width` is kept on its own line
    (do NOT break inside a word — it would look worse than overflow).
    """
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if font.getlength(candidate) <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _sample_luma(image: Image.Image, y_start: int) -> float:
    """Return average luminance (0-255) of the bottom region of `image`.

    Samples the strip from y_start to canvas bottom, converted to grayscale.
    Used to choose dark vs light plashka automatically.
    """
    region = image.crop((0, y_start, CANVAS_W, CANVAS_H)).convert("L")
    pixels = list(region.getdata())
    return sum(pixels) / len(pixels) if pixels else 128.0


def _build_slide_overlay(title: str, body: str | None, bright_bg: bool) -> Image.Image:
    """Return RGBA 1080x1350 overlay: transparent bg + plashka + centered text.

    Caller composes onto base image via Image.alpha_composite.
    bright_bg=True → dark plashka; bright_bg=False → light plashka.
    """
    plashka_bg = PLASHKA_BG_DARK if bright_bg else PLASHKA_BG_LIGHT
    text_color = PLASHKA_TEXT_DARK if bright_bg else PLASHKA_TEXT_LIGHT

    overlay = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    title_clean = _strip_emoji(title)
    body_clean = _strip_emoji(body) if body else None

    font_title = _get_font(FONT_BOLD_PATH, FONT_SIZE_TITLE)
    font_body = _get_font(FONT_REGULAR_PATH, FONT_SIZE_BODY) if body_clean else None

    # Text-usable width inside plashka (canvas minus side margins minus padding)
    plashka_inner_width = CANVAS_W - 2 * PLASHKA_MARGIN_X - 2 * PLASHKA_PADDING_X

    title_lines = _wrap_text(title_clean, font_title, plashka_inner_width)
    body_lines = (
        _wrap_text(body_clean, font_body, plashka_inner_width)
        if body_clean and font_body
        else []
    )

    title_line_h = int(FONT_SIZE_TITLE * LINE_HEIGHT_TITLE)
    body_line_h = int(FONT_SIZE_BODY * LINE_HEIGHT_BODY)

    title_block_h = title_line_h * len(title_lines)
    body_block_h = body_line_h * len(body_lines)
    gap_between = 20 if body_lines else 0
    text_block_h = title_block_h + gap_between + body_block_h

    plashka_h = text_block_h + 2 * PLASHKA_PADDING_Y
    plashka_w = CANVAS_W - 2 * PLASHKA_MARGIN_X
    plashka_x0 = PLASHKA_MARGIN_X
    plashka_y1 = CANVAS_H - PLASHKA_MARGIN_BOTTOM
    plashka_y0 = plashka_y1 - plashka_h

    # Semi-transparent rounded plashka
    draw.rounded_rectangle(
        [plashka_x0, plashka_y0, plashka_x0 + plashka_w, plashka_y1],
        radius=PLASHKA_RADIUS,
        fill=(*plashka_bg, PLASHKA_OPACITY),
    )

    # Render text centered within plashka
    y = plashka_y0 + PLASHKA_PADDING_Y
    for line in title_lines:
        line_w = font_title.getlength(line)
        x = plashka_x0 + (plashka_w - line_w) / 2
        draw.text((x, y), line, font=font_title, fill=text_color)
        y += title_line_h

    if body_lines and font_body is not None:
        y += gap_between
        for line in body_lines:
            line_w = font_body.getlength(line)
            x = plashka_x0 + (plashka_w - line_w) / 2
            draw.text((x, y), line, font=font_body, fill=text_color)
            y += body_line_h

    return overlay


def _render_photo_slide_sync(
    source_path: Path,
    title: str,
    body: str | None,
    output_path: Path,
) -> Path:
    """Sync photo slide renderer. Wrapped by `render_photo_slide` for async."""
    with Image.open(source_path) as src:
        # Scale+crop to 1080x1350 (cover, centered)
        src_w, src_h = src.width, src.height
        src_ratio = src_w / src_h
        target_ratio = CANVAS_W / CANVAS_H
        if src_ratio > target_ratio:
            # Source is wider → scale by height, crop width
            new_h = CANVAS_H
            new_w = int(src_w * (CANVAS_H / src_h))
        else:
            # Source is taller / equal → scale by width, crop height
            new_w = CANVAS_W
            new_h = int(src_h * (CANVAS_W / src_w))
        scaled = src.convert("RGB").resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - CANVAS_W) // 2
        top = (new_h - CANVAS_H) // 2
        base = scaled.crop((left, top, left + CANVAS_W, top + CANVAS_H))

    # Sample bottom third of the photo to pick plashka variant automatically
    sample_y = int(CANVAS_H * 0.67)
    avg_luma = _sample_luma(base, sample_y)
    bright_bg = avg_luma > PLASHKA_LUMA_THRESHOLD
    logger.debug("Plashka auto-select: luma=%.1f → %s", avg_luma, "dark" if bright_bg else "light")

    base_rgba = base.convert("RGBA")
    overlay = _build_slide_overlay(title, body, bright_bg=bright_bg)
    composed = Image.alpha_composite(base_rgba, overlay).convert("RGB")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    composed.save(output_path, "JPEG", quality=PHOTO_JPEG_QUALITY, optimize=True)
    logger.info(
        "Rendered carousel photo slide: %s (%dx%d)",
        output_path, CANVAS_W, CANVAS_H,
    )
    return output_path


# ── Public API ─────────────────────────────────────────────────────────────


async def render_photo_slide(
    source_path: str | Path,
    title: str,
    body: str | None,
    output_path: str | Path,
) -> Path:
    """Render one 1080x1350 JPEG slide with semi-transparent plashka + Russian text.

    Wraps the sync PIL pipeline in asyncio.to_thread so this is safe to call
    from aiogram handlers without blocking the event loop. Source may be
    JPG/PNG/HEIC (pillow_heif opener registered at module import).

    Raises:
        FileNotFoundError: if `source_path` does not exist.
    """
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Slide source not found: {source}")
    return await asyncio.to_thread(
        _render_photo_slide_sync,
        source,
        title,
        body,
        Path(output_path),
    )
