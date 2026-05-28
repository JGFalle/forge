"""LinkedIn scraper via python-jobspy (jobs-guest public API, no login required)."""

from __future__ import annotations

from utils.config import get
from utils.logging import get_logger

logger = get_logger(__name__)

_SEARCH_TERMS = [
    "Director of Operations",
    "Senior Director of Operations",
    "Director Supply Chain Operations",
    "Director Supply Chain Transformation",
    "Director Operations Strategy",
    "Director Network Optimization",
    "Director Digital Transformation",
    "Director Operations Analytics",
    "Director Automation",
    "Principal Consultant Supply Chain",
    "Principal Consultant Operations",
]


def scrape() -> list[dict]:
    try:
        from jobspy import scrape_jobs
    except ImportError:
        logger.error("python-jobspy not installed")
        return []

    lookback = int(get("discovery.lookback_days", 7))
    locations = get("discovery.search_locations", [{"city": "Atlanta, GA", "radius": 50}])
    include_remote = get("discovery.include_remote", True)
    results = []
    seen_ids = set()

    def _run(term, location, distance=50, is_remote=False):
        try:
            df = scrape_jobs(
                site_name=["linkedin"],
                search_term=term,
                location=location,
                distance=distance,
                results_wanted=20,
                hours_old=lookback * 24,
                linkedin_fetch_description=False,
                is_remote=is_remote,
            )
            if df is None or df.empty:
                return
            for _, row in df.iterrows():
                job_id = str(row.get("id", ""))
                if job_id and job_id in seen_ids:
                    return
                if job_id:
                    seen_ids.add(job_id)
                results.append(row.to_dict())
        except Exception as exc:
            logger.warning("LinkedIn error '%s' @ %s: %s", term, location, exc)

    for term in _SEARCH_TERMS:
        for loc in locations:
            logger.info("LinkedIn: '%s' — %s (%s mi)", term, loc["city"], loc["radius"])
            _run(term, loc["city"], loc.get("radius", 50))
        if include_remote:
            logger.info("LinkedIn: '%s' — Remote (national)", term)
            _run(term, "United States", 0, is_remote=True)

    logger.info("LinkedIn: %d raw results", len(results))
    return results
