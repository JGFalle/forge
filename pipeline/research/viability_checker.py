"""Job viability assessment — runs before fit assessment and API calls.

Checks for ghost job signals and posting freshness using Perplexity for live
research and Claude haiku to structure the findings. Both gates (viability +
fit) must pass before tailoring credits are spent.

Returns gracefully with skipped=True if Perplexity is unavailable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

from utils.logging import get_logger

load_dotenv(Path(".env"))
logger = get_logger(__name__)

_STRUCTURE_PROMPT = """
You are evaluating whether a job posting is real or a ghost job based on live research findings.
Produce a structured JSON risk assessment. No explanation text — only JSON.

Research findings:
{research}

Return this exact structure:
{{
  "ghost_risk": "low|medium|high",
  "freshness_verdict": "active|stale|likely_filled|unknown",
  "signals": ["specific negative signal", "..."],
  "positive_signals": ["specific positive signal", "..."],
  "recommendation": "proceed|proceed_with_caution|verify_before_applying"
}}

Ghost risk definitions — apply these strictly:
- high: ONLY for definitively confirmed cases where THIS specific role cannot be filled:
    (a) a named individual has been publicly announced as hired into this role via press release or LinkedIn;
    (b) the company has explicitly announced a hiring freeze covering THIS function or business unit in the last 90 days;
    (c) the exact same posting has been recycled identically 3+ times over 6+ months with zero reported hires.
    General restructuring, TA team reductions, consulting practice changes, and industry headwinds are NOT high.
- medium: concerning but not definitive — restructuring affecting a related unit, TA/recruiting capacity reduced,
    posting appears 60-90+ days old, industry headwinds, vague or evergreen-style posting language.
- low: posting appears active and credible, company is actively hiring at this level, no meaningful red flags.

Multi-location postings are NOT a ghost signal — they often reflect geographic flexibility for travel-heavy
or stealth-remote roles. Only flag if the locations are incoherent with the company's operational footprint
or the count exceeds 8+ identical postings.

Keep each signal list to 3 items max. Be specific and cite dates or sources where available. No generic statements.
""".strip()


def check(company: str, role: str, job_url: str = "") -> dict:
    """
    Run job viability assessment.
    Returns result dict with ghost_risk, freshness_verdict, signals, recommendation.
    Falls back to OSS (ddgs + feedparser + Wayback CDX) when Perplexity is unavailable.
    """
    api_key = os.getenv("PERPLEXITY_API_KEY", "")
    if not api_key:
        logger.info("PERPLEXITY_API_KEY not set — using OSS viability check")
        try:
            from pipeline.research.viability_oss import check as oss_check
            return oss_check(company, role, job_url=job_url)
        except ImportError:
            logger.debug("viability_oss dependencies missing — skipping viability check")
            return _skipped()

    research = _fetch_viability_research(company, role, api_key)
    if not research:
        return _skipped()

    return _structure_findings(research, company)


def display(result: dict) -> None:
    """Print viability check result to terminal."""
    if result.get("skipped"):
        return

    risk = result.get("ghost_risk", "unknown")
    verdict = result.get("freshness_verdict", "unknown")
    rec = result.get("recommendation", "proceed")
    width = 60

    risk_icons = {"low": "✓", "medium": "!", "high": "✗"}
    icon = risk_icons.get(risk, "?")

    print(f"\n{'='*width}")
    print(f"  JOB VIABILITY   [{icon}] Ghost Risk: {risk.upper()}   Posting: {verdict.upper()}")
    print(f"{'='*width}")

    for s in result.get("signals", []):
        print(f"  [!] {s}")
    for p in result.get("positive_signals", []):
        print(f"  [+] {p}")

    if rec == "proceed_with_caution":
        print(f"\n  Heads up: some signals worth monitoring — see above.")
    elif rec == "verify_before_applying":
        print(f"\n  Recommend: verify this posting is still active before submitting.")
    print()


def should_block(result: dict) -> bool:
    """Return True if ghost_risk is high — pipeline should pause for user confirmation."""
    if result.get("skipped"):
        return False
    return result.get("ghost_risk") == "high"


def _fetch_viability_research(company: str, role: str, api_key: str) -> str:
    """Query Perplexity for live ghost job and freshness signals."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")

        query = (
            f"I am evaluating a job posting before applying. Company: {company}. Role: {role}. "
            f"Please research and answer these specific questions: "
            f"(1) Has {company} announced layoffs, hiring freezes, or significant restructuring "
            f"in the last 6 months? If yes, which functions were affected? "
            f"(2) Are there any signals this specific {role} role has already been filled — "
            f"such as a LinkedIn announcement, news article, or the posting being taken down and reposted? "
            f"(3) Is {company} currently actively hiring at Director level based on current "
            f"job board signals and LinkedIn activity? "
            f"(4) Any known ghost job patterns at {company} — roles posted repeatedly without hiring? "
            f"Be concise, cite dates, and flag any strong red flags clearly."
        )

        response = client.chat.completions.create(
            model="sonar-pro",
            messages=[{"role": "user", "content": query}],
            max_tokens=800,
        )
        result = response.choices[0].message.content or ""
        logger.info("Viability research fetched for %s (%d chars)", company, len(result))
        return result

    except Exception as exc:
        logger.warning("Perplexity viability fetch failed for %s: %s", company, exc)
        return ""


def _structure_findings(research: str, company: str) -> dict:
    """Use Claude haiku to structure Perplexity findings into a risk assessment JSON."""
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        return _skipped()

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)
        prompt = _STRUCTURE_PROMPT.format(research=research[:3000])
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        data["skipped"] = False
        return data

    except Exception as exc:
        logger.warning("Viability structuring failed: %s", exc)
        return _skipped()


def _skipped() -> dict:
    return {
        "ghost_risk": "unknown",
        "freshness_verdict": "unknown",
        "signals": [],
        "positive_signals": [],
        "recommendation": "proceed",
        "skipped": True,
    }
