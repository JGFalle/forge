"""Google for Jobs scraper via SerpApi, catches ATS-only postings not on Indeed/LinkedIn.

Requires SERPAPI_KEY in .env.
Sign up at serpapi.com, ~$50/month for the volume needed here.
"""

from __future__ import annotations

import os

import requests

from utils.config import get
from utils.logging import get_logger

logger = get_logger(__name__)

_SERPAPI_URL = "https://serpapi.com/search"

_SEARCH_TERMS = [
    "Director of Operations",
    "Senior Director Supply Chain",
    "Director Supply Chain Transformation",
    "Director Operations Strategy",
    "Director Network Optimization",
    "Director Digital Transformation Operations",
    "Director Supply Chain Operations",
    "Director Operations Analytics",
    "Director Automation",
    "Director Solutions Consulting supply chain",
    "Director Professional Services supply chain",
    "Principal Consultant Operations Transformation",
    "Principal Consultant Supply Chain",
]


def scrape() -> list[dict]:
    api_key = os.getenv("SERPAPI_KEY", "")
    if not api_key:
        logger.warning("SERPAPI_KEY not set — skipping Google for Jobs")
        return []

    locations = get("discovery.search_locations", [{"city": "Atlanta, GA"}])
    include_remote = get("discovery.include_remote", True)
    results = []
    seen_ids = set()

    def _add(jobs):
        for job in jobs:
            job_id = job.get("job_id") or job.get("title", "") + job.get("company_name", "")
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)
            results.append(job)

    for term in _SEARCH_TERMS:
        for loc in locations:
            city = loc["city"]
            query = f"{term} {city}"
            logger.info("SerpApi: '%s'", query)
            _add(_search(query, api_key))

        if include_remote:
            query = f"{term} remote"
            logger.info("SerpApi: '%s'", query)
            _add(_search(query, api_key))

    logger.info("SerpApi: %d raw results", len(results))
    return results


def _search(query: str, api_key: str) -> list[dict]:
    params = {
        "engine": "google_jobs",
        "q": query,
        "hl": "en",
        "api_key": api_key,
        "chips": "date_posted:week",
    }
    try:
        resp = requests.get(_SERPAPI_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("SerpApi error for '%s': %s", query, exc)
        return []

    raw_jobs = data.get("jobs_results", [])
    results = []
    for job in raw_jobs:
        url = _extract_url(job)
        results.append({
            "company": job.get("company_name", ""),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "url": url,
            "job_url": url,
            "job_id": job.get("job_id", ""),
            "salary_range": _extract_salary(job),
            "posted_date": job.get("detected_extensions", {}).get("posted_at", ""),
        })
    return results


def _extract_url(job: dict) -> str:
    for share in job.get("apply_options", []):
        link = share.get("link", "")
        if link:
            return link
    return job.get("share_link", "")


def _extract_salary(job: dict) -> str:
    ext = job.get("detected_extensions", {})
    salary = ext.get("salary", "") or ext.get("compensation", "")
    return salary or ""
