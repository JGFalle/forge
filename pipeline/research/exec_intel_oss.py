"""Open-source deep company intelligence — no Perplexity required.

Drop-in fallback for exec_intel.fetch_deep_intel / run_intel_council. Gathers
web context with ddgs across the same five research angles, then synthesizes a
structured brief with the local OSS LLM (Groq / Gemini / Ollama via
utils.oss_llm). Returns the exact shapes exec_intel produces, so exec_summary
and resume_mods render identically.

Degradation ladder:
  - ddgs + OSS LLM available -> synthesized briefs + full council
  - ddgs only (no LLM)       -> raw search snippets as intel, minimal aggregation
  - neither                  -> {} (caller treats exec intel as unavailable)
"""

from __future__ import annotations

import time
from pathlib import Path

from dotenv import load_dotenv

from pipeline.research.exec_intel import (
    _AGGREGATOR_PROMPT,
    _PANEL_PROMPT,
    INTEL_LABELS,
    _seniority,
    candidate_context,
    format_intel_block,
    infer_function,
    parse_json,
)
from utils.logging import get_logger

load_dotenv(Path(".env"))
logger = get_logger(__name__)

# Short keyword searches per angle (ddgs text search, not a full prompt).
_SEARCHES = {
    "strategy": "{company} {role} strategy leadership priorities earnings",
    "role_context": "{role} at {company} responsibilities team reports org structure",
    "financials": "{company} {function_area} analyst growth investment performance",
    "recent_news": "{company} {function_area} leadership change layoffs restructuring news",
    "culture_talent": "{company} {function_area} employee reviews culture glassdoor",
}

_SYNTH_SYSTEM = (
    "You are a research analyst preparing a briefing. Summarize ONLY what the "
    "supplied search results support. Be specific and concise. If the results are "
    "thin, say so plainly rather than inventing detail."
)


def _search(query: str, max_results: int = 6) -> list[str]:
    """Return ddgs text-search snippets as 'title: body' lines."""
    try:
        from ddgs import DDGS
    except ImportError:
        logger.debug("ddgs not installed — OSS exec intel cannot search")
        return []
    try:
        with DDGS() as ddgs:
            out = []
            for r in ddgs.text(query, max_results=max_results):
                title = (r.get("title") or "").strip()
                body = (r.get("body") or "").strip()
                if title or body:
                    out.append(f"- {title}: {body}")
            return out
    except Exception as exc:
        logger.debug("ddgs search failed for %r: %s", query, exc)
        return []


def fetch_deep_intel(
    company: str,
    role: str,
    jd_text: str,
    function_area: str = "",
) -> dict[str, str]:
    """OSS equivalent of exec_intel.fetch_deep_intel. Returns query_key -> text."""
    function_area = function_area or infer_function(role)

    from utils import oss_llm
    have_llm = oss_llm.available_provider() is not None

    results: dict[str, str] = {}
    for key, tmpl in _SEARCHES.items():
        query = tmpl.format(company=company, role=role, function_area=function_area)
        snippets = _search(query)
        if not snippets:
            results[key] = ""
            continue

        joined = "\n".join(snippets[:8])
        if have_llm:
            label = INTEL_LABELS.get(key, key)
            try:
                results[key] = oss_llm.generate(
                    prompt=(
                        f"Research angle: {label}.\n"
                        f"Company: {company}. Role: {role}.\n\n"
                        f"SEARCH RESULTS:\n{joined}\n\n"
                        "Write a tight 4-6 sentence briefing on this angle from the results above."
                    ),
                    system=_SYNTH_SYSTEM,
                    max_tokens=500,
                )
            except Exception as exc:
                logger.debug("OSS synth failed for %s: %s", key, exc)
                results[key] = joined
        else:
            results[key] = joined
        time.sleep(0.3)

    good = sum(1 for v in results.values() if v)
    logger.info("OSS exec intel: %d/%d angles returned content", good, len(_SEARCHES))
    return results


def _fallback_aggregation(intel: dict[str, str]) -> dict:
    """Minimal structured brief when no OSS LLM is available — surface raw intel."""
    return {
        "company_strategy": intel.get("strategy", "") or "Not enough signal found.",
        "role_context": intel.get("role_context", "") or "Not enough signal found.",
        "day_in_the_life": "",
        "interview_talking_points": [],
        "high_signal_question": "",
        "red_flags": intel.get("recent_news", "") or "None identified",
        "tailwinds": intel.get("financials", "") or "None identified",
        "unique_insights": [],
        "consensus": "",
        "divergence": "OSS mode without a local LLM: raw search findings only, no council synthesis.",
    }


def run_intel_council(
    intel: dict[str, str],
    jd_text: str,
    company: str,
    role: str,
) -> dict:
    """OSS equivalent of exec_intel.run_intel_council. Returns panel + aggregation."""
    if not intel or not any(intel.values()):
        return {}

    from utils import oss_llm
    if oss_llm.available_provider() is None:
        logger.info("No OSS LLM available — exec intel returns raw findings without council")
        return {
            "panel": {},
            "aggregation": _fallback_aggregation(intel),
            "raw_intel": intel,
            "source": "oss",
        }

    intel_block = format_intel_block(intel)
    panel_prompt = _PANEL_PROMPT.format(
        company=company,
        role=role,
        seniority=_seniority(),
        candidate_context=candidate_context(),
        intel_block=intel_block,
        jd_excerpt=jd_text[:2500],
    )

    panel: dict[str, str] = {}
    try:
        for provider, response in oss_llm.generate_multi(panel_prompt, max_tokens=1000):
            panel[provider] = response
    except Exception as exc:
        logger.warning("OSS intel panel failed: %s", exc)

    if not panel:
        return {
            "panel": {},
            "aggregation": _fallback_aggregation(intel),
            "raw_intel": intel,
            "source": "oss",
        }

    reviews_block = "\n\n".join(f"=== {p.upper()} ===\n{t}" for p, t in panel.items())
    agg_prompt = _AGGREGATOR_PROMPT.format(
        company=company,
        role=role,
        intel_block=intel_block[:6000],
        jd_excerpt=jd_text[:1500],
        reviews=reviews_block,
    )

    try:
        agg_raw = oss_llm.generate(
            agg_prompt,
            system="Return ONLY valid JSON. No markdown fences.",
            max_tokens=2000,
        )
        agg = parse_json(agg_raw)
    except Exception as exc:
        logger.warning("OSS intel aggregator failed: %s", exc)
        agg = _fallback_aggregation(intel)

    return {"panel": panel, "aggregation": agg, "raw_intel": intel, "source": "oss"}
