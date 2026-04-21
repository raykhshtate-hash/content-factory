"""Instagram Carousel slide rendering (Phase 7).

Photo path: PIL → JPEG 1080x1350 with semi-transparent plashka overlay.
Video path: ffmpeg filter_complex (Plan 07-03+).

All PIL work is sync; wrap caller in asyncio.to_thread when invoking from
aiogram handlers. The public `render_photo_slide` does this already.

Design tokens (font sizes, plashka opacity, margins, colors) live in
`assets/carousel_tokens.py` — no magic numbers here. Per 07-RESEARCH.md §7.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
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
    PLASHKA_MARGIN_TOP,
    PLASHKA_MARGIN_X,
    PLASHKA_MIN_WIDTH,
    PLASHKA_OPACITY,
    PLASHKA_PADDING_X,
    PLASHKA_PADDING_Y,
    PLASHKA_RADIUS,
    PLASHKA_TEXT_DARK,
    PLASHKA_TEXT_LIGHT,
)

VALID_PLASHKA_POSITIONS = {"top", "center", "bottom"}

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


def _sample_luma(image: Image.Image, y0: int, y1: int) -> float:
    """Return average luminance (0-255) of the strip [y0, y1) of `image`.

    Used to choose dark vs light plashka automatically based on the region the
    plashka will actually occupy.
    """
    y0 = max(0, int(y0))
    y1 = min(CANVAS_H, int(y1))
    if y1 <= y0:
        return 128.0
    region = image.crop((0, y0, CANVAS_W, y1)).convert("L")
    pixels = list(region.getdata())
    return sum(pixels) / len(pixels) if pixels else 128.0


def _plashka_y_range(plashka_h: int, position: str) -> tuple[int, int]:
    """Compute (y0, y1) for plashka based on position keyword."""
    if position == "top":
        y0 = PLASHKA_MARGIN_TOP
        y1 = y0 + plashka_h
    elif position == "center":
        y0 = (CANVAS_H - plashka_h) // 2
        y1 = y0 + plashka_h
    else:  # bottom (default)
        y1 = CANVAS_H - PLASHKA_MARGIN_BOTTOM
        y0 = y1 - plashka_h
    return y0, y1


def _build_slide_overlay(
    title: str,
    body: str | None,
    bright_bg: bool,
    position: str = "bottom",
) -> Image.Image:
    """Return RGBA 1080x1350 overlay: transparent bg + plashka + centered text.

    Caller composes onto base image via Image.alpha_composite.
    bright_bg=True → dark plashka; bright_bg=False → light plashka.
    position: 'top' | 'center' | 'bottom' (default 'bottom').

    Width is adaptive — fits the longest rendered line plus padding, clamped
    between PLASHKA_MIN_WIDTH and (CANVAS_W - 2*PLASHKA_MARGIN_X).
    """
    if position not in VALID_PLASHKA_POSITIONS:
        position = "bottom"

    plashka_bg = PLASHKA_BG_DARK if bright_bg else PLASHKA_BG_LIGHT
    text_color = PLASHKA_TEXT_DARK if bright_bg else PLASHKA_TEXT_LIGHT

    overlay = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    title_clean = _strip_emoji(title)
    body_clean = _strip_emoji(body) if body else None

    font_title = _get_font(FONT_BOLD_PATH, FONT_SIZE_TITLE)
    font_body = _get_font(FONT_REGULAR_PATH, FONT_SIZE_BODY) if body_clean else None

    # Max available inner width (wrap cap). Final plashka width may shrink below this
    # to fit actual text — see adaptive-width step below.
    max_plashka_w = CANVAS_W - 2 * PLASHKA_MARGIN_X
    wrap_inner_width = max_plashka_w - 2 * PLASHKA_PADDING_X

    title_lines = _wrap_text(title_clean, font_title, wrap_inner_width)
    body_lines = (
        _wrap_text(body_clean, font_body, wrap_inner_width)
        if body_clean and font_body
        else []
    )

    # ── Adaptive width: plashka hugs the longest rendered line ──
    longest_line = 0.0
    for line in title_lines:
        longest_line = max(longest_line, font_title.getlength(line))
    if font_body is not None:
        for line in body_lines:
            longest_line = max(longest_line, font_body.getlength(line))
    plashka_w = int(longest_line) + 2 * PLASHKA_PADDING_X
    plashka_w = max(PLASHKA_MIN_WIDTH, min(plashka_w, max_plashka_w))
    plashka_x0 = (CANVAS_W - plashka_w) // 2

    title_line_h = int(FONT_SIZE_TITLE * LINE_HEIGHT_TITLE)
    body_line_h = int(FONT_SIZE_BODY * LINE_HEIGHT_BODY)

    title_block_h = title_line_h * len(title_lines)
    body_block_h = body_line_h * len(body_lines)
    gap_between = 20 if body_lines else 0
    text_block_h = title_block_h + gap_between + body_block_h

    plashka_h = text_block_h + 2 * PLASHKA_PADDING_Y
    plashka_y0, plashka_y1 = _plashka_y_range(plashka_h, position)

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


def _luma_sample_range(position: str) -> tuple[int, int]:
    """Return (y0, y1) range to sample for dark/light plashka auto-select.

    Samples the part of the photo the plashka will cover, so color choice
    matches the actual region — not always the bottom third.
    """
    if position == "top":
        return 0, int(CANVAS_H * 0.33)
    if position == "center":
        return int(CANVAS_H * 0.33), int(CANVAS_H * 0.67)
    return int(CANVAS_H * 0.67), CANVAS_H


def _render_photo_slide_sync(
    source_path: Path,
    title: str,
    body: str | None,
    output_path: Path,
    position: str = "bottom",
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

    # Sample region that plashka will cover to pick dark/light variant
    sample_y0, sample_y1 = _luma_sample_range(position)
    avg_luma = _sample_luma(base, sample_y0, sample_y1)
    bright_bg = avg_luma > PLASHKA_LUMA_THRESHOLD
    logger.debug(
        "Plashka auto-select: pos=%s luma=%.1f → %s",
        position, avg_luma, "dark" if bright_bg else "light",
    )

    base_rgba = base.convert("RGBA")
    overlay = _build_slide_overlay(title, body, bright_bg=bright_bg, position=position)
    composed = Image.alpha_composite(base_rgba, overlay).convert("RGB")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    composed.save(output_path, "JPEG", quality=PHOTO_JPEG_QUALITY, optimize=True)
    logger.info(
        "Rendered carousel photo slide: %s (%dx%d, pos=%s)",
        output_path, CANVAS_W, CANVAS_H, position,
    )
    return output_path


# ── Public API ─────────────────────────────────────────────────────────────


async def render_photo_slide(
    source_path: str | Path,
    title: str,
    body: str | None,
    output_path: str | Path,
    position: str = "bottom",
) -> Path:
    """Render one 1080x1350 JPEG slide with semi-transparent plashka + Russian text.

    Wraps the sync PIL pipeline in asyncio.to_thread so this is safe to call
    from aiogram handlers without blocking the event loop. Source may be
    JPG/PNG/HEIC (pillow_heif opener registered at module import).

    position: 'top' | 'center' | 'bottom' — where to place the plashka.

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
        position,
    )


