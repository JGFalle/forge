"""JobSpy-based job board scraper.

Replaces SerpApi when SERPAPI_API_KEY is not set.
Scrapes Indeed, LinkedIn, ZipRecruiter, and Google Jobs via python-jobspy.
Returns the same normalized dict format as the other scrapers.

Install: pip install python-jobspy
GitHub: https://github.com/speedyapply/JobSpy
"""

from __future__ import annotations

import time
from pathlib import Path

from dotenv import load_dotenv

from utils.config import get
from utils.logging import get_logger

load_dotenv(Path(".env"))
logger = get_logger(__name__)


def scrape() -> list[dict]:
    try:
        from jobspy import scrape_jobs
    except ImportError:
        logger.warning("python-jobspy not installed — run: pip install python-jobspy")
        return []

    titles = get("discovery.target_titles", [])
    locations = get("discovery.search_locations", [])
    lookback = get("discovery.lookback_days", 7)
    results_per_search = 30

    if not titles:
        logger.warning("JobSpy scraper: no target_titles configured in config.yaml")
        return []

    # Boards to scrape — skip LinkedIn by default (aggressive rate limits without proxy)
    boards = ["indeed", "zip_recruiter", "google"]
    logger.info("JobSpy: scraping %s", boards)

    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    search_pairs = []
    for title in titles[:6]:        # cap title searches
        for loc in locations[:3]:   # cap locations
            search_pairs.append((title, loc.get("city", "")))
    # Remote pass
    if get("discovery.include_remote", True):
        search_pairs.append((titles[0], "Remote"))

    for title, location in search_pairs:
        try:
            df = scrape_jobs(
                site_name=boards,
                search_term=title,
                location=location,
                results_wanted=results_per_search,
                hours_old=lookback * 24,
                country_indeed="USA",
                verbose=0,
            )
            if df is None or df.empty:
                continue

            for _, row in df.iterrows():
                job_id = _make_id(row)
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                all_jobs.append({
                    "job_id":      job_id,
                    "title":       str(row.get("title", "") or ""),
                    "company":     str(row.get("company", "") or ""),
                    "location":    str(row.get("location", "") or ""),
                    "url":         str(row.get("job_url", "") or ""),
                    "description": str(row.get("description", "") or "")[:3000],
                    "salary":      _salary_str(row),
                    "date_posted": str(row.get("date_posted", "") or ""),
                    "source":      str(row.get("site", "jobspy") or "jobspy"),
                    "_source":     "jobspy",
                })

            logger.info("JobSpy: %s / %s → %d results", title, location, len(df))
            time.sleep(1.5)

        except Exception as exc:
            logger.warning("JobSpy search failed (%s / %s): %s", title, location, exc)
            time.sleep(2)

    logger.info("JobSpy total: %d unique jobs across all searches", len(all_jobs))
    return all_jobs


def _make_id(row) -> str:
    import hashlib
    key = f"{row.get('company','')}{row.get('title','')}{row.get('location','')}"
    return "jspy_" + hashlib.md5(key.encode()).hexdigest()[:12]


def _salary_str(row) -> str:
    parts = []
    for col in ("min_amount", "max_amount"):
        val = row.get(col)
        if val and str(val) not in ("nan", "None", ""):
            try:
                parts.append(f"${int(float(val)):,}")
            except (ValueError, TypeError):
                pass
    interval = str(row.get("interval", "") or "").lower()
    if parts:
        joined = " - ".join(parts)
        if interval == "hourly":
            return f"{joined}/hr"
        if interval in ("yearly", "annual"):
            return joined
        return joined
    return ""
