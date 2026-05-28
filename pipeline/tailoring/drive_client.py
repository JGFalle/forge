"""Google Drive client for downloading the base resume and uploading outputs."""

from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


class DriveClient:
    def __init__(self, credentials_path: Path, token_path: Path):
        self.service = self._authenticate(credentials_path, token_path)

    def _authenticate(self, creds_path: Path, token_path: Path):
        creds = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired Google Drive token.")
                creds.refresh(Request())
            else:
                if not creds_path.exists():
                    logger.error("credentials.json not found: %s", creds_path)
                    sys.exit(1)
                logger.info("Running Google OAuth flow in browser.")
                flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
                creds = flow.run_local_server(port=0)
            token_path.write_text(creds.to_json(), encoding="utf-8")
        logger.info("Google Drive authenticated.")
        return build("drive", "v3", credentials=creds)

    def download_base_resume(self, file_id: str, local_path: Path | None = None) -> io.BytesIO:
        if local_path and local_path.exists():
            logger.info("Loading base resume from local file: %s", local_path)
            return io.BytesIO(local_path.read_bytes())

        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        logger.info("Downloading base resume from Drive (fileId=%s).", file_id)
        try:
            request = self.service.files().export_media(fileId=file_id, mimeType=mime)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            buffer.seek(0)
            return buffer
        except HttpError as e:
            logger.error("Failed to download base resume: %s", e)
            sys.exit(1)

    def upload_file(self, local_path: Path, filename: str, folder_id: str) -> str:
        file_metadata = {"name": filename, "parents": [folder_id]}
        media = MediaFileUpload(
            str(local_path),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            resumable=True,
        )
        try:
            request = self.service.files().create(body=file_metadata, media_body=media, fields="id")
            response = None
            while response is None:
                _, response = request.next_chunk()
            file_id = response.get("id", "")
            logger.info("Uploaded %s to Drive (id=%s).", filename, file_id)
            return file_id
        except HttpError as e:
            logger.error("Failed to upload %s: %s", filename, e)
            return ""
