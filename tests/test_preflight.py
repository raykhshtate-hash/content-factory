"""Unit tests for carousel preflight checks (check_album_sizes).

Plan 07-03 Task 2 — TDD RED.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.carousel_service import (
    TELEGRAM_GETFILE_LIMIT,
    check_album_sizes,
)


def _mk_photo_msg(mb: int, *, extra_sizes: list[int] | None = None):
    """Build a mock Telegram photo message."""
    sizes = (extra_sizes or []) + [mb * 1024 * 1024]
    photo = [SimpleNamespace(file_size=s, file_id=f"fid_{s}") for s in sizes]
    return SimpleNamespace(photo=photo, video=None)


def _mk_video_msg(mb: int):
    """Build a mock Telegram video message."""
    return SimpleNamespace(
        photo=None,
        video=SimpleNamespace(file_size=mb * 1024 * 1024, file_id="vid_fid"),
    )


@pytest.mark.asyncio
async def test_rejects_large_photo():
    """A photo message with 25MB must be rejected with Russian Drive fallback."""
    ok, text = await check_album_sizes([_mk_photo_msg(25)])
    assert ok is False
    assert text is not None
    assert "20MB" in text
    assert "Drive" in text


@pytest.mark.asyncio
async def test_accepts_all_under_20mb():
    """All files under 20MB must pass preflight."""
    ok, text = await check_album_sizes([_mk_photo_msg(10), _mk_video_msg(18)])
    assert ok is True
    assert text is None


@pytest.mark.asyncio
async def test_rejection_points_to_correct_index():
    """When file #2 is oversized, the rejection text must mention file #2."""
    ok, text = await check_album_sizes([_mk_photo_msg(10), _mk_photo_msg(21)])
    assert ok is False
    assert text is not None
    assert "#2" in text


@pytest.mark.asyncio
async def test_none_file_size_in_photo_sizes():
    """PhotoSize entries with file_size=None must be skipped; last valid size checked."""
    # Largest known size is 5MB → under limit → should pass
    msg = SimpleNamespace(
        photo=[
            SimpleNamespace(file_size=None, file_id="a"),
            SimpleNamespace(file_size=5_000_000, file_id="b"),
        ],
        video=None,
    )
    ok, text = await check_album_sizes([msg])
    assert ok is True
    assert text is None


@pytest.mark.asyncio
async def test_large_video_rejected():
    """Video message with 22MB must be rejected."""
    ok, text = await check_album_sizes([_mk_video_msg(22)])
    assert ok is False
    assert text is not None


@pytest.mark.asyncio
async def test_rejection_text_mentions_drive_and_ready():
    """Rejection text must reference Drive folder and /ready command."""
    ok, text = await check_album_sizes([_mk_photo_msg(25)])
    assert ok is False
    assert "Drive" in text
    assert "/ready" in text


def test_settings_exposes_drive_carousel_folder_id():
    """settings.DRIVE_CAROUSEL_FOLDER_ID must exist (even if None)."""
    from app.config import settings

    assert hasattr(settings, "DRIVE_CAROUSEL_FOLDER_ID"), (
        "Settings must expose DRIVE_CAROUSEL_FOLDER_ID attribute"
    )
    # Value is None when env var not set — that's fine
    assert settings.DRIVE_CAROUSEL_FOLDER_ID is None or isinstance(
        settings.DRIVE_CAROUSEL_FOLDER_ID, str
    )
