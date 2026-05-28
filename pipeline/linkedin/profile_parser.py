"""Parse LinkedIn data export into a structured profile dict.

LinkedIn data export (Settings → Data Privacy → Get a copy of your data)
produces a ZIP file with CSVs. Key files used: Profile.csv, Positions.csv,
Skills.csv, Education.csv.

Accepts either the raw .zip or an already-unzipped directory.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import Any


def parse_export(path: Path) -> dict[str, Any]:
    """Parse LinkedIn data export ZIP or directory into a profile dict."""
    if path.suffix.lower() == ".zip":
        return _parse_zip(path)
    if path.is_dir():
        return _parse_dir(path)
    raise ValueError(f"Expected a .zip file or directory, got: {path}")


def _parse_zip(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()

        def read(filename: str) -> str:
            matches = [n for n in names if n.endswith(filename)]
            if not matches:
                return ""
            return zf.read(matches[0]).decode("utf-8", errors="replace")

        return _parse_csvs(read)


def _parse_dir(path: Path) -> dict[str, Any]:
    def read(filename: str) -> str:
        matches = list(path.rglob(filename))
        if not matches:
            return ""
        return matches[0].read_text(encoding="utf-8", errors="replace")

    return _parse_csvs(read)


def _parse_csvs(read_fn) -> dict[str, Any]:
    profile = _parse_profile_csv(read_fn("Profile.csv"))
    positions = _parse_positions_csv(read_fn("Positions.csv"))
    skills = _parse_skills_csv(read_fn("Skills.csv"))
    education = _parse_education_csv(read_fn("Education.csv"))
    return {
        "headline": profile.get("headline", ""),
        "about": profile.get("summary", ""),
        "positions": positions,
        "skills": skills,
        "education": education,
    }


def _csv_rows(text: str) -> list[dict]:
    if not text.strip():
        return []
    # LinkedIn exports sometimes have a BOM
    text = text.lstrip("﻿")
    return list(csv.DictReader(io.StringIO(text)))


def _parse_profile_csv(text: str) -> dict:
    rows = _csv_rows(text)
    if not rows:
        return {}
    row = rows[0]
    return {
        "headline": row.get("Headline", "").strip(),
        "summary": row.get("Summary", "").strip(),
    }


def _parse_positions_csv(text: str) -> list[dict]:
    rows = _csv_rows(text)
    return [
        {
            "company": r.get("Company Name", "").strip(),
            "title": r.get("Title", "").strip(),
            "description": r.get("Description", "").strip(),
            "location": r.get("Location", "").strip(),
            "started_on": r.get("Started On", "").strip(),
            "finished_on": r.get("Finished On", "").strip(),
        }
        for r in rows
        if r.get("Company Name", "").strip()
    ]


def _parse_skills_csv(text: str) -> list[str]:
    rows = _csv_rows(text)
    return [r.get("Name", "").strip() for r in rows if r.get("Name", "").strip()]


def _parse_education_csv(text: str) -> list[dict]:
    rows = _csv_rows(text)
    return [
        {
            "school": r.get("School Name", "").strip(),
            "degree": r.get("Degree Name", "").strip(),
            "start_date": r.get("Start Date", "").strip(),
            "end_date": r.get("End Date", "").strip(),
        }
        for r in rows
        if r.get("School Name", "").strip()
    ]


def summarize(profile: dict[str, Any]) -> str:
    """Return a human-readable summary of parsed profile for logging/debug."""
    lines = [
        f"Headline:   {profile['headline'][:80] or '(none)'}",
        f"About:      {len(profile['about'])} chars",
        f"Positions:  {len(profile['positions'])}",
        f"Skills:     {len(profile['skills'])}",
        f"Education:  {len(profile['education'])}",
    ]
    return "\n".join(lines)
