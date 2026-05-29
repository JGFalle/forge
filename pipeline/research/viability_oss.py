"""Open-source ghost job viability check.

No paid API needed. Three signal sources chained together:
  1. ddgs news search , layoff / hiring freeze headlines with dates
  2. Google News RSS  - feedparser for fresh structured results
  3. Wayback CDX API   - first snapshot date of the job URL (posting age)

Returns the same dict shape as viability_checker.check() so the caller
can drop this in as a transparent fallback.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from utils.logging import get_logger

load_dotenv(Path(".env"))
logger = get_logger(__name__)

_CDX_URL  = "https://web.archive.org/cdx/search/cdx"
_GNEWS_URL = "https://news.google.com/rss/search"
_TIMEOUT  = 8


def check(company: str, role: str, job_url: str = "") -> dict:
    """Run OSS ghost job assessment. Returns same shape as viability_checker.check()."""
    signals: list[str] = []
    positive: list[str] = []
    risk_score = 0  # 0-100; <30 low, 30-60 medium, >60 high

    # Signal 1: layoff / freeze news
    news_signals, news_positive, news_score = _check_news(company)
    signals.extend(news_signals)
    positive.extend(news_positive)
    risk_score += news_score

    # Signal 2: posting age via Wayback CDX
    if job_url:
        age_signal, age_score = _check_posting_age(job_url)
        if age_signal:
            if age_score > 0:
                signals.append(age_signal)
            else:
                positive.append(age_signal)
            risk_score += age_score

    # Score → verdict
    if risk_score >= 60:
        ghost_risk = "high"
        recommendation = "verify_before_applying"
        freshness = "stale"
    elif risk_score >= 30:
        ghost_risk = "medium"
        recommendation = "proceed_with_caution"
        freshness = "uncertain"
    else:
        ghost_risk = "low"
        recommendation = "proceed"
        freshness = "fresh"

    if not signals and not positive:
        positive.append(f"No layoff or hiring-freeze signals found for {company} in the last 90 days.")
        positive.append("OSS check: news scan and Wayback CDX returned no red flags.")

    return {
        "skipped": False,
        "ghost_risk": ghost_risk,
        "freshness_verdict": freshness,
        "signals": signals[:3],
        "positive_signals": positive[:3],
        "recommendation": recommendation,
        "source": "oss",
    }


def _check_news(company: str) -> tuple[list[str], list[str], int]:
    signals = []
    positive = []
    score = 0
    cutoff = date.today() - timedelta(days=90)

    layoff_terms = [
        f'"{company}" layoffs',
        f'"{company}" hiring freeze',
        f'"{company}" job cuts',
        f'"{company}" restructuring headcount',
    ]
    growth_terms = [
        f'"{company}" hiring',
        f'"{company}" expansion jobs',
    ]

    # ddgs news search
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            for q in layoff_terms[:2]:
                for r in ddgs.news(q, max_results=5, timelimit="m"):
                    pub = _parse_date(r.get("date", ""))
                    if pub and pub >= cutoff:
                        score += 25
                        signals.append(
                            f"Recent news ({pub.strftime('%b %d')}): {r['title'][:90]}"
                        )
                        break
                time.sleep(0.5)

            for q in growth_terms[:1]:
                for r in ddgs.news(q, max_results=3, timelimit="m"):
                    pub = _parse_date(r.get("date", ""))
                    if pub and pub >= cutoff:
                        positive.append(
                            f"Active hiring signals ({pub.strftime('%b %d')}): {r['title'][:90]}"
                        )
                        score = max(0, score - 10)
                        break
                time.sleep(0.5)

    except ImportError:
        logger.debug("ddgs not installed — skipping news search")
    except Exception as exc:
        logger.debug("ddgs news search failed: %s", exc)

    # Google News RSS via feedparser
    try:
        import feedparser
        for term in [f"{company} layoffs", f"{company} hiring freeze"]:
            url = f"{_GNEWS_URL}?q={requests.utils.quote(term)}&hl=en-US&gl=US&ceid=US:en"
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                pub = _parse_date(entry.get("published", ""))
                if pub and pub >= cutoff:
                    title = entry.get("title", "")[:90]
                    if not any(title in s for s in signals):
                        score += 15
                        signals.append(f"Google News ({pub.strftime('%b %d')}): {title}")
                    break
            time.sleep(0.8)
    except ImportError:
        logger.debug("feedparser not installed — skipping RSS check")
    except Exception as exc:
        logger.debug("feedparser RSS check failed: %s", exc)

    return signals[:3], positive[:2], min(score, 60)


def _check_posting_age(job_url: str) -> tuple[str, int]:
    """Check Wayback CDX for first snapshot of the job URL - reveals posting age."""
    try:
        params = {
            "url": job_url,
            "output": "json",
            "limit": 1,
            "fl": "timestamp",
            "filter": "statuscode:200",
        }
        resp = requests.get(_CDX_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if len(data) < 2:  # first row is header
            return "", 0

        ts = data[1][0]  # format: YYYYMMDDHHMMSS
        first_seen = datetime.strptime(ts[:8], "%Y%m%d").date()
        age_days = (date.today() - first_seen).days

        if age_days > 120:
            return (
                f"Wayback Machine: posting first crawled {first_seen} ({age_days} days ago — stale signal)",
                35,
            )
        if age_days > 60:
            return (
                f"Wayback Machine: posting first crawled {first_seen} ({age_days} days old — monitor)",
                15,
            )
        return (
            f"Wayback Machine: posting first crawled {first_seen} ({age_days} days ago — fresh)",
            -5,
        )

    except Exception as exc:
        logger.debug("Wayback CDX check failed: %s", exc)
        return "", 0


def _parse_date(date_str: str) -> date | None:
    if not date_str:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str[:25], fmt).date()
        except ValueError:
            continue
    # fallback: extract 4-digit year and hope for the best
    m = re.search(r"\d{4}-\d{2}-\d{2}", date_str)
    if m:
        try:
            return date.fromisoformat(m.group())
        except ValueError:
            pass
    return None
