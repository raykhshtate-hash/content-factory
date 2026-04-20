"""Unit tests for render_video_slide, extract_video_thumbnail, _FFMPEG_SEMAPHORE.

Plan 07-03 Task 1 — TDD RED.
All tests MUST fail before implementation.
"""
from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_VIDEO = FIXTURES / "sample_video.mp4"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_video() -> Path:
    """Short (~2s) test video from fixtures."""
    assert SAMPLE_VIDEO.exists(), f"sample_video.mp4 fixture missing: {SAMPLE_VIDEO}"
    return SAMPLE_VIDEO


@pytest.fixture
def sample_overlay_png(tmp_path) -> Path:
    """Generate a sample overlay PNG using the existing _build_slide_overlay."""
    from app.services.carousel_service import _build_slide_overlay

    overlay = _build_slide_overlay("Тест заголовка", "Тело слайда", bright_bg=True)
    out = tmp_path / "overlay.png"
    overlay.save(str(out), "PNG")
    return out


@pytest.fixture
def long_video_90s(tmp_path) -> Path:
    """Generate a 90s test video on-the-fly for the truncate test."""
    out = tmp_path / "long_video.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=90:size=320x240:rate=5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return out


# ── Semaphore value test (sync, no event loop needed) ───────────────────────


def test_ffmpeg_semaphore_value_is_two():
    """_FFMPEG_SEMAPHORE must be module-level asyncio.Semaphore(2)."""
    from app.services.carousel_service import _FFMPEG_SEMAPHORE

    assert isinstance(_FFMPEG_SEMAPHORE, asyncio.Semaphore)
    assert _FFMPEG_SEMAPHORE._value == 2


# ── Codec / profile / mute tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_render_video_slide_codec_profile(tmp_path, sample_video, sample_overlay_png):
    """Output MP4 must be H.264 Main/4.0, yuv420p."""
    from app.services.carousel_service import render_video_slide

    out = str(tmp_path / "out.mp4")
    await render_video_slide(str(sample_video), str(sample_overlay_png), out)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,profile,level,pix_fmt",
            "-of", "default=noprint_wrappers=1",
            out,
        ],
        check=True, capture_output=True, timeout=15,
    )
    info = probe.stdout.decode()
    assert "codec_name=h264" in info
    assert "profile=Main" in info
    assert "pix_fmt=yuv420p" in info
    # level=40 means 4.0 in ffprobe output
    assert "level=40" in info


@pytest.mark.asyncio
async def test_render_video_slide_mutes_audio(tmp_path, sample_video, sample_overlay_png):
    """Output MP4 must have NO audio stream (-an mandatory, E4)."""
    from app.services.carousel_service import render_video_slide

    out = str(tmp_path / "muted.mp4")
    await render_video_slide(str(sample_video), str(sample_overlay_png), out)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "default=noprint_wrappers=1",
            out,
        ],
        check=True, capture_output=True, timeout=15,
    )
    assert probe.stdout.decode().strip() == "", "Expected NO audio stream in rendered video"


@pytest.mark.asyncio
async def test_render_video_slide_truncates_to_60s(tmp_path, long_video_90s, sample_overlay_png):
    """Source longer than 60s must be truncated to exactly 60s (within [59.5, 60.5]s)."""
    from app.services.carousel_service import render_video_slide

    out = str(tmp_path / "truncated.mp4")
    await render_video_slide(str(long_video_90s), str(sample_overlay_png), out, per_slide_timeout=120.0)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            out,
        ],
        check=True, capture_output=True, timeout=15,
    )
    duration = float(probe.stdout.decode().strip())
    assert 59.5 <= duration <= 60.5, f"Expected ~60s, got {duration:.2f}s"


@pytest.mark.asyncio
async def test_render_video_slide_faststart_flag_in_command(monkeypatch, tmp_path, sample_overlay_png):
    """ffmpeg invocation must include +faststart, -an, libx264, main, 4.0."""
    import app.services.carousel_service as cs

    captured_args: list[list[str]] = []

    def fake_subprocess_run(cmd, **kwargs):
        captured_args.append(list(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    # Also patch _probe_duration to avoid real ffprobe call
    async def fake_probe(path: str) -> float:
        return 5.0

    monkeypatch.setattr(cs, "_probe_duration", fake_probe)
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    out = str(tmp_path / "fake.mp4")
    await cs.render_video_slide(
        str(tmp_path / "src.mp4"),
        str(sample_overlay_png),
        out,
    )

    assert len(captured_args) == 1, "Expected exactly one subprocess.run call"
    argv = captured_args[0]
    argv_str = " ".join(argv)

    assert "+faststart" in argv_str, f"Missing +faststart in: {argv_str}"
    assert "-an" in argv, f"Missing -an in: {argv}"
    assert "libx264" in argv_str, f"Missing libx264 in: {argv_str}"
    assert "main" in argv_str, f"Missing 'main' profile in: {argv_str}"
    assert "4.0" in argv_str, f"Missing level 4.0 in: {argv_str}"


# ── Thumbnail test ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_video_thumbnail_size(tmp_path, sample_video):
    """Thumbnail must be exactly 320x320 JPEG and ≤200KB."""
    from app.services.carousel_service import extract_video_thumbnail

    thumb = str(tmp_path / "thumb.jpg")
    await extract_video_thumbnail(str(sample_video), thumb)

    assert Path(thumb).exists(), "Thumbnail file not created"
    size_bytes = Path(thumb).stat().st_size
    assert size_bytes <= 200 * 1024, f"Thumbnail too large: {size_bytes} bytes"

    with Image.open(thumb) as im:
        assert im.size == (320, 320), f"Expected 320x320, got {im.size}"
        assert im.format == "JPEG"


# ── Semaphore concurrency test ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ffmpeg_semaphore_limits_concurrency(monkeypatch, tmp_path, sample_overlay_png):
    """With Semaphore(2), 4 renders should take ≥0.6s (two waves of 0.3s each)."""
    import app.services.carousel_service as cs

    async def fake_probe(path: str) -> float:
        return 5.0

    # Patch _probe_duration to be instant
    monkeypatch.setattr(cs, "_probe_duration", fake_probe)

    # Patch subprocess.run (called inside asyncio.to_thread) with 0.3s sleep
    def fake_run(cmd, **kwargs):
        import time as _time
        _time.sleep(0.3)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    overlay = str(sample_overlay_png)
    src = str(tmp_path / "src.mp4")
    # Create dummy source files (paths don't matter — probe is mocked)
    Path(src).touch()

    start = time.monotonic()
    await asyncio.gather(*[
        cs.render_video_slide(src, overlay, str(tmp_path / f"out{i}.mp4"))
        for i in range(4)
    ])
    elapsed = time.monotonic() - start

    # Semaphore(2) allows 2 at a time; 4 tasks in 0.3s each → 2 waves → ≥0.6s
    assert elapsed >= 0.55, f"Expected ≥0.55s with Semaphore(2), got {elapsed:.3f}s"
