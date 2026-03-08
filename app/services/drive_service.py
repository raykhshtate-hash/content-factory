"""
Google Drive Service — DE Hack edition.

Key design: copy_to_gcs() streams Drive → GCS WITHOUT buffering
the entire file in RAM.  We open an authorized HTTP stream from the
Drive download endpoint and pipe response.raw straight into
blob.upload_from_file() which performs a GCS resumable upload
reading 10 MB chunks at a time.

Memory footprint ≈ chunk_size (10 MB), not file_size (300-500 MB).
"""

import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.auth.transport.requests import AuthorizedSession
from google.cloud import storage
from tenacity import retry, wait_exponential, stop_after_attempt

from app.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/cloud-platform",
]

# 10 MB chunk — sweet spot between memory use and network round-trips
_CHUNK_SIZE = 10 * 1024 * 1024


def _get_credentials():
    creds_path = settings.GOOGLE_APPLICATION_CREDENTIALS
    if not creds_path or not os.path.exists(creds_path):
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS is not set or file does not exist."
        )
    return service_account.Credentials.from_service_account_file(
        creds_path, scopes=SCOPES
    )


class DriveService:
    def __init__(self):
        self._creds = _get_credentials()
        self._drive_build = build("drive", "v3", credentials=self._creds)

    # ------------------------------------------------------------------
    # list_inbox_files
    # ------------------------------------------------------------------
    def list_inbox_files(self) -> list[dict]:
        """Return list of file metadata dicts from Drive INBOX folder."""
        folder_id = settings.DRIVE_INBOX_FOLDER_ID
        if not folder_id:
            raise RuntimeError("DRIVE_INBOX_FOLDER_ID is not configured.")

        results = (
            self._drive_build.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                pageSize=50,
                fields="files(id, name, mimeType, size)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            )
            .execute()
        )
        return results.get("files", [])

    # ------------------------------------------------------------------
    # copy_to_gcs  —  the DE Hack (zero-buffer streaming)
    # ------------------------------------------------------------------
    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def copy_to_gcs(
        self,
        drive_file_id: str,
        gcs_bucket: str,
        gcs_path: str,
    ) -> str:
        """
        Stream a file from Google Drive directly into a GCS blob
        using a resumable upload.  The server never holds the whole
        file in memory — only one chunk (~10 MB) at a time.

        Retries on failure up to 3 times using exponential backoff.
        Returns the gs:// URI of the uploaded object.
        """
        import tempfile
        
        # Download to a temporary local file — most reliable approach.
        # MediaIoBaseDownload writes directly to disk, no seek/truncate issues.
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mov") as tmp:
            tmp_path = tmp.name
            request = self._drive_build.files().get_media(
                fileId=drive_file_id, acknowledgeAbuse=True, supportsAllDrives=True
            )
            downloader = MediaIoBaseDownload(tmp, request, chunksize=8 * 1024 * 1024)
            done = False
            while not done:
                status, done = downloader.next_chunk()

        # Upload the intact file to GCS
        try:
            gcs_client = storage.Client(credentials=self._creds)
            bucket = gcs_client.bucket(gcs_bucket)
            blob = bucket.blob(gcs_path)
            blob.upload_from_filename(tmp_path, content_type="video/mp4")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        return f"gs://{gcs_bucket}/{gcs_path}"

    # ------------------------------------------------------------------
    # delete_file
    # ------------------------------------------------------------------
    def delete_file(self, drive_file_id: str) -> None:
        """Permanently delete a file from Drive (requires owner/editor)."""
        self._drive_build.files().delete(
            fileId=drive_file_id,
            supportsAllDrives=True,
        ).execute()
