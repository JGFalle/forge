"""Match scoring for discovery jobs — ranks each posting against the user's target profile.

Two-stage scoring:

  match_score  0-100  Metadata score (instant, no API)
    title_score    0-40  keyword alignment to target function/identity
    company_score  0-30  named target company tiers from config.yaml
    seniority      0-15  Senior Director > Director > Principal
    location       0-15  home metro > Remote > secondary cities > other
    salary_bonus    0-5  extra signal when strong comp is posted

  deep_score   0-100  Claude Haiku resume-vs-JD score (runs for match_score >= 40
                      jobs that have a description; blank otherwise)
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from utils.logging import get_logger

logger = get_logger(__name__)

# Ordered longest-first so longer phrases are matched and "consumed" before
# their component words get a chance to double-score.
_TITLE_HIGH = [
    ("supply chain transformation", 12),
    ("network optim",               12),
    ("supply chain strategy",       10),
    ("digital transformation",      10),
    ("operations strategy",          9),
    ("supply chain",                 8),
    ("automation",                   8),
    ("analytics",                    7),
    ("optimization",                  6),
    ("logistics",                    6),
    ("procurement",                  5),
    ("fulfillment",                  5),
    ("warehouse",                    4),
    ("distribution",                 4),
    ("inventory",                    3),
    ("planning",                     3),
    ("transformation",               3),
    ("operations",                   2),
]

# Pre-sort by keyword length descending so longer phrases claim their character
# spans before shorter sub-phrases can re-match the same text.
_TITLE_HIGH = sorted(_TITLE_HIGH, key=lambda kv: len(kv[0]), reverse=True)

def _load_tier_set(tier_key: str) -> list[str]:
    from utils.config import get
    companies = get(f"target_companies.{tier_key}", [])
    result = []
    for name in companies:
        lower = name.lower()
        result.append(lower)
        # add common abbreviation/variant if recognizable
        if " " in lower:
            result.append(lower.replace(" ", ""))
    return result


def _get_tier1() -> list[str]:
    return _load_tier_set("tier1")

def _get_tier2() -> list[str]:
    return _load_tier_set("tier2")

def _get_tier3() -> list[str]:
    return _load_tier_set("tier3")


def _load_location_signals() -> list[str]:
    from utils.config import get
    signals = []
    for loc in get("discovery.search_locations", []):
        city = loc.get("city", "").lower()
        if city:
            # take the city portion before the comma
            signals.append(city.split(",")[0].strip())
    return signals


def _load_search_cities() -> list[str]:
    from utils.config import get
    cities = []
    for loc in get("discovery.search_locations", []):
        city = loc.get("city", "").lower().split(",")[0].strip()
        cities.append(city)
    return cities[1:] if len(cities) > 1 else []  # first city is "home" metro


def score(job: dict) -> int:
    title   = job.get("title",        "").lower()
    company = job.get("company",      "").lower()
    loc     = job.get("location",     "").lower()
    salary  = job.get("salary_range", "")

    return min(100, (
        _title_score(title)
        + _company_score(company)
        + _seniority_score(title)
        + _location_score(loc)
        + _salary_bonus(salary)
    ))


def score_all(jobs: list[dict]) -> list[dict]:
    """Add match_score to each job and return sorted high-to-low."""
    for job in jobs:
        job["match_score"] = score(job)
    return sorted(jobs, key=lambda j: j["match_score"], reverse=True)


# ── Component scorers ─────────────────────────────────────────────────────────

def _title_score(title: str) -> int:
    pts = 5  # base
    claimed: list[tuple[int, int]] = []  # (start, end) spans already matched

    for keyword, value in _TITLE_HIGH:
        pos = title.find(keyword)
        if pos == -1:
            continue
        end = pos + len(keyword)
        # Skip if this span overlaps any already-claimed span
        if any(s < end and pos < e for s, e in claimed):
            continue
        claimed.append((pos, end))
        pts += value

    return min(40, pts)


def _company_score(company: str) -> int:
    for t1 in _get_tier1():
        if t1 in company:
            return 28
    for t2 in _get_tier2():
        if t2 in company:
            return 18
    for t3 in _get_tier3():
        if t3 in company:
            return 12
    return 0


def _seniority_score(title: str) -> int:
    if re.search(r"\b(senior\s+director|sr\.?\s+director)\b", title):
        return 15
    if re.search(r"\bdirector\b", title):
        return 10
    if "principal consultant" in title:
        return 8
    return 5


def _location_score(loc: str) -> int:
    home_signals = _load_location_signals()
    if any(s in loc for s in home_signals):
        return 15
    if "remote" in loc:
        return 10
    if any(s in loc for s in _load_search_cities()):
        return 8
    return 5


def _salary_bonus(salary: str) -> int:
    if not salary:
        return 0
    nums = re.findall(r"[\d]+(?:,[\d]+)*(?:\.\d+)?", salary.replace(",", ""))
    try:
        values = [float(n) for n in nums]
        # K-suffix already converted by normalizer — look for 5+ digit numbers
        annual = [v for v in values if v >= 10000]
        if not annual:
            # might be hourly
            hourly = [v for v in values if 10 <= v <= 500]
            annual = [v * 2080 for v in hourly]
        if not annual:
            return 0
        upper = max(annual)
        if upper >= 200_000:
            return 5
        if upper >= 175_000:
            return 3
        if upper >= 150_000:
            return 1
    except (ValueError, TypeError):
        pass
    return 0


# ── Deep score (Claude Haiku resume-vs-JD) ────────────────────────────────────

_DEEP_SCORE_THRESHOLD = 40   # only run for jobs at or above this match_score
_RESUME_TEXT: str | None = None  # cached once per process


def _load_resume_text() -> str:
    global _RESUME_TEXT
    if _RESUME_TEXT:
        return _RESUME_TEXT
    try:
        from docx import Document
        from utils.config import get
        resume_path = Path(get("paths.base_resume", "assets/base_resume_v9.docx"))
        if not resume_path.exists():
            resume_path = Path(__file__).parents[3] / "assets" / "base_resume_v9.docx"
        doc = Document(str(resume_path))
        _RESUME_TEXT = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as exc:
        logger.warning("Could not load base resume for deep scoring: %s", exc)
        _RESUME_TEXT = ""
    return _RESUME_TEXT


_DEEP_PROMPT = """\
You are evaluating a job opportunity for a specific candidate. Score the fit 0-100.

