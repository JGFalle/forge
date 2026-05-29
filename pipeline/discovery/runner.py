"""Discovery runner, orchestrates all scrapers, normalizes, filters, and updates tracker.

Called daily by the 6am cron job, or manually via:
    pace --discover          # run scrapers + send digest
    pace --discover --quiet  # run scrapers, skip email
"""

from __future__ import annotations

import concurrent.futures
import logging
import time

from utils.logging import get_logger
from utils.progress import spinner, stage as show_stage, done as show_done
from pipeline.discovery import tracker, normalizer, fit_filter, digest, scorer

# Import scrapers at module level so their loggers exist before _quiet() is called
from pipeline.discovery.scrapers import indeed_scraper, linkedin_scraper, workday_scraper, serpapi_scraper, email_scraper

logger = get_logger(__name__)

_SCRAPER_LOGGERS = [
    "pipeline.discovery.scrapers.indeed_scraper",
    "pipeline.discovery.scrapers.linkedin_scraper",
    "pipeline.discovery.scrapers.workday_scraper",
    "pipeline.discovery.scrapers.serpapi_scraper",
    "pipeline.discovery.scrapers.email_scraper",
    "pipeline.discovery.tracker",
    "JobSpy",
    "jobspy",
    "JobSpy:Linkedin",   # colon-separated, not a child of JobSpy in Python logging
    "JobSpy:Indeed",
    "JobSpy:Glassdoor",
    "JobSpy:ZipRecruiter",
]

_SCRAPER_LABELS = {
    "indeed":   "Indeed",
    "linkedin": "LinkedIn",
    "workday":  "Workday",
    "serpapi":  "Google for Jobs",
    "email":    "Gmail Alerts",
}


def _quiet():
    # logging.disable() sets a global floor that blocks all records at or below
    # the given level - affects every logger regardless of individual level config,
    # including loggers created inside threads after this call (e.g. JobSpy:Linkedin).
    logging.disable(logging.INFO)


def _loud():
    logging.disable(logging.NOTSET)  # remove the global floor


def run(send_email: bool = True) -> dict:
    logger.info("=== PACE Discovery starting ===")
    print(f"\n{'─'*54}")
    print("  PACE Discovery — scanning job boards")
    print(f"{'─'*54}")

    auto_passed = tracker.auto_pass_stale()
    if auto_passed:
        logger.info("Auto-passed %d stale TBD jobs", auto_passed)

    # Stage 1: Scrape
    show_stage(1, "Scanning all job boards", total=3)
    _quiet()
    with spinner("Running all scrapers in parallel"):
        raw_results, timings = _run_all_scrapers()

    _SCRAPER_LABELS["jobspy"] = "JobSpy (OSS)"
    _SCRAPER_LABELS["ats"]    = "ATS Direct (OSS)"
    for src in _active_sources():
        count = sum(1 for r in raw_results if r.get("_source") == src)
        elapsed = timings.get(src, 0)
        label = _SCRAPER_LABELS.get(src, src)
        print(f"        {label:<22} {count:4d} raw  ({elapsed:.1f}s)")

    # Stage 2: Normalize, filter, score
    show_stage(2, "Normalizing + filtering + scoring", total=3)
    with spinner("Normalizing, deduplicating, scoring"):
        normalized = _normalize_all(raw_results)
        passed, rejected = fit_filter.filter_jobs(normalized)
        scored_jobs = scorer.score_all(passed)
        existing_ids = tracker.known_ids()
        new_jobs = [j for j in scored_jobs if j["job_id"] not in existing_ids]

    has_descriptions = sum(1 for j in new_jobs if j.get("description"))
    if has_descriptions:
        with spinner(f"Deep scoring {has_descriptions} jobs with descriptions (Haiku)"):
            new_jobs = scorer.score_deep_all(new_jobs)
    else:
        for job in new_jobs:
            job.setdefault("deep_score", "")

    added = tracker.append_new(new_jobs)

    summary = tracker.summary()

    # Stage 3: Digest
    email_sent = False
    if send_email and new_jobs:
        show_stage(3, "Sending digest email", total=3)
        with spinner("Sending digest email"):
            email_sent = digest.send(new_jobs, summary)
    elif not send_email:
        _print_digest(new_jobs, summary)

    show_done("Discovery complete", total=3)

    _loud()
    logger.info("Total raw results: %d", len(raw_results))
    logger.info("After fit filter: %d passed, %d rejected", len(passed), len(rejected))
    logger.info("New (not previously seen): %d", len(new_jobs))
    logger.info("Tracker summary: %s", summary)
    logger.info("=== PACE Discovery complete ===")
    return {"new": added, "summary": summary, "email_sent": email_sent}


