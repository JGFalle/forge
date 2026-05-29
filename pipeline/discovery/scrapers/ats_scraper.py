"""Direct ATS API scraper - Greenhouse, Lever, Ashby, Workable.

All of these expose fully public, unauthenticated job listing endpoints.
No API key. No rate limits worth worrying about at personal-pipeline volume.
Returns the same normalized dict format as the other scrapers.

Configure target ATS boards in config.yaml under oss.ats_boards:

  oss:
    ats_boards:
      - company: "Acme Corp"
        ats: "greenhouse"
        board_token: "acmecorp"
      - company: "Startup Inc"
        ats: "lever"
        board_token: "startupinc"

Board token is usually the slug in the company's careers URL:
  https://boards.greenhouse.io/{board_token}
  https://jobs.lever.co/{board_token}
  https://jobs.ashbyhq.com/{board_token}
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from utils.config import get
from utils.logging import get_logger

load_dotenv(Path(".env"))
logger = get_logger(__name__)

_TIMEOUT = 10

# ATS endpoint templates
_ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
    "lever":      "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby":      "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true",
    "workable":   "https://apply.workable.com/api/v1/widget/accounts/{token}/jobs",
    "recruitee":  "https://{token}.recruitee.com/api/offers",
}


def scrape() -> list[dict]:
    boards = get("oss.ats_boards", [])
    if not boards:
        logger.debug("ATS scraper: no oss.ats_boards configured — skipping")
        return []

    target_titles = [t.lower() for t in get("discovery.target_titles", [])]
    all_jobs: list[dict] = []

    for board in boards:
        company    = board.get("company", "")
        ats        = board.get("ats", "").lower()
        token      = board.get("board_token", "")

        if not all([company, ats, token]):
            logger.debug("ATS board entry missing fields: %s", board)
            continue

        if ats not in _ENDPOINTS:
            logger.warning("Unknown ATS type '%s' for %s — supported: %s", ats, company, list(_ENDPOINTS))
            continue

        jobs = _fetch(company, ats, token, target_titles)
        all_jobs.extend(jobs)
        logger.info("ATS %s (%s/%s): %d matching jobs", ats, company, token, len(jobs))
        time.sleep(0.5)

    logger.info("ATS scraper total: %d jobs", len(all_jobs))
    return all_jobs


def _fetch(company: str, ats: str, token: str, target_titles: list[str]) -> list[dict]:
    url = _ENDPOINTS[ats].format(token=token)
    try:
        resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("ATS fetch failed (%s / %s): %s", company, ats, exc)
        return []

    jobs = []
    raw_jobs = _extract_jobs(ats, data)

    for raw in raw_jobs:
        title = _field(raw, ats, "title")
        if target_titles and not any(kw in title.lower() for kw in target_titles):
            continue

        location  = _field(raw, ats, "location")
        url_apply = _field(raw, ats, "url")
        desc      = _field(raw, ats, "description")
        salary    = _field(raw, ats, "salary")
        posted    = _field(raw, ats, "date")

        job_id = "ats_" + hashlib.md5(f"{company}{title}{location}".encode()).hexdigest()[:12]

        jobs.append({
            "job_id":      job_id,
            "title":       title,
            "company":     company,
            "location":    location,
            "url":         url_apply,
            "description": desc[:3000],
            "salary":      salary,
            "date_posted": posted,
            "source":      ats,
            "_source":     "ats",
        })

    return jobs


def _extract_jobs(ats: str, data) -> list[dict]:
    if ats == "greenhouse":
        return data.get("jobs", [])
    if ats == "lever":
        return data if isinstance(data, list) else []
    if ats == "ashby":
        return data.get("jobPostings", [])
    if ats == "workable":
        return data.get("jobs", [])
    if ats == "recruitee":
        return data.get("offers", [])
    return []


def _field(raw: dict, ats: str, field: str) -> str:
    """Extract a normalized field from a raw ATS job dict."""
    if field == "title":
        return str(raw.get("title", "") or raw.get("name", "") or "")

    if field == "location":
        if ats == "greenhouse":
            return _gh_location(raw)
        if ats == "lever":
            return str(raw.get("categories", {}).get("location", "") or raw.get("workplaceType", ""))
        if ats == "ashby":
            locs = raw.get("locationNames", [])
            return ", ".join(locs) if locs else str(raw.get("isRemote") and "Remote" or "")
        if ats == "workable":
            return str(raw.get("location", {}).get("city", "") or "")
        if ats == "recruitee":
            return str(raw.get("city", "") or "")
        return ""

    if field == "url":
        if ats == "greenhouse":
            return str(raw.get("absolute_url", ""))
        if ats == "lever":
            return str(raw.get("hostedUrl", ""))
        if ats == "ashby":
            return f"https://jobs.ashbyhq.com/{raw.get('organizationName','')}/{raw.get('id','')}"
        if ats == "workable":
            return str(raw.get("url", ""))
        if ats == "recruitee":
            return str(raw.get("careers_url", ""))
        return ""

    if field == "description":
        if ats == "greenhouse":
            return _strip_html(str(raw.get("content", "") or ""))
        if ats == "lever":
            lists = raw.get("lists", [])
            return " ".join(str(l.get("content", "")) for l in lists)
        if ats == "ashby":
            return _strip_html(str(raw.get("descriptionHtml", "") or raw.get("description", "") or ""))
        if ats in ("workable", "recruitee"):
            return _strip_html(str(raw.get("description", "") or ""))
        return ""

    if field == "salary":
        if ats == "ashby":
            comp = raw.get("compensation", {})
            if comp:
                lo = comp.get("minValue", "")
                hi = comp.get("maxValue", "")
                if lo or hi:
                    return f"${int(lo):,} - ${int(hi):,}" if lo and hi else str(lo or hi)
        if ats == "lever":
            return str(raw.get("salaryRange", {}).get("min", "") or "")
        return ""

    if field == "date":
        if ats == "greenhouse":
            return str(raw.get("updated_at", ""))[:10]
        if ats == "lever":
            ts = raw.get("createdAt", 0)
            if ts:
                from datetime import datetime
                return datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        if ats == "ashby":
            return str(raw.get("publishedDate", ""))[:10]
        return ""

    return ""


def _gh_location(raw: dict) -> str:
    locs = raw.get("offices", [])
    if locs:
        return ", ".join(str(l.get("name", "")) for l in locs if l.get("name"))
    loc = raw.get("location", {})
    if isinstance(loc, dict):
        return str(loc.get("name", ""))
    return str(loc or "")


def _strip_html(text: str) -> str:
    import re
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s{2,}", " ", clean)
    return clean.strip()
