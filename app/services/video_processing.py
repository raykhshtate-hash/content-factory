"""
Video Processing Service — MOV to MP4 remux utility.

Reserved for future use. Not currently called from the pipeline.
"""

import asyncio
import logging
import os
import shutil
import tempfile

import requests as req

from app.services.gcs_service import GCSService

logger = logging.getLogger(__name__)


async def _run_ffmpeg(args: list[str], description: str = ""):
    """Run ffmpeg/ffprobe command in thread pool."""
    result = await asyncio.to_thread(
        __import__("subprocess").run, args,
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        logger.error("ffmpeg failed (%s): %s", description, result.stderr[:500])
    return result


async def remux_mov_to_mp4(gcs_uri: str) -> str:
    """Remux MOV to MP4 container (no re-encode). Reserved for future use."""
    if gcs_uri.lower().endswith(".mp4"):
        return gcs_uri

    gcs = GCSService()
    signed_url = await asyncio.to_thread(gcs.generate_presigned_url, gcs_uri)

    tmp_dir = tempfile.mkdtemp()
    raw_path = os.path.join(tmp_dir, "raw.mov")
    mp4_path = os.path.join(tmp_dir, "remuxed.mp4")

    try:
        resp = await asyncio.to_thread(
            lambda: req.get(signed_url, stream=True, timeout=180)
        )
        resp.raise_for_status()
        with open(raw_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        resp.close()

        result = await _run_ffmpeg(
            ["ffmpeg", "-y", "-i", raw_path, "-c", "copy", mp4_path],
            "MOV to MP4 remux",
        )
        if result.returncode != 0:
            return gcs_uri

        original_blob_path = gcs_uri.split("//", 1)[1].split("/", 1)[1]
        mp4_gcs_path = original_blob_path.rsplit(".", 1)[0] + ".mp4"
        bucket_name = gcs_uri.split("//")[1].split("/")[0]
        bucket = gcs._client.bucket(bucket_name)
        blob = bucket.blob(mp4_gcs_path)
        await asyncio.to_thread(blob.upload_from_filename, mp4_path)

        mp4_gcs_uri = f"gs://{bucket_name}/{mp4_gcs_path}"
        logger.info("Remuxed MOV→MP4: %s", mp4_gcs_uri)
        return mp4_gcs_uri
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
