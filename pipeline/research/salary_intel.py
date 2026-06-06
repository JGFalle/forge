"""Salary intelligence, infer comp band when the JD does not post compensation.

Uses Perplexity Sonar to query live salary data from Levels.fyi, LinkedIn Salary,
Glassdoor, and state pay transparency disclosures. Returns empty string gracefully
if PERPLEXITY_API_KEY is not set or the query fails.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from utils.logging import get_logger

load_dotenv(Path(".env"))
logger = get_logger(__name__)


def fetch_salary_intel(company: str, role: str, location: str = "") -> dict:
    """
    Return salary intelligence for a role at a company.

    Result keys:
      estimated_range  - short string: "$185K-$215K base" or "" if unavailable
      confidence       - "high" | "medium" | "low" | "unknown"
      source_note      - one-line attribution
      raw              - full Perplexity response (for saving to research/)

    Returns empty strings / "unknown" on failure.
    """
    api_key = os.getenv("PERPLEXITY_API_KEY", "")
    if not api_key:
        logger.info("PERPLEXITY_API_KEY not set; using OSS salary intel (BLS + H-1B LCA)")
        try:
            from pipeline.research.salary_oss import fetch as oss_fetch
            result = oss_fetch(company, role, location)
            return {
                "estimated_range": result.get("estimated_range", ""),
                "confidence":      result.get("confidence", "low"),
                "source_note":     result.get("source_note", ""),
                "raw":             result.get("raw_text", ""),
            }
        except ImportError:
            logger.debug("salary_oss dependencies missing; skipping salary intel")
            return _empty()

    loc_fragment = f" based in {location}" if location and location != "Not specified" else ""
    query = (
        f"What is the base salary range for a {role} at {company}{loc_fragment}? "
        f"Search Levels.fyi, LinkedIn Salary, Glassdoor, Blind, and any state pay transparency "
        f"disclosures or public job postings from the last 12 months. "
        f"Respond with: (1) the estimated base salary range as a short range string like '$175K–$210K', "
        f"(2) your confidence level (high/medium/low) based on data availability, "
        f"(3) the primary data sources found. "
        f"If this is a Director or Sr Director level role, focus on that band. "
        f"Keep the entire response under 150 words."
    )

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")
        response = client.chat.completions.create(
            model="sonar-pro",
            messages=[{"role": "user", "content": query}],
            max_tokens=300,
        )
        raw = response.choices[0].message.content or ""
        logger.info("Salary intel fetched for %s / %s (%d chars)", company, role, len(raw))
        return _parse_response(raw)

    except Exception as exc:
        logger.warning("Salary intel fetch failed for %s / %s: %s", company, role, exc)
        return _empty()


def _parse_response(raw: str) -> dict:
    """Extract the range string, confidence, and source note from Perplexity's response."""
    import re

    # Try to pull a salary range pattern: $XXX-$XXX or $XXXK-$XXXK
    range_match = re.search(
        r"\$[\d,]+[KkMm]?\s*[–\-—to]+\s*\$[\d,]+[KkMm]?",
        raw,
        re.IGNORECASE,
    )
    estimated_range = range_match.group(0).strip() if range_match else ""

    # Normalize separators
    if estimated_range:
        estimated_range = re.sub(r"\s*[—-]+\s*", "–", estimated_range)

    # Pull confidence
    confidence = "unknown"
    for level in ("high", "medium", "low"):
        if level in raw.lower():
            confidence = level
            break

    # Source note: first line that mentions a known source
    sources = ["levels.fyi", "linkedin", "glassdoor", "blind", "pay transparency", "job posting"]
    source_note = ""
    for line in raw.split("\n"):
        line_l = line.lower()
        if any(s in line_l for s in sources):
            source_note = line.strip()
            break
    if not source_note and estimated_range:
        source_note = "Perplexity market estimate"

    return {
        "estimated_range": estimated_range,
        "confidence": confidence,
        "source_note": source_note,
        "raw": raw,
    }


def display_salary_intel(intel: dict, posted_salary: str) -> None:
    """Print salary intel inline with fit assessment output."""
    if posted_salary and posted_salary not in ("Not listed", "Not specified", ""):
        return  # JD posted comp, no need to print estimate

    if not intel.get("estimated_range"):
        return

    conf_label = {"high": "high confidence", "medium": "est.", "low": "rough est.", "unknown": "est."}.get(
        intel.get("confidence", "unknown"), "est."
    )
    print(f"  Comp (market):        {intel['estimated_range']} base  [{conf_label}]")
    if intel.get("source_note"):
        print(f"  Source:               {intel['source_note'][:80]}")


def save_salary_intel(intel: dict, company: str, role: str, output_dir: Path) -> Path | None:
    """Save full salary intel to research/ folder."""
    if not intel.get("raw"):
        return None
    output_dir.mkdir(parents=True, exist_ok=True)

    import re
    slug = re.sub(r"[^a-z0-9]+", "_", f"{company}_{role}".lower()).strip("_")[:60]
    out = output_dir / f"salary_intel_{slug}.txt"
    lines = [
        f"SALARY INTELLIGENCE: {company} / {role}",
        f"Estimated range: {intel.get('estimated_range', 'N/A')}",
        f"Confidence: {intel.get('confidence', 'unknown')}",
        f"Source note: {intel.get('source_note', '')}",
        "",
        "--- FULL PERPLEXITY RESPONSE ---",
        intel.get("raw", ""),
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def _empty() -> dict:
    return {"estimated_range": "", "confidence": "unknown", "source_note": "", "raw": ""}