# ── Video slide rendering (Plan 07-03) ─────────────────────────────────────

# G1 — module-level semaphore, NOT per-instance. Caps concurrent ffmpeg renders
# to prevent OOM on Cloud Run 2Gi when multiple slides render simultaneously.
_FFMPEG_SEMAPHORE = asyncio.Semaphore(2)

# E5 — Instagram carousel max video length
_MAX_VIDEO_SECONDS = 60.0


async def _probe_duration(path: str) -> float:
    """ffprobe helper — returns float seconds. Raises on bad ffprobe output."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    proc = await asyncio.to_thread(
        subprocess.run, cmd, check=True, capture_output=True, timeout=15,
    )
    return float(proc.stdout.decode().strip())


async def render_video_slide(
    source_path: str,
    overlay_png_path: str,
    out_path: str,
    per_slide_timeout: float = 90.0,
) -> None:
    """Scale/crop source to 1080x1350, composite plashka overlay PNG, mute,
    encode H.264 Main/4.0 yuv420p +faststart. Truncates to 60s (E5).
    Obeys _FFMPEG_SEMAPHORE (G1) — max 2 concurrent ffmpeg processes.

    Raises subprocess.CalledProcessError if ffmpeg fails.
    """
    src_duration = await _probe_duration(source_path)
    duration = min(src_duration, _MAX_VIDEO_SECONDS)

    cmd = [
        "ffmpeg", "-y",
        "-i", source_path,
        "-i", overlay_png_path,
        "-filter_complex",
        "[0:v]scale=1080:1350:force_original_aspect_ratio=increase,"
        "crop=1080:1350,setsar=1[base];"
        "[base][1:v]overlay=0:0[v]",
        "-map", "[v]",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-profile:v", "main",
        "-level", "4.0",
        "-pix_fmt", "yuv420p",
        "-preset", "fast",
        "-crf", "23",
        "-movflags", "+faststart",
        "-an",        # E4 — carousel videos are silent
        out_path,
    ]
    async with _FFMPEG_SEMAPHORE:
        await asyncio.to_thread(
            subprocess.run,
            cmd,
            check=True,
            capture_output=True,
            timeout=per_slide_timeout,
        )
    logger.info("Rendered carousel video slide: %s (duration=%.1fs)", out_path, duration)


async def extract_video_thumbnail(video_path: str, thumb_path: str) -> None:
    """Extract a 320x320 JPEG thumbnail at t=0.5s from a video file.

    Uses t=0.5s to avoid black frames from GOP-aligned encodes (Pitfall 14).
    Does NOT consume _FFMPEG_SEMAPHORE — cheap single-frame operation.
    Output is ≤200KB for use as InputMediaVideo.thumbnail (E6, CAR-06).
    """
    cmd = [
        "ffmpeg", "-y",
        "-ss", "0.5", "-i", video_path,
        "-frames:v", "1",
        "-vf",
        "scale=320:320:force_original_aspect_ratio=decrease,"
        "pad=320:320:(ow-iw)/2:(oh-ih)/2:black",
        "-q:v", "5",   # ~70 JPEG quality — well under 200KB for 320×320
        thumb_path,
    ]
    await asyncio.to_thread(
        subprocess.run, cmd, check=True, capture_output=True, timeout=10,
    )
    logger.info("Extracted video thumbnail: %s", thumb_path)


# ── Preflight + ingest + retry (Plan 07-03 Task 2) ─────────────────────────

from html import escape as html_escape  # noqa: E402 — used later in deliver_carousel too

# Bot API hard cap for getFile (Pitfall 5 — not 50MB)
TELEGRAM_GETFILE_LIMIT = 20 * 1024 * 1024  # 20 MB

MAX_ATTEMPTS = 2
PER_SLIDE_TIMEOUT = 90.0   # seconds per slide render (bumped from 45 — ffmpeg timed out on 22s videos under CPU contention)
TOTAL_BUDGET = 520.0       # total carousel render budget in seconds (bumped from 450 — stays under Cloud Run 540s limit)


async def check_album_sizes(album: list) -> tuple[bool, str | None]:
    """Returns (ok, rejection_text).

    Reads msg.photo[-1].file_size for photos and msg.video.file_size for
    videos (never msg.document). Returns (False, Russian text) if ANY file
    exceeds TELEGRAM_GETFILE_LIMIT (20MB Bot API hard cap — Pitfall 5).
    The rejection text references Drive and /ready so the user knows what to do.
    """
    for i, msg in enumerate(album, start=1):
        size: int | None = None
        if getattr(msg, "photo", None):
            # Use the largest PhotoSize with a known file_size
            valid_sizes = [
                p.file_size
                for p in msg.photo
                if getattr(p, "file_size", None) is not None
            ]
            size = max(valid_sizes) if valid_sizes else None
        elif getattr(msg, "video", None):
            size = getattr(msg.video, "file_size", None)

        if size and size > TELEGRAM_GETFILE_LIMIT:
            mb = size // (1024 * 1024)
            return False, (
                f"Файл #{i} ({mb}MB) слишком большой для Telegram (лимит 20MB). "
                f"Загрузи все файлы в Drive-папку карусели и пришли /ready."
            )
    return True, None


async def ingest_album(bot, album: list, tmp_dir: Path) -> list[dict]:
    """Download each Telegram album message to tmp_dir/slide_XX.ext.

    Returns a manifest: [{kind: 'photo'|'video', order: int, local_path: str}]
    Uses msg.photo[-1] (largest) for photos and msg.video for videos.
    """
    items: list[dict] = []
    for i, msg in enumerate(album, start=1):
        if getattr(msg, "photo", None):
            file_id = msg.photo[-1].file_id
            kind, ext = "photo", ".jpg"
        elif getattr(msg, "video", None):
            file_id = msg.video.file_id
            kind, ext = "video", ".mp4"
        else:
            # Non-photo/video in album — skip silently
            continue
        local_path = tmp_dir / f"slide_{i:02d}{ext}"
        file = await bot.get_file(file_id)
        await bot.download_file(file.file_path, destination=str(local_path))
        items.append({"kind": kind, "order": i, "local_path": str(local_path)})
    return items


async def ingest_file_ids(bot, file_infos: list[dict], tmp_dir: Path) -> list[dict]:
    """Download files from stored file_id list to tmp_dir.

    file_infos: [{type: 'photo'|'video', file_id: str}]
    Returns manifest: [{kind, order, local_path}]
    """
    items: list[dict] = []
    for i, fi in enumerate(file_infos, start=1):
        kind = fi["type"]
        ext = ".jpg" if kind == "photo" else ".mp4"
        local_path = tmp_dir / f"slide_{i:02d}{ext}"
        tg_file = await bot.get_file(fi["file_id"])
        await bot.download_file(tg_file.file_path, destination=str(local_path))
        items.append({"kind": kind, "order": i, "local_path": str(local_path)})
    return items


async def render_with_retry(slide: dict) -> dict:
    """Render one slide with up to MAX_ATTEMPTS attempts.

    Never raises — returns {ok: bool, attempts: int, order: int, error?: str}.
    Catches asyncio.TimeoutError (from per_slide_timeout wrap in caller) and
    any other exception. Each failed attempt is logged as a WARNING.
    """
    last_err: Exception | None = None
    position = slide.get("position", "bottom")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if slide["kind"] == "photo":
                await render_photo_slide(
                    slide["source"],
                    slide["text_title"],
                    slide.get("text_body"),
                    slide["out"],
                    position=position,
                )
            else:
                await render_video_slide(
                    slide["source"],
                    slide["overlay"],
                    slide["out"],
                    PER_SLIDE_TIMEOUT,
                )
            return {"ok": True, "attempts": attempt, "order": slide.get("order")}
        except Exception as e:  # noqa: BLE001 — intentional catch-all for retry
            last_err = e
            logger.warning(
                "carousel slide %s attempt %d/%d failed: %s",
                slide.get("order"), attempt, MAX_ATTEMPTS, e,
            )
    return {
        "ok": False,
        "attempts": MAX_ATTEMPTS,
        "error": str(last_err),
        "order": slide.get("order"),
    }


async def render_carousel(slides: list[dict]) -> list[dict]:
    """Run render_with_retry on all slides within TOTAL_BUDGET seconds.

    Returns list of result dicts from render_with_retry.
    Caller must check `all(r['ok'] for r in results)` for all-or-nothing semantics.
    Raises RuntimeError (with Russian 'бюджет' text) only on total-budget overflow.
    """
    coros = [render_with_retry(s) for s in slides]
    try:
        return list(
            await asyncio.wait_for(
                asyncio.gather(*coros),
                timeout=TOTAL_BUDGET,
            )
        )
    except asyncio.TimeoutError as e:
        raise RuntimeError(
            "Общий бюджет времени рендера превышен — карусель отменена"
        ) from e


# ── Delivery (Plan 07-03 Task 3) ────────────────────────────────────────────

from aiogram.types import FSInputFile  # noqa: E402
from aiogram.utils.media_group import MediaGroupBuilder  # noqa: E402

_IG_HINT = "Не забудь добавить trending audio в IG при публикации."

# Telegram Bot API hard limit: max 10 items in a single send_media_group call.
# Instagram carousel allows up to 20, so for 11–20 slides we ship multiple groups.
_TG_MEDIA_GROUP_MAX = 10


async def deliver_carousel(
    bot,
    chat_id: int,
    slides: list[dict],
    caption_telegram: str,
    caption_instagram: str,
) -> None:
    """Send the carousel via send_media_group with caption on the first media (Pitfall 4).
    Sends a follow-up HTML message wrapping caption_instagram in <code> for copy-paste.

    Slides are chunked into groups of ≤10 (Telegram Bot API hard limit). Only the
    first group carries caption_telegram; continuation groups are captionless.

    `slides` manifest shape (from render_carousel + local tmp_path files):
      [{kind: 'photo'|'video', order: int, rendered_path: str, thumb_path: str|None}]

    All video items MUST have thumb_path (required by Bot API — Pitfall 11).
    """
    sorted_slides = sorted(slides, key=lambda s: s["order"])
    chunks = [
        sorted_slides[i:i + _TG_MEDIA_GROUP_MAX]
        for i in range(0, len(sorted_slides), _TG_MEDIA_GROUP_MAX)
    ]

    for chunk_idx, chunk in enumerate(chunks):
        # First chunk carries caption; rest are plain continuation groups.
        caption = caption_telegram if chunk_idx == 0 else ""
        builder = MediaGroupBuilder(caption=caption)
        for slide in chunk:
            media = FSInputFile(slide["rendered_path"])
            if slide["kind"] == "photo":
                builder.add_photo(media=media)
            else:
                thumb_path = slide.get("thumb_path")
                if not thumb_path:
                    raise RuntimeError(
                        f"Video slide {slide['order']} missing thumb_path — "
                        "extract_video_thumbnail must run before deliver_carousel (Pitfall 11)"
                    )
                builder.add_video(
                    media=media,
                    thumbnail=FSInputFile(thumb_path),
                )
        await bot.send_media_group(chat_id=chat_id, media=builder.build())

    # Follow-up: IG caption for copy-paste + trending audio hint
    ig_text = caption_instagram or "(подпись не сгенерирована — добавь вручную)"
    safe_ig = html_escape(ig_text)
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "📋 <b>Текст для Instagram</b> "
            "(скопируй и вставь при публикации карусели):\n\n"
            f"<code>{safe_ig}</code>\n\n"
            f"💡 {_IG_HINT}"
        ),
        parse_mode="HTML",
    )
