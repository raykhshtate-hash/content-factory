"""
Google Cloud Storage Service.

Provides:
 - generate_presigned_url  — V4 signed URL for Creatomate / external consumers
 - upload_from_url         — fetch a remote URL and stream it into GCS
 - download                — download blob contents as bytes
 - delete                  — delete a blob
 - upload_carousel_slides  — async helper to upload carousel rendered files + thumbs
"""

import asyncio
import os
import re
from datetime import timedelta

import requests
from google.cloud import storage
from google.oauth2 import service_account

from app.config import settings

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _parse_gs_uri(gs_uri: str) -> tuple[str, str]:
    """Parse 'gs://bucket/path/to/blob' → (bucket, path/to/blob)."""
    m = re.match(r"^gs://([^/]+)/(.+)$", gs_uri)
    if not m:
        raise ValueError(f"Invalid GCS URI: {gs_uri}")
    return m.group(1), m.group(2)


def _get_credentials():
    creds_path = settings.GOOGLE_APPLICATION_CREDENTIALS
    if not creds_path or not os.path.exists(creds_path):
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS is not set or file does not exist."
        )
    return service_account.Credentials.from_service_account_file(
        creds_path, scopes=SCOPES
    )


class GCSService:
    def __init__(self, bucket_name: str | None = None):
        self._creds = _get_credentials()
        self._client = storage.Client(credentials=self._creds)
        self._default_bucket = bucket_name or settings.GCS_BUCKET_NAME

    # ------------------------------------------------------------------
    # generate_presigned_url
    # ------------------------------------------------------------------
    def generate_presigned_url(
        self,
        gcs_uri: str,
        expiration_minutes: int = 360,
    ) -> str:
        """
        Generate a V4 signed GET URL for the given gs:// URI.
        Requires service-account credentials (signBlob permission).
        """
        bucket_name, blob_name = _parse_gs_uri(gcs_uri)
        bucket = self._client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=expiration_minutes),
            method="GET",
            credentials=self._creds,
        )
        return url

    # ------------------------------------------------------------------
    # upload_from_url
    # ------------------------------------------------------------------
    def upload_from_url(self, url: str, gcs_path: str) -> str:
        """
        Fetch a remote URL (e.g. Creatomate render result) and stream
        it into the default GCS bucket.

        Returns gs:// URI of the uploaded object.
        """
        resp = requests.get(url, stream=True, timeout=300)
        resp.raise_for_status()

        bucket = self._client.bucket(self._default_bucket)
        blob = bucket.blob(gcs_path, chunk_size=10 * 1024 * 1024)

        content_type = resp.headers.get("Content-Type", "application/octet-stream")

        # Stream directly — same zero-buffer pattern as drive_service
        blob.upload_from_file(
            resp.raw,
            content_type=content_type,
            timeout=600,
        )
        resp.close()
        return f"gs://{self._default_bucket}/{gcs_path}"

    # ------------------------------------------------------------------
    # download
    # ------------------------------------------------------------------
    def download(self, gcs_uri: str) -> bytes:
        """Download blob contents as bytes (use only for small files)."""
        bucket_name, blob_name = _parse_gs_uri(gcs_uri)
        bucket = self._client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        return blob.download_as_bytes()

    # ------------------------------------------------------------------
    # delete
    # ------------------------------------------------------------------
    def delete(self, gcs_uri: str) -> None:
        """Delete a blob from GCS."""
        bucket_name, blob_name = _parse_gs_uri(gcs_uri)
        bucket = self._client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.delete()

    # ------------------------------------------------------------------
    # upload_local_file
    # ------------------------------------------------------------------
    def upload_local_file(self, local_path: str, gs_uri: str) -> None:
        """Upload a local file to GCS at the given gs:// URI."""
        bucket_name, blob_name = _parse_gs_uri(gs_uri)
        bucket = self._client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(local_path)


# ── Carousel upload helper (Plan 07-03) ─────────────────────────────────────


async def upload_carousel_slides(items: list[dict], bucket_prefix: str) -> list[dict]:
    """Upload rendered slide files + thumbnails to GCS.

    Returns a manifest list with gs:// URIs and presigned URLs (6h expiry).
    Each item in `items` is a dict with keys:
      - kind: 'photo' | 'video'
      - order: int
      - rendered_path: str  (local path to rendered slide file)
      - thumb_path: str | None  (local path to thumbnail JPEG, video only)

    Returned manifest items contain:
      - kind, order, rendered_gs, rendered_presigned
      - thumb_gs, thumb_presigned  (video only, when thumb_path provided)
    """
    gcs = GCSService()
    manifest: list[dict] = []

    for item in items:
        ext = "mp4" if item["kind"] == "video" else "jpg"
        rendered_gs = (
            f"gs://{gcs._default_bucket}/"
            f"{bucket_prefix}/slide_{item['order']:02d}.{ext}"
        )
        await asyncio.to_thread(gcs.upload_local_file, item["rendered_path"], rendered_gs)

        entry: dict = {
            "kind": item["kind"],
            "order": item["order"],
            "rendered_gs": rendered_gs,
            "rendered_presigned": gcs.generate_presigned_url(rendered_gs, expiration_minutes=360),
        }

        if item["kind"] == "video" and item.get("thumb_path"):
            thumb_gs = (
                f"gs://{gcs._default_bucket}/"
                f"{bucket_prefix}/thumb_{item['order']:02d}.jpg"
            )
            await asyncio.to_thread(gcs.upload_local_file, item["thumb_path"], thumb_gs)
            entry["thumb_gs"] = thumb_gs
            entry["thumb_presigned"] = gcs.generate_presigned_url(thumb_gs, expiration_minutes=360)

        manifest.append(entry)

    return manifest