CANDIDATE RESUME:
{resume}

JOB DESCRIPTION:
Company: {company}
Title: {title}
Location: {location}
{description}

Score this opportunity for this candidate on a scale of 0-100 where:
  90-100 = Near-perfect fit — title, scope, industry, and seniority all align strongly
  70-89  = Strong fit — most criteria match, minor gaps
  50-69  = Moderate fit — clear relevant experience but meaningful gaps or misalignments
  30-49  = Stretch — some overlap but significant gap in seniority, domain, or scope
  0-29   = Poor fit — wrong function, level, or domain

Consider:
1. Does the role match the candidate's seniority level (Director/Sr Director)?
2. Is the functional domain aligned (supply chain, operations, analytics, transformation)?
3. Does the candidate's experience (ML/AI-driven ops, $65M automation program, network optimization) match key JD requirements?
4. Is the industry a natural fit?

Respond with ONLY a JSON object: {{"score": <integer 0-100>, "reason": "<one sentence>"}}"""


def deep_score(job: dict, resume_text: str) -> tuple[int, str]:
    """Call Claude Haiku to score resume vs JD description. Returns (score, reason)."""
    description = job.get("description", "")
    if not description or len(description) < 100:
        return 0, ""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
        prompt = _DEEP_PROMPT.format(
            resume=resume_text[:4000],
            company=job.get("company", ""),
            title=job.get("title", ""),
            location=job.get("location", ""),
            description=description[:3000],
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=128,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        import json as _json
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = _json.loads(raw)
        return int(data.get("score", 0)), str(data.get("reason", ""))
    except Exception as exc:
        logger.debug("Deep score failed for %s: %s", job.get("title", ""), exc)
        return 0, ""


def score_deep_all(jobs: list[dict]) -> list[dict]:
    """Run deep scoring for jobs above the threshold that have a description.

    Updates each job dict in-place with 'deep_score' key.
    Jobs below the threshold or without a description get deep_score = "".
    """
    resume_text = _load_resume_text()
    if not resume_text:
        logger.warning("Deep scoring skipped — could not load base resume text")
        for job in jobs:
            job.setdefault("deep_score", "")
        return jobs

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("Deep scoring skipped — ANTHROPIC_API_KEY not set")
        for job in jobs:
            job.setdefault("deep_score", "")
        return jobs

    candidates = [
        j for j in jobs
        if j.get("match_score", 0) >= _DEEP_SCORE_THRESHOLD
        and j.get("description", "")
    ]
    skipped = len(jobs) - len(candidates)
    logger.info(
        "Deep scoring: %d jobs eligible (≥%d match_score + description), %d skipped",
        len(candidates), _DEEP_SCORE_THRESHOLD, skipped,
    )

    for job in jobs:
        job.setdefault("deep_score", "")

    if not candidates:
        return jobs

    import concurrent.futures

    def _score_one(job: dict) -> tuple[str, int, str]:
        score, reason = deep_score(job, resume_text)
        return job["job_id"], score, reason

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        for job_id, score, reason in executor.map(_score_one, candidates):
            for job in candidates:
                if job["job_id"] == job_id:
                    job["deep_score"] = score if score else ""
                    break

    logger.info("Deep scoring complete for %d jobs", len(candidates))
    return jobs