def run_email_only() -> int:
    """Lightweight path for the 15-minute launchd interval.

    Pulls unread Gmail alert emails, normalizes + scores + filters, then appends
    any genuinely new jobs to the tracker CSV. No board scraping, no email digest.
    Designed to complete in under 10 seconds.
    """
    _quiet()
    try:
        raw = email_scraper.scrape()
        if not raw:
            return 0
        tagged = [dict(r, _source="email") for r in raw]
        normalized = _normalize_all(tagged)
        passed, _ = fit_filter.filter_jobs(normalized)
        scored = scorer.score_all(passed)
        existing = tracker.known_ids()
        new_jobs = [j for j in scored if j["job_id"] not in existing]
        new_jobs = scorer.score_deep_all(new_jobs)
        added = tracker.append_new(new_jobs)
    finally:
        _loud()

    if added:
        logger.info("Email-only check: %d new jobs appended", added)
    return added


def _active_sources() -> list[str]:
    """Choose which scrapers to run based on what API keys are configured."""
    import os
    sources = ["workday", "email"]

    if os.getenv("SERPAPI_API_KEY"):
        sources += ["indeed", "linkedin", "serpapi"]
    else:
        # OSS scrapers when SerpApi is not available
        sources += ["jobspy", "ats"]
        logger.info("SERPAPI_API_KEY not set — using OSS scrapers (JobSpy + ATS direct APIs)")

    return sources


def _run_all_scrapers() -> tuple[list[dict], dict[str, float]]:
    sources = _active_sources()

    def _timed(src: str) -> tuple[str, list[dict], float]:
        t0 = time.time()
        return src, _run_scraper(src), time.time() - t0

    all_results: list[dict] = []
    timings: dict[str, float] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        for src, results, elapsed in executor.map(_timed, sources):
            all_results.extend(results)
            timings[src] = elapsed

    return all_results, timings


def _run_scraper(source: str) -> list[dict]:
    # Lazy import OSS scrapers so missing packages don't break cloud mode
    if source == "jobspy":
        try:
            from pipeline.discovery.scrapers import jobspy_scraper
            results = jobspy_scraper.scrape()
            return [dict(r, _source="jobspy") for r in results]
        except ImportError:
            logger.warning("python-jobspy not installed — run: pip install python-jobspy")
            return []
        except Exception as exc:
            logger.error("JobSpy scraper failed: %s", exc)
            return []

    if source == "ats":
        try:
            from pipeline.discovery.scrapers import ats_scraper
            results = ats_scraper.scrape()
            return [dict(r, _source="ats") for r in results]
        except Exception as exc:
            logger.error("ATS scraper failed: %s", exc)
            return []

    _map = {
        "indeed":   indeed_scraper,
        "linkedin": linkedin_scraper,
        "workday":  workday_scraper,
        "serpapi":  serpapi_scraper,
        "email":    email_scraper,
    }
    module = _map.get(source)
    if not module:
        return []
    try:
        results = module.scrape()
        return [dict(r, _source=source) for r in results]
    except Exception as exc:
        logger.error("Scraper '%s' failed: %s", source, exc)
        return []


def _normalize_all(raw_results: list[dict]) -> list[dict]:
    seen_ids = set()
    normalized = []
    for raw in raw_results:
        source = raw.pop("_source", "unknown")
        job = normalizer.normalize(raw, source)
        if job["job_id"] not in seen_ids and job["company"] and job["title"]:
            seen_ids.add(job["job_id"])
            normalized.append(job)
    return normalized


def _print_digest(new_jobs: list[dict], summary: dict) -> None:
    print(f"\n{'─'*60}")
    print(f"  PACE Discovery — {len(new_jobs)} new opportunit{'y' if len(new_jobs)==1 else 'ies'}")
    print(f"{'─'*60}")
    for job in new_jobs:
        salary = f"  {job['salary_range']}" if job.get("salary_range") else ""
        print(f"  [{job['source'].upper():<8}] {job['company']} — {job['title']}")
        print(f"             {job['location']}{salary}")
        print(f"             {job['url']}")
    print(f"\n  Queue: {summary.get('TBD',0)} TBD | {summary.get('Queued',0)} Queued | {summary.get('Applied',0)} Applied\n")
