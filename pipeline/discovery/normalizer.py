"""Normalize raw job dicts from any scraper into the common tracker schema."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pipeline.discovery.tracker import make_job_id, COLUMNS


def normalize(raw: dict[str, Any], source: str) -> dict[str, Any]:
    company = _clean(raw.get("company") or raw.get("employer") or "")
    title = _clean(raw.get("title") or raw.get("job_title") or "")
    location = _clean_location(raw.get("location") or "")
    url = _clean(raw.get("job_url") or raw.get("url") or raw.get("link") or "")
    salary = _extract_salary(raw)

    # description is not stored in the tracker CSV (no column for it) but is
    # passed through in-memory so the deep scorer can use it during the same run.
    description = _clean_description(raw.get("description") or "")

    return {
        "job_id": make_job_id(company, title, location),
        "date_found": datetime.now().strftime("%Y-%m-%d"),
        "company": company,
        "title": title,
        "location": location,
        "url": url,
        "source": source,
        "salary_range": salary,
        "description": description,
        "status": "TBD",
        "date_status_changed": datetime.now().strftime("%Y-%m-%d"),
        "notes": "",
    }


def normalize_many(raws: list[dict], source: str) -> list[dict]:
    out = []
    seen = set()
    for raw in raws:
        job = normalize(raw, source)
        if job["job_id"] not in seen and job["company"] and job["title"]:
            seen.add(job["job_id"])
            out.append(job)
    return out


def _clean(s: str) -> str:
    return " ".join(str(s).split()).strip()


def _clean_description(s: str) -> str:
    if not s or str(s).strip().lower() in ("nan", "none", ""):
        return ""
    # Collapse whitespace but preserve paragraph breaks as single newlines
    text = re.sub(r"\r\n|\r", "\n", str(s))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean_location(loc: str) -> str:
    loc = _clean(loc)
    loc = re.sub(r"\s*\(.*?\)", "", loc).strip()
    if not loc:
        return "Not specified"
    return loc


def _extract_salary(raw: dict) -> str:
    for field in ("salary_range", "salary", "min_salary", "compensation"):
        val = raw.get(field)
        if val and str(val).strip().lower() not in ("nan", "none", ""):
            return _clean(str(val))

    min_s = raw.get("min_amount")
    max_s = raw.get("max_amount")
    interval = raw.get("interval", "")

    def _is_valid(v) -> bool:
        if v is None:
            return False
        try:
            import math
            return not math.isnan(float(v))
        except (TypeError, ValueError):
            return bool(v)

    def fmt(v) -> str:
        n = float(v)
        if interval and "year" in str(interval).lower():
            return f"${n/1000:.0f}K"
        return f"${n:,.0f}"

    min_ok = _is_valid(min_s)
    max_ok = _is_valid(max_s)
    if min_ok and max_ok:
        return f"{fmt(min_s)}-{fmt(max_s)}"
    if min_ok or max_ok:
        return fmt(min_s if min_ok else max_s)

    desc = str(raw.get("description") or "")
    match = re.search(r"\$[\d,]+[Kk]?\s*[-–]\s*\$[\d,]+[Kk]?", desc)
    if match:
        return match.group(0)
    return ""
