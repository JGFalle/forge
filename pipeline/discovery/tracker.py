"""Discovery queue tracker — single source of truth CSV in Google Drive.

Schema (columns in order):
  job_id               md5 hash of (company_lower + title_normalized_lower + location_lower)
  date_found           YYYY-MM-DD
  company              employer name
  title                job title as posted
  location             city/state or Remote
  url                  direct link to job posting
  source               indeed | linkedin | workday | serpapi
  salary_range         "$175K–$210K" or "" if unknown
  status               TBD | Queued | Applied | Passed
  date_status_changed  YYYY-MM-DD of last status change
  notes                free text

The file lives in Google Drive and is opened/edited in Google Sheets by the user.
Python reads and appends to it via the local GDrive mount.
"""

from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from utils.config import get
from utils.logging import get_logger

logger = get_logger(__name__)

COLUMNS = [
    "job_id",
    "date_found",
    "company",
    "title",
    "location",
    "url",
    "source",
    "salary_range",
    "match_score",
    "deep_score",
    "status",
    "date_status_changed",
    "notes",
]

STATUS_TBD = "TBD"
STATUS_QUEUED = "Queued"
STATUS_APPLIED = "Applied"
STATUS_PASSED = "Passed"


def _tracker_path() -> Path:
    raw = get("discovery.tracker_csv", "")
    return Path(raw).expanduser()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def make_job_id(company: str, title: str, location: str) -> str:
    key = f"{company.lower().strip()}|{_normalize_title(title).lower()}|{location.lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _normalize_title(title: str) -> str:
    t = title.strip()
    t = t.replace("Sr.", "Senior").replace("Sr ", "Senior ")
    return t


def load() -> list[dict]:
    path = _tracker_path()
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def known_ids() -> set[str]:
    return {row["job_id"] for row in load()}


def queued_jobs() -> list[dict]:
    return [r for r in load() if r.get("status") == STATUS_QUEUED]


def append_new(jobs: list[dict[str, Any]]) -> int:
    """Append jobs that are not already tracked. Returns count of new rows added."""
    if not jobs:
        return 0

    path = _tracker_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = known_ids()
    new_rows = [j for j in jobs if j["job_id"] not in existing]
    if not new_rows:
        return 0

    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in new_rows:
            row.setdefault("status", STATUS_TBD)
            row.setdefault("date_status_changed", _today())
            row.setdefault("notes", "")
            writer.writerow(row)

    logger.info("Appended %d new jobs to tracker", len(new_rows))
    return len(new_rows)


def auto_pass_stale() -> int:
    """Set status=Passed on TBD rows older than stale_tbd_days. Returns count changed."""
    rows = load()
    stale_days = int(get("discovery.stale_tbd_days", 21))
    cutoff = datetime.now() - timedelta(days=stale_days)
    changed = 0
    for row in rows:
        if row.get("status") != STATUS_TBD:
            continue
        try:
            found = datetime.strptime(row["date_found"], "%Y-%m-%d")
        except ValueError:
            continue
        if found < cutoff:
            row["status"] = STATUS_PASSED
            row["date_status_changed"] = _today()
            row["notes"] = (row.get("notes") or "") + " [auto-passed: stale]"
            changed += 1

    if changed:
        _rewrite(rows)
        logger.info("Auto-passed %d stale TBD jobs", changed)
    return changed


def _rewrite(rows: list[dict]) -> None:
    path = _tracker_path()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def summary() -> dict:
    rows = load()
    counts = {STATUS_TBD: 0, STATUS_QUEUED: 0, STATUS_APPLIED: 0, STATUS_PASSED: 0}
    for row in rows:
        s = row.get("status", STATUS_TBD)
        if s in counts:
            counts[s] += 1
    return counts
