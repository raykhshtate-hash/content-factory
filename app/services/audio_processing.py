"""
Audio Processing Service — silence removal + speedup for storyboard mode.

Pipeline:
1. Download voiceover from GCS (via presigned URL)
2. Remove silences > threshold via ffmpeg silenceremove
3. Speed up to match total video duration (capped at max_speedup)
4. Upload processed audio back to GCS
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile

from app.services.gcs_service import GCSService

logger = logging.getLogger(__name__)


async def _run_ffmpeg(args: list[str], description: str = "") -> subprocess.CompletedProcess:
    """Run ffmpeg/ffprobe command in thread pool."""
    result = await asyncio.to_thread(
        subprocess.run, args,
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        logger.error("ffmpeg failed (%s): %s", description, result.stderr[:500])
    return result


async def get_duration(url_or_path: str) -> float:
    """Get media duration via ffprobe."""
    result = await _run_ffmpeg(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", url_or_path],
        "get_duration",
    )
    if result.returncode != 0:
        return 0.0
    try:
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except Exception:
        return 0.0


async def process_voiceover(
    voiceover_gcs_uri: str,
    total_video_duration: float,
    video_durations: list[float] | None = None,
    max_speedup: float = 1.5,
    silence_threshold_sec: float = 1.0,
) -> tuple[str, float, float]:
    """
    Process voiceover audio:
    1. Download from GCS
    2. Remove silences > silence_threshold_sec
    3. Speed up to match video duration (capped at max_speedup)
    4. Upload processed audio back to GCS

    Returns: (processed_gcs_uri, processed_duration, speedup_applied)
    """
    gcs = GCSService()

    # Download voiceover to temp file
    signed_url = await asyncio.to_thread(
        gcs.generate_presigned_url, voiceover_gcs_uri
    )

    tmp_dir = tempfile.mkdtemp()
    raw_path = os.path.join(tmp_dir, "raw_voiceover.mp3")
    cleaned_path = os.path.join(tmp_dir, "cleaned.mp3")
    final_path = os.path.join(tmp_dir, "processed.mp3")

    try:
        # Download via requests (ffmpeg can't handle GCS presigned URLs with special chars)
        import requests as req
        try:
            resp = await asyncio.to_thread(
                lambda: req.get(signed_url, stream=True, timeout=120)
            )
            resp.raise_for_status()
            with open(raw_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            resp.close()
        except Exception as e:
            raise RuntimeError(f"Failed to download voiceover: {e}")

        raw_duration = await get_duration(raw_path)
        logger.info("Voiceover raw duration: %.1fs", raw_duration)

        # Step 1: Remove silences > threshold
        result = await _run_ffmpeg(
            [
                "ffmpeg", "-y", "-i", raw_path,
                "-af", (
                    f"silenceremove="
                    f"stop_periods=-1:"
                    f"stop_duration={silence_threshold_sec}:"
                    f"stop_threshold=-30dB"
                ),
                "-ar", "44100",
                cleaned_path,
            ],
            "silence removal",
        )
        if result.returncode != 0:
            # Fallback: use raw audio without silence removal
            logger.warning("Silence removal failed, using raw audio")
            cleaned_path = raw_path

        cleaned_duration = await get_duration(cleaned_path)
        logger.info("Voiceover cleaned duration: %.1fs (removed %.1fs of silence)",
                     cleaned_duration, raw_duration - cleaned_duration)

        # Step 2: Always speed up after silence removal.
        # Target: 70% of total video duration. Gemini will distribute
        # segments proportionally to clip lengths, so short clips get
        # short segments automatically.

        if total_video_duration <= 0:
            speedup = 1.3
        else:
            target_duration = total_video_duration * 0.70
            if cleaned_duration <= target_duration:
                speedup = 1.2  # minimum speedup for reels pacing
            else:
                speedup = cleaned_duration / target_duration

        speedup = max(1.2, min(speedup, max_speedup))

        logger.info(
            "Speedup: cleaned=%.1fs, video=%.1fs, target=%.1fs, speedup=%.2fx",
            cleaned_duration, total_video_duration,
            total_video_duration * 0.70 if total_video_duration > 0 else 0,
            speedup,
        )

        # Step 3: Apply speedup via atempo
        result = await _run_ffmpeg(
            [
                "ffmpeg", "-y", "-i", cleaned_path,
                "-af", f"atempo={speedup}",
                "-ar", "44100",
                final_path,
            ],
            f"speedup {speedup:.2f}x",
        )
        if result.returncode != 0:
            logger.warning("Speedup failed, using cleaned audio")
            final_path = cleaned_path
            speedup = 1.0

        processed_duration = await get_duration(final_path)
        logger.info("Voiceover processed: %.1fs (speedup=%.2fx)", processed_duration, speedup)

        # Upload processed audio to GCS
        # Build path next to original: footage/{item_id}/processed_voiceover.mp3
        original_path = voiceover_gcs_uri.split("//", 1)[1].split("/", 1)[1]  # strip gs://bucket/
        processed_gcs_path = original_path.rsplit("/", 1)[0] + "/processed_voiceover.mp3"

        bucket_name = voiceover_gcs_uri.split("//")[1].split("/")[0]
        bucket = gcs._client.bucket(bucket_name)
        blob = bucket.blob(processed_gcs_path)
        await asyncio.to_thread(blob.upload_from_filename, final_path)

        processed_gcs_uri = f"gs://{bucket_name}/{processed_gcs_path}"
        logger.info("Processed voiceover uploaded: %s", processed_gcs_uri)

        return processed_gcs_uri, processed_duration, speedup

    finally:
        # Cleanup temp files
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def adjust_voiceover_for_transitions(
    voiceover_gcs_uri: str,
    overlap_seconds: float,
    current_duration: float,
) -> tuple[str, float]:
    """
    Re-speed voiceover to compensate transition overlaps.
    Storyboard mode only — transitions eat into video timeline,
    so voiceover needs to be slightly faster to stay in sync.

    Returns (new_gcs_uri, new_duration).
    """
    if overlap_seconds <= 0:
        return voiceover_gcs_uri, current_duration

    new_target = current_duration - overlap_seconds
    speedup = current_duration / new_target

    if speedup > 1.3:
        logger.warning(
            "Overlap %.1fs too large for %.1fs voiceover (would need %.2fx), skipping adjustment",
            overlap_seconds, current_duration, speedup,
        )
        return voiceover_gcs_uri, current_duration

    gcs = GCSService()

    signed_url = await asyncio.to_thread(
        gcs.generate_presigned_url, voiceover_gcs_uri
    )

    tmp_dir = tempfile.mkdtemp()
    input_path = os.path.join(tmp_dir, "input.mp3")
    output_path = os.path.join(tmp_dir, "adjusted.mp3")

    try:
        import requests as req
        resp = await asyncio.to_thread(
            lambda: req.get(signed_url, stream=True, timeout=120)
        )
        resp.raise_for_status()
        with open(input_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        resp.close()

        result = await _run_ffmpeg(
            [
                "ffmpeg", "-y", "-i", input_path,
                "-af", f"atempo={speedup:.4f}",
                "-ar", "44100",
                output_path,
            ],
            f"transition compensation {speedup:.3f}x",
        )
        if result.returncode != 0:
            logger.warning("Transition compensation ffmpeg failed, using original")
            return voiceover_gcs_uri, current_duration

        new_duration = await get_duration(output_path)

        # Upload adjusted audio to GCS
        original_path = voiceover_gcs_uri.split("//", 1)[1].split("/", 1)[1]
        adjusted_gcs_path = original_path.rsplit("/", 1)[0] + "/adjusted_voiceover.mp3"

        bucket_name = voiceover_gcs_uri.split("//")[1].split("/")[0]
        bucket = gcs._client.bucket(bucket_name)
        blob = bucket.blob(adjusted_gcs_path)
        await asyncio.to_thread(blob.upload_from_filename, output_path)

        adjusted_gcs_uri = f"gs://{bucket_name}/{adjusted_gcs_path}"

        logger.info(
            "Adjusted voiceover: %.1fs -> %.1fs (%.3fx, compensating %.1fs overlap)",
            current_duration, new_duration, speedup, overlap_seconds,
        )
        return adjusted_gcs_uri, new_duration

    except Exception as e:
        logger.warning("Transition audio adjustment failed (%s), using original", e)
        return voiceover_gcs_uri, current_duration

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
