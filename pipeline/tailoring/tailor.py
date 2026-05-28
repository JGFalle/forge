"""Apply a tailoring JSON to the V9 base resume DOCX."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

from docx import Document

from pipeline.tailoring.docx_modifier import DocxModifier
from pipeline.tailoring.json_validator import load_json, validate_json
from utils.config import get

logger = logging.getLogger(__name__)


def run(
    tailoring_json_path: Path,
    output_dir: Path,
    dry_run: bool = False,
) -> Path | None:
    tailoring = load_json(tailoring_json_path)
    validate_json(tailoring)

    doc_bytes = _load_base_resume()
    doc = Document(doc_bytes)

    modifier = DocxModifier(doc, tailoring, dry_run=dry_run)
    modifier.apply_all()

    if dry_run:
        logger.info("DRY RUN complete — no files written.")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / tailoring["filename"]
    doc.save(out_path)
    logger.info("Tailored resume saved: %s", out_path)
    return out_path


def _load_base_resume() -> BytesIO:
    local_path = Path(get("paths.base_resume", "assets/base_resume_v9.docx"))
    if not local_path.is_absolute():
        local_path = Path(__file__).parent.parent.parent / local_path

    if local_path.exists():
        logger.info("Loading base resume from local file: %s", local_path)
        return BytesIO(local_path.read_bytes())

    logger.info("Local base resume not found; downloading from Google Drive.")
    from pipeline.tailoring.drive_client import DriveClient
    creds_dir = Path(__file__).parent.parent.parent / "config"
    drive = DriveClient(
        credentials_path=creds_dir / "credentials.json",
        token_path=creds_dir / "token.json",
    )
    file_id = get("google_docs.base_resume_v9", "")
    if not file_id:
        raise RuntimeError("No base resume file_id in config and no local file found.")
    return drive.download_base_resume(file_id)
