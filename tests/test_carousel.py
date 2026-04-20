"""Unit tests for app.services.carousel_service (Phase 7 Plan 07-01).

Covers:
- Font cache identity (_get_font).
- Emoji stripping for PIL (color emoji cannot render natively).
- Word-boundary text wrap (including single-token overflow edge case).
- End-to-end photo slide render: JPEG input, HEIC input, emoji-in-text, no body.
- FileNotFoundError for missing source.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.services.carousel_service import (
    _get_font,
    _strip_emoji,
    _wrap_text,
    render_photo_slide,
)
from assets.carousel_tokens import FONT_BOLD_PATH, FONT_REGULAR_PATH


# ── Helpers ─────────────────────────────────────────────────────


def test_get_font_caches():
    """Two calls with identical (path, size) must return the same object."""
    a = _get_font(FONT_BOLD_PATH, 40)
    b = _get_font(FONT_BOLD_PATH, 40)
    assert a is b


def test_get_font_distinct_sizes_are_distinct():
    """Different sizes must return different font objects."""
    a = _get_font(FONT_REGULAR_PATH, 40)
    b = _get_font(FONT_REGULAR_PATH, 68)
    assert a is not b


def test_strip_emoji_removes_color_emoji():
    out = _strip_emoji("Привет 👋 мир 🎉")
    assert "👋" not in out
    assert "🎉" not in out
    assert "Привет" in out
    assert "мир" in out


def test_strip_emoji_preserves_cyrillic_and_punctuation():
    text = "Как дела? — Отлично!"
    assert _strip_emoji(text) == text


def test_wrap_text_breaks_on_word_boundary():
    font = _get_font(FONT_BOLD_PATH, 68)
    text = (
        "Это длинный заголовок который обязательно должен "
        "переноситься на несколько строк"
    )
    lines = _wrap_text(text, font, max_width=700)
    assert len(lines) >= 2
    for line in lines:
        # Single-token lines may exceed max_width by contract;
        # multi-word lines must fit.
        if " " in line:
            assert font.getlength(line) <= 700


def test_wrap_text_single_long_word_stays_on_own_line():
    font = _get_font(FONT_BOLD_PATH, 68)
    lines = _wrap_text("ААААААААААААААААААААААААА", font, max_width=100)
    assert len(lines) == 1


def test_wrap_text_empty_input_returns_empty_list():
    font = _get_font(FONT_BOLD_PATH, 68)
    assert _wrap_text("", font, max_width=800) == []


# ── Renderer ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_render_photo_slide_produces_1080x1350_jpeg(fixtures_dir, tmp_path):
    out = tmp_path / "slide.jpg"
    result = await render_photo_slide(
        fixtures_dir / "sample.jpg",
        title="Тест заголовка",
        body="Краткое описание",
        output_path=out,
    )
    assert result.exists()
    with Image.open(result) as im:
        assert im.size == (1080, 1350)
        assert im.format == "JPEG"


@pytest.mark.asyncio
async def test_render_photo_slide_accepts_heic_input(fixtures_dir, tmp_path):
    out = tmp_path / "slide_heic.jpg"
    result = await render_photo_slide(
        fixtures_dir / "sample.heic",
        title="HEIC тест",
        body=None,
        output_path=out,
    )
    with Image.open(result) as im:
        assert im.size == (1080, 1350)


@pytest.mark.asyncio
async def test_render_photo_slide_strips_emoji_before_render(fixtures_dir, tmp_path):
    out = tmp_path / "slide_emoji.jpg"
    # Must not raise — emoji stripped before PIL draw.text
    result = await render_photo_slide(
        fixtures_dir / "sample.jpg",
        title="Тест 👋 с эмодзи 🎉",
        body="Описание ✨ со звёздочками",
        output_path=out,
    )
    assert result.exists()


@pytest.mark.asyncio
async def test_render_photo_slide_no_body(fixtures_dir, tmp_path):
    out = tmp_path / "slide_title_only.jpg"
    result = await render_photo_slide(
        fixtures_dir / "sample.jpg",
        title="Только заголовок",
        body=None,
        output_path=out,
    )
    assert result.exists()
    with Image.open(result) as im:
        assert im.size == (1080, 1350)


@pytest.mark.asyncio
async def test_render_photo_slide_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        await render_photo_slide(
            tmp_path / "does_not_exist.jpg",
            title="x",
            body=None,
            output_path=tmp_path / "out.jpg",
        )
