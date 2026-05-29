"""Open-source model council using Groq, Google Gemini, and/or Ollama.

Replaces the Perplexity sonar/sonar-pro/sonar-reasoning panel with three
genuinely different open-source model families to preserve architectural
diversity. Uses whichever providers are configured/available.

Same output shape as summary_council.run() so the caller is transparent to
which backend ran.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from utils.config import get
from utils.logging import get_logger
from utils.oss_llm import generate_multi, generate as llm_generate, available_provider

logger = get_logger(__name__)

# ── Panel prompts ──────────────────────────────────────────────────────────────

_PANEL_PROMPT = """\
Review this resume summary and cover letter against the job description.
Your only job: identify specific problems. Do NOT rewrite anything.

TARGET ROLE: {role} at {company}

JD (source of truth):
{jd_excerpt}

JD TERMS NOT YET IN THE DRAFT:
{missing_keywords}

APPLICATION CONTEXT:
{application_context}

SUMMARY DRAFT:
{summary}

COVER LETTER DRAFT:
{cover_letter}

Flag issues in these categories ONLY if genuinely present:
1. FABRICATION — any claim not supported by the JD or the draft itself
2. JD MISMATCH — strong JD keywords absent from the draft
3. TONE — em dashes, AI-tell phrases (delve/synergy/landscape/etc.), or >3-sentence paragraphs
4. STRUCTURE — summary >500 chars, cover letter >150 words
5. YEARS/FIGURES — experience years inflated, same dollar figure repeated

Under 200 words. List issues only. No rewrites, no praise."""

_AGGREGATOR_PROMPT = """\
You are applying surgical edits to a resume summary and cover letter.

TARGET ROLE: {role} at {company}

ORIGINAL JD:
{jd_excerpt}

ORIGINAL SUMMARY (Claude-generated, do not touch anything not flagged):
{summary}

ORIGINAL COVER LETTER (do not touch anything not flagged):
{cover_letter}

PANEL FINDINGS (3 independent reviewers):
{panel_findings}

RULES — read carefully:
- Apply ONLY changes that fix a flagged issue. Every unflagged word stays exactly as-is.
- Never add claims not present in the JD or the original draft.
- Never inflate years of experience.
- No em dashes in output.
- Summary must stay under {summary_max} chars.
- Cover letter must stay under {cover_max} words.

Return ONLY a JSON object with these fields:
{{
  "revised_summary": "...",
  "revised_cover_letter": "...",
  "changes_made": ["change 1", "change 2"],
  "changes_skipped": ["reason for skipping panel suggestion"]
}}"""


# ── Public API ────────────────────────────────────────────────────────────────

def run(
    company: str,
    role: str,
    summary: str,
    cover_letter: str,
    jd_excerpt: str,
    missing_keywords: list[str],
    application_context: str = "",
) -> dict:
    """
    Run the OSS council. Returns same shape as summary_council.run():
      revised_summary, revised_cover_letter, changes_made, panel_findings, skipped
    """
    if not available_provider():
        logger.info("No OSS LLM available — skipping council")
        return _skipped()

    panel_prompt = _PANEL_PROMPT.format(
        company=company,
        role=role,
        jd_excerpt=jd_excerpt[:3000],
        missing_keywords=", ".join(missing_keywords[:20]) if missing_keywords else "none identified",
        application_context=application_context[:500] or "Standard application.",
        summary=summary,
        cover_letter=cover_letter,
    )

    # ── Stage 1: Parallel panel calls ─────────────────────────────────────────
    logger.info("OSS council: running panel across %d voices", len(_active_voices()))
    panel_results = generate_multi(panel_prompt, max_tokens=500)

    panel_findings = "\n\n".join(
        f"[{provider.upper()}]:\n{text}" for provider, text in panel_results
    )

    if not panel_results:
        logger.warning("OSS council: no panel responses received")
        return _skipped()

    # ── Stage 2: Aggregator ───────────────────────────────────────────────────
    summary_max = get("tailoring.summary_max_chars", 500)
    cover_max   = get("tailoring.cover_letter_words_max", 150)

    agg_prompt = _AGGREGATOR_PROMPT.format(
        company=company,
        role=role,
        jd_excerpt=jd_excerpt[:3000],
        summary=summary,
        cover_letter=cover_letter,
        panel_findings=panel_findings,
        summary_max=summary_max,
        cover_max=cover_max,
    )

    try:
        raw = llm_generate(agg_prompt, max_tokens=1500)
        result = _parse_json(raw)
    except Exception as exc:
        logger.warning("OSS council aggregator failed: %s", exc)
        return _skipped()

    revised_summary = _sanitize(result.get("revised_summary", summary))
    revised_cover   = _sanitize(result.get("revised_cover_letter", cover_letter))

    # Hard guard: revert if the model inflated content beyond limits
    if len(revised_summary) > summary_max * 1.05:
        revised_summary = summary
    from utils.text import word_count
    if word_count(revised_cover) > cover_max * 1.1:
        revised_cover = cover_letter

    return {
        "skipped": False,
        "revised_summary": revised_summary,
        "revised_cover_letter": revised_cover,
        "changes_made": result.get("changes_made", []),
        "changes_skipped": result.get("changes_skipped", []),
        "panel_findings": panel_findings,
        "source": "oss",
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _active_voices() -> list[dict]:
    voices = get("oss.council_voices", [])
    if voices:
        return voices
    from utils.oss_llm import _provider_works, _DEFAULTS
    return [
        {"provider": p, "model": _DEFAULTS[p]}
        for p in ("groq", "gemini", "ollama", "mistral")
        if _provider_works(p)
    ]


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    return json.loads(raw)


def _sanitize(text: str) -> str:
    # Strip em dashes and collapse double punctuation
    text = text.replace("—", " ").replace("–", "-")
    text = re.sub(r"[.,;:]{2,}", ".", text)
    return text.strip()


def _skipped() -> dict:
    return {
        "skipped": True,
        "revised_summary": "",
        "revised_cover_letter": "",
        "changes_made": [],
        "panel_findings": "",
        "source": "oss",
    }
