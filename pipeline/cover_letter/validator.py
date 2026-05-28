"""Cover letter quality gate — runs after generation, before the file goes to GDrive.

Checks:
  - Word count (hard max 160, warn at 150)
  - Em dash presence
  - AI-tell phrases
  - JD keyword coverage (at least 3 of the top JD terms should appear)
  - Minimum length (at least 3 sentences / 50 words)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from utils.text import word_count
from utils.logging import get_logger

logger = get_logger(__name__)

_EM_DASH_PATTERN = re.compile(r"—")

_AI_TELLS = [
    "delve", "delving",
    "landscape",
    "synergy", "synergies",
    "in today's rapidly evolving",
    "it's important to note",
    "let that sink in",
    "navigate the complexities",
    "foster", "fostering",
    "robust",
    "leverage", "leveraging",
    "stakeholders",
    "i am writing to express",
    "i am excited to apply",
    "i would be an excellent fit",
    "please find attached",
    "do not hesitate",
]

_SENTENCE_SPLIT = re.compile(r"[.!?]+")


def validate(cover_letter: str, jd_text: str = "", max_words: int = 150) -> dict:
    """
    Run quality checks on a cover letter string.

    Returns:
      grade        — "PASS" | "WARN" | "FAIL"
      word_count   — int
      issues       — list of {severity, message} dicts
      em_dashes    — list of positions found
      ai_tells     — list of phrases found
      sentence_count — int
    """
    issues = []
    wc = word_count(cover_letter)
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(cover_letter) if s.strip()]

    # Word count
    if wc > max_words:
        issues.append({
            "severity": "fail",
            "message": f"Word count {wc} exceeds {max_words}-word maximum — trim before submitting.",
        })
    elif wc > max_words - 10:
        issues.append({
            "severity": "warn",
            "message": f"Word count {wc} is close to the {max_words}-word limit.",
        })

    if wc < 50:
        issues.append({
            "severity": "fail",
            "message": f"Cover letter is only {wc} words — too short to be credible.",
        })

    # Em dashes
    em_positions = [m.start() for m in _EM_DASH_PATTERN.finditer(cover_letter)]
    if em_positions:
        issues.append({
            "severity": "fail",
            "message": f"{len(em_positions)} em dash(es) found — replace with commas, periods, or colons.",
        })

    # AI-tell phrases
    cl_lower = cover_letter.lower()
    found_tells = [phrase for phrase in _AI_TELLS if phrase in cl_lower]
    if found_tells:
        issues.append({
            "severity": "warn",
            "message": f"AI-tell phrase(s) detected: {', '.join(found_tells[:4])}",
        })

    # JD keyword coverage (only if JD text is provided)
    jd_coverage_pct = None
    if jd_text:
        jd_coverage_pct = _check_jd_coverage(cover_letter, jd_text)
        if jd_coverage_pct < 30:
            issues.append({
                "severity": "warn",
                "message": (
                    f"Low JD keyword coverage ({jd_coverage_pct}%) — "
                    "cover letter may not mirror JD language."
                ),
            })

    # Sentence count sanity
    if len(sentences) > 6:
        issues.append({
            "severity": "warn",
            "message": f"{len(sentences)} sentences detected — target is 4. Tighten.",
        })

    fail_count = sum(1 for i in issues if i["severity"] == "fail")
    warn_count = sum(1 for i in issues if i["severity"] == "warn")
    grade = "FAIL" if fail_count > 0 else ("WARN" if warn_count > 0 else "PASS")

    logger.info(
        "Cover letter validation: %s | %d words | %d issue(s)",
        grade, wc, len(issues),
    )
    return {
        "grade": grade,
        "word_count": wc,
        "sentence_count": len(sentences),
        "em_dashes": em_positions,
        "ai_tells": found_tells,
        "jd_coverage_pct": jd_coverage_pct,
        "issues": issues,
    }


def display_result(result: dict) -> None:
    """Print validation result inline with pipeline output."""
    grade = result["grade"]
    wc = result["word_count"]
    width = 60
    print(f"\n{'='*width}")
    print(f"  COVER LETTER CHECK   {wc} words  [{grade}]")
    print(f"{'='*width}")

    if not result["issues"]:
        print("  No issues — cover letter passes all checks.\n")
        return

    for issue in result["issues"]:
        icon = {"fail": "✗", "warn": "!"}.get(issue["severity"], "~")
        print(f"  [{icon}] {issue['message']}")
    print()


def save_result(result: dict, output_dir: Path, slug: str) -> Path:
    """Persist quality gate result as JSON alongside ATS and keyword gap reports."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"cover_quality_{slug}.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    logger.info("Cover quality report saved: %s", out)
    return out


def _check_jd_coverage(cover_letter: str, jd_text: str) -> int:
    """Return % of meaningful JD terms that appear in the cover letter."""
    stop = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "in", "is", "it", "of", "on", "or", "that", "the", "to",
        "was", "we", "will", "with", "you", "your",
    }
    jd_words = set(re.findall(r"[a-z][a-z0-9]{3,}", jd_text.lower()))
    jd_words -= stop
    if not jd_words:
        return 100
    cl_lower = cover_letter.lower()
    matched = sum(1 for w in jd_words if w in cl_lower)
    return round(matched / len(jd_words) * 100)
