"""Unit tests for deliver_carousel (MediaGroupBuilder caption, thumbnails, follow-up).

Plan 07-03 Task 3 — TDD RED.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo

from app.services.carousel_service import deliver_carousel


def _make_slides(tmp_path: Path, kinds: list[str]) -> list[dict]:
    """Build a minimal slides manifest with real files for FSInputFile."""
    slides = []
    for i, kind in enumerate(kinds, start=1):
        if kind == "photo":
            p = tmp_path / f"slide_{i:02d}.jpg"
            p.write_bytes(b"fake_jpeg")
            slides.append({"kind": "photo", "order": i, "rendered_path": str(p)})
        else:
            p = tmp_path / f"slide_{i:02d}.mp4"
            p.write_bytes(b"fake_mp4")
            t = tmp_path / f"thumb_{i:02d}.jpg"
            t.write_bytes(b"fake_thumb")
            slides.append({
                "kind": "video",
                "order": i,
                "rendered_path": str(p),
                "thumb_path": str(t),
            })
    return slides


@pytest.mark.asyncio
async def test_caption_on_first_media(tmp_path):
    """Caption must be on the first media item, not on bot.send_media_group itself."""
    bot = MagicMock()
    bot.send_media_group = AsyncMock(return_value=[MagicMock()])
    bot.send_message = AsyncMock()

    slides = _make_slides(tmp_path, ["photo", "photo", "photo"])
    await deliver_carousel(bot, 123, slides, "tg_cap", "ig_cap")

    assert bot.send_media_group.call_count == 1
    call_kwargs = bot.send_media_group.call_args.kwargs
    media_list = call_kwargs["media"]

    # First media has caption
    assert media_list[0].caption == "tg_cap"
    # Other items have no caption (or empty)
    for m in media_list[1:]:
        assert not m.caption


@pytest.mark.asyncio
async def test_every_video_has_thumbnail(tmp_path):
    """Every InputMediaVideo must have a non-None thumbnail (Pitfall 11)."""
    bot = MagicMock()
    bot.send_media_group = AsyncMock(return_value=[MagicMock()])
    bot.send_message = AsyncMock()

    slides = _make_slides(tmp_path, ["video", "photo", "video"])
    await deliver_carousel(bot, 123, slides, "tg", "ig")

    media_list = bot.send_media_group.call_args.kwargs["media"]
    for m in media_list:
        if isinstance(m, InputMediaVideo):
            assert m.thumbnail is not None, "InputMediaVideo must have thumbnail"


@pytest.mark.asyncio
async def test_photo_uses_input_media_photo(tmp_path):
    """Photo slides must be InputMediaPhoto, not InputMediaVideo."""
    bot = MagicMock()
    bot.send_media_group = AsyncMock(return_value=[MagicMock()])
    bot.send_message = AsyncMock()

    slides = _make_slides(tmp_path, ["photo", "video"])
    await deliver_carousel(bot, 123, slides, "tg", "ig")

    media_list = bot.send_media_group.call_args.kwargs["media"]
    assert isinstance(media_list[0], InputMediaPhoto)
    assert isinstance(media_list[1], InputMediaVideo)


@pytest.mark.asyncio
async def test_followup_message_wraps_caption_in_code_html(tmp_path):
    """Follow-up bot.send_message must wrap caption_instagram in <code> HTML tags."""
    bot = MagicMock()
    bot.send_media_group = AsyncMock(return_value=[MagicMock()])
    bot.send_message = AsyncMock()

    slides = _make_slides(tmp_path, ["photo"])
    ig_cap = "Пять правил ухода за кожей зимой"
    await deliver_carousel(bot, 123, slides, "tg", ig_cap)

    assert bot.send_message.call_count == 1
    msg_kwargs = bot.send_message.call_args.kwargs
    assert msg_kwargs.get("parse_mode") == "HTML"
    assert f"<code>{ig_cap}</code>" in msg_kwargs["text"] or ig_cap in msg_kwargs["text"]
    # Must contain the <code> tag
    assert "<code>" in msg_kwargs["text"]


@pytest.mark.asyncio
async def test_followup_mentions_trending_audio(tmp_path):
    """Follow-up message must mention 'trending' (audio hint for Instagram)."""
    bot = MagicMock()
    bot.send_media_group = AsyncMock(return_value=[MagicMock()])
    bot.send_message = AsyncMock()

    slides = _make_slides(tmp_path, ["photo"])
    await deliver_carousel(bot, 123, slides, "tg", "ig")

    msg_text = bot.send_message.call_args.kwargs["text"]
    assert "trending" in msg_text.lower(), f"'trending' not found in follow-up: {msg_text}"


@pytest.mark.asyncio
async def test_handles_mixed_photo_video(tmp_path):
    """deliver_carousel must not raise for a mix of photos and videos."""
    bot = MagicMock()
    bot.send_media_group = AsyncMock(return_value=[MagicMock()])
    bot.send_message = AsyncMock()

    slides = _make_slides(tmp_path, ["photo", "video", "photo", "video"])
    await deliver_carousel(bot, 123, slides, "tg", "ig")

    assert bot.send_media_group.call_count == 1
    assert bot.send_message.call_count == 1


@pytest.mark.asyncio
async def test_empty_instagram_caption_still_sends_followup(tmp_path):
    """When caption_instagram is empty/None, follow-up is still sent with placeholder."""
    bot = MagicMock()
    bot.send_media_group = AsyncMock(return_value=[MagicMock()])
    bot.send_message = AsyncMock()

    slides = _make_slides(tmp_path, ["photo"])
    await deliver_carousel(bot, 123, slides, "tg", "")

    # Must still call send_message — no crash on empty ig caption
    assert bot.send_message.call_count == 1
    msg_text = bot.send_message.call_args.kwargs["text"]
    assert len(msg_text) > 0
