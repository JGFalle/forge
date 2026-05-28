"""Workday CXS API scraper — hits Tier 1 company career sites directly.

No anti-bot. Clean structured JSON from source. Each company has its own
Workday tenant slug configured in config.yaml under discovery.workday_tenants.
"""

from __future__ import annotations

import requests

from utils.config import get
from utils.logging import get_logger

logger = get_logger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}

_SEARCH_KEYWORDS = ["Director", "Senior Director"]


def scrape() -> list[dict]:
    tenants = get("discovery.workday_tenants", [])
    if not tenants:
        logger.warning("No Workday tenants configured")
        return []

    results = []
    for tenant_cfg in tenants:
        name = tenant_cfg.get("name", "")
        tenant = tenant_cfg.get("tenant", "")
        site = tenant_cfg.get("site", "wd1")
        slug = tenant_cfg.get("slug", "")
        if not tenant:
            continue
        for keyword in _SEARCH_KEYWORDS:
            jobs = _scrape_tenant(name, tenant, site, keyword, slug)
            results.extend(jobs)

    logger.info("Workday: %d raw results across all tenants", len(results))
    return results


def _scrape_tenant(company: str, tenant: str, site: str, keyword: str, slug: str = "") -> list[dict]:
    base = f"https://{tenant}.{site}.myworkdayjobs.com/wday/cxs/{tenant}"
    career_site = slug if slug else _find_career_site(base, tenant, site)
    if not career_site:
        return []

    url = f"{base}/{career_site}/jobs"
    payload = {
        "appliedFacets": {},
        "limit": 20,
        "offset": 0,
        "searchText": keyword,
    }

    try:
        resp = requests.post(url, json=payload, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Workday %s (%s): %s", company, keyword, exc)
        return []

    jobs = data.get("jobPostings", [])
    results = []
    for job in jobs:
        title = job.get("title", "")
        external_path = job.get("externalPath", "")
        job_url = f"https://{tenant}.{site}.myworkdayjobs.com/{career_site}{external_path}" if external_path else ""
        results.append({
            "company": company,
            "title": title,
            "location": _extract_location(job),
            "url": job_url,
            "job_url": job_url,
            "posted_date": job.get("postedOnDate", ""),
        })

    logger.info("Workday %s '%s': %d jobs", company, keyword, len(results))
    return results


def _find_career_site(base: str, tenant: str, site: str) -> str:
    common_slugs = [
        "CareerDepot", "coca-cola-careers", "Careers", "External",
        "External_Career_Site", "ExternalCareers", "GP_Careers",
        "americold-careers", "UPSjobs",
    ]
    for slug in common_slugs:
        url = f"{base}/{slug}/jobs"
        try:
            resp = requests.post(
                url,
                json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": "Director"},
                headers=_HEADERS,
                timeout=10,
            )
            if resp.status_code == 200:
                return slug
        except Exception:
            continue
    logger.warning("Could not find Workday career site slug for tenant '%s'", tenant)
    return ""


def _extract_location(job: dict) -> str:
    locs = job.get("locationsText", "") or job.get("location", "")
    if isinstance(locs, list):
        return ", ".join(locs)
    return str(locs)
