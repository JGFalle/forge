"""Create and manage the application folder structure for each job."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

from utils.config import get
from utils.logging import get_logger

logger = get_logger(__name__)


def make_slug(company: str, role: str) -> str:
    combined = f"{company}_{role}".lower()
    slug = re.sub(r"[^a-z0-9]+", "_", combined).strip("_")
    return slug[:60]


def create_application_folder(company: str, role: str, base_dir: Path | None = None) -> Path:
    if base_dir is None:
        base_dir = Path(get("paths.outputs_dir", "outputs"))

    slug = make_slug(company, role)
    date_str = datetime.now().strftime("%Y-%m-%d")
    folder = base_dir / f"{date_str}_{slug}"
    folder.mkdir(parents=True, exist_ok=True)

    for sub in ["resume", "cover_letter", "people_intel", "tailoring_json", "research", "jd"]:
        (folder / sub).mkdir(exist_ok=True)

    logger.info("Application folder created: %s", folder)
    return folder


def copy_file(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.copy2(src, dest)
    logger.info("Copied %s -> %s", src.name, dest)
    return dest


def sync_to_gdrive(app_folder: Path, company: str) -> None:
    mount = Path(get("gdrive.mount_base", "")).expanduser()
    applications = get("gdrive.applications_folder", "Job Search HQ/Applications")
    gdrive_base = mount / applications / company

    if not mount.exists():
        logger.warning("Google Drive mount not found: %s", mount)
        return

    gdrive_base.mkdir(parents=True, exist_ok=True)
    errors = 0
    for f in app_folder.rglob("*"):
        if f.is_file():
            rel = f.relative_to(app_folder)
            dest = gdrive_base / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(f, dest)
            except Exception as e:
                logger.warning("GDrive copy failed: %s -> %s: %s", f.name, dest, e)
                errors += 1

    if errors:
        logger.warning("GDrive sync finished with %d error(s).", errors)
    else:
        logger.info("GDrive sync complete for %s.", company)
