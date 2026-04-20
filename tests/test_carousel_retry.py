"""Unit tests for render_with_retry, render_carousel, ingest_album.

Plan 07-03 Task 2 — TDD RED.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.carousel_service as cs


# ── render_with_retry ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_succeeds_first_attempt(monkeypatch):
    """When render succeeds on the first try, returns ok=True, attempts=1."""
    async def fake_render_photo(*args, **kwargs):
        return None  # success

    monkeypatch.setattr(cs, "render_photo_slide", fake_render_photo)

    slide = {
        "kind": "photo",
        "order": 1,
        "source": "/tmp/src.jpg",
        "text_title": "Title",
        "text_body": "Body",
        "out": "/tmp/out.jpg",
    }
    result = await cs.render_with_retry(slide)
    assert result["ok"] is True
    assert result["attempts"] == 1


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt(monkeypatch):
    """When render fails once then succeeds, returns ok=True, attempts=2."""
    call_count = [0]

    async def flaky_render(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] < 2:
            raise RuntimeError("transient failure")

    monkeypatch.setattr(cs, "render_photo_slide", flaky_render)

    slide = {
        "kind": "photo",
        "order": 1,
        "source": "/tmp/src.jpg",
        "text_title": "T",
        "text_body": "B",
        "out": "/tmp/out.jpg",
    }
    result = await cs.render_with_retry(slide)
    assert result["ok"] is True
    assert result["attempts"] == 2


@pytest.mark.asyncio
async def test_retry_gives_up_after_two_failures(monkeypatch):
    """After MAX_ATTEMPTS consecutive failures, returns ok=False, never raises."""

    async def always_fail(*args, **kwargs):
        raise RuntimeError("always fails")

    monkeypatch.setattr(cs, "render_photo_slide", always_fail)

    slide = {
        "kind": "photo",
        "order": 2,
        "source": "/tmp/src.jpg",
        "text_title": "T",
        "text_body": "B",
        "out": "/tmp/out.jpg",
    }
    result = await cs.render_with_retry(slide)
    assert result["ok"] is False
    assert result["attempts"] == cs.MAX_ATTEMPTS
    assert "error" in result


@pytest.mark.asyncio
async def test_retry_catches_timeout_error(monkeypatch):
    """asyncio.TimeoutError inside a render is treated as a retry-able failure."""

    async def timeout_render(*args, **kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(cs, "render_photo_slide", timeout_render)

    slide = {
        "kind": "photo",
        "order": 3,
        "source": "/tmp/src.jpg",
        "text_title": "T",
        "text_body": "B",
        "out": "/tmp/out.jpg",
    }
    # Must not propagate the TimeoutError
    result = await cs.render_with_retry(slide)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_render_carousel_enforces_total_budget(monkeypatch):
    """render_carousel must raise RuntimeError with Russian 'бюджет' text on timeout."""
    monkeypatch.setattr(cs, "TOTAL_BUDGET", 0.2)

    async def slow_render_photo(*args, **kwargs):
        await asyncio.sleep(0.5)

    monkeypatch.setattr(cs, "render_photo_slide", slow_render_photo)

    slides = [
        {
            "kind": "photo",
            "order": i,
            "source": "/tmp/s.jpg",
            "text_title": "T",
            "text_body": "B",
            "out": f"/tmp/out{i}.jpg",
        }
        for i in range(3)
    ]
    with pytest.raises(RuntimeError) as exc_info:
        await cs.render_carousel(slides)

    assert "бюджет" in str(exc_info.value).lower() or "бюджет" in str(exc_info.value)


@pytest.mark.asyncio
async def test_render_carousel_all_or_nothing_signal(monkeypatch):
    """When a slide returns ok=False, render_carousel returns the result list.
    Caller must check all(r['ok'] for r in results) for all-or-nothing semantics."""

    async def always_fail_photo(*args, **kwargs):
        raise RuntimeError("fail")

    monkeypatch.setattr(cs, "render_photo_slide", always_fail_photo)

    slides = [
        {
            "kind": "photo",
            "order": 1,
            "source": "/tmp/s.jpg",
            "text_title": "T",
            "text_body": "B",
            "out": "/tmp/out1.jpg",
        }
    ]
    # render_carousel should NOT raise — it returns results
    results = await cs.render_carousel(slides)
    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0]["ok"] is False


@pytest.mark.asyncio
async def test_ingest_album_writes_tmp_files(monkeypatch, tmp_path):
    """ingest_album downloads each message to tmp_dir/slide_XX.ext, returns manifest."""

    # Mock bot.get_file → returns file with file_path
    # Mock bot.download_file → writes dummy content to destination

    async def fake_get_file(file_id):
        return SimpleNamespace(file_path=f"path/{file_id}.jpg")

    async def fake_download_file(file_path, destination):
        # Write dummy bytes to the destination path
        Path(destination).write_bytes(b"dummy_image_data")

    mock_bot = MagicMock()
    mock_bot.get_file = fake_get_file
    mock_bot.download_file = fake_download_file

    # Build a 2-photo album
    album = [
        SimpleNamespace(
            photo=[
                SimpleNamespace(file_size=100, file_id="photo_1"),
                SimpleNamespace(file_size=200, file_id="photo_2_large"),
            ],
            video=None,
        ),
        SimpleNamespace(
            photo=[
                SimpleNamespace(file_size=300, file_id="photo_3"),
            ],
            video=None,
        ),
    ]

    result = await cs.ingest_album(mock_bot, album, tmp_path)

    assert len(result) == 2
    for item in result:
        assert item["kind"] == "photo"
        assert Path(item["local_path"]).exists()
        assert Path(item["local_path"]).read_bytes() == b"dummy_image_data"
