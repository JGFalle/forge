"""Deep company and role intelligence for the Executive Summary.

Two phases:
1. Perplexity phase — 5 parallel queries covering strategy, role context,
   financials, recent news, and culture/talent reputation.
2. Council phase — 3 panel models each independently review all Perplexity
   findings against the JD, then the aggregator synthesizes consensus and
   divergence into a structured brief.

When PERPLEXITY_API_KEY is absent, both phases transparently fall back to the
OSS backend (ddgs web search + local LLM via utils.oss_llm). The OSS path
returns the exact same shapes, so exec_summary / resume_mods render identically.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

from utils.config import get
from utils.logging import get_logger

load_dotenv(Path(".env"))
logger = get_logger(__name__)

PANEL_MODELS    = ["sonar", "sonar-pro", "sonar-reasoning-pro"]
AGGREGATOR_MODEL = "sonar-pro"
AGGREGATOR_FALLBACK = "sonar"

# Human labels for each research angle (shared with the OSS backend).
INTEL_LABELS = {
    "strategy":       "COMPANY STRATEGY & LEADERSHIP LANGUAGE",
    "role_context":   "ROLE CONTEXT & ORG STRUCTURE",
    "financials":     "FINANCIAL & ANALYST VIEW",
    "recent_news":    "RECENT NEWS & CHANGES (last 12 months)",
    "culture_talent": "CULTURE & TALENT REPUTATION",
}

# ── Perplexity query templates ────────────────────────────────────────────────

_QUERIES = {
    "strategy": (
        "Research {company} for a candidate applying to the {role} position. "
        "Focus: How does senior leadership at {company} publicly talk about this function or business unit? "
        "Search earnings call transcripts, investor day presentations, annual reports, and shareholder letters "
        "from the last 18 months. Does the CEO, CFO, or COO reference this area by name? "
        "What language do they use? Is it framed as a strategic priority, a turnaround, a cost center, or a growth engine? "
        "Include direct quotes with dates and sources."
    ),
    "role_context": (
        "Research the {role} position at {company}. "
        "Focus: What does this role actually own? What team does it lead, who does it report to, "
        "and who are the key cross-functional partners? "
        "What does success look like in year one? What problems is this role expected to solve? "
        "Search LinkedIn profiles of current/former people in this role, job postings, and any org chart signals. "
        "Be specific to {company}'s structure, not generic."
    ),
    "financials": (
        "Research {company}'s {function_area} function from an analyst and market perspective. "
        "Focus: How do sell-side analysts or trade press describe this part of the business? "
        "Is it a growth area, cost center, or turnaround story in {company}'s portfolio? "
        "Any recent earnings call references, analyst day coverage, or trade press commentary "
        "about performance, investment levels, or strategic bets in this area? Cite sources and dates."
    ),
    "recent_news": (
        "What significant changes have happened at {company} affecting the {role} function in the last 12 months? "
        "Search for: leadership changes, M&A activity, restructuring announcements, layoffs, expansions, "
        "new technology investments, or major contract wins/losses relevant to this function. "
        "Include specific dates, sources, and why each development matters for someone joining this team."
    ),
    "culture_talent": (
        "What is {company}'s reputation as an employer for {seniority} professionals in {function_area}? "
        "Search Glassdoor reviews, LinkedIn employee posts, Blind, and any industry coverage about the culture, "
        "management quality, and career trajectory for this function at {company}. "
        "What do current and former employees say about working in this area? "
        "Any patterns in turnover, promotion rates, or management style worth flagging?"
    ),
}

# ── Prompt fragments built from config ────────────────────────────────────────

def _seniority() -> str:
    """Target seniority descriptor, from config.identity.target_levels."""
    levels = get("identity", {}).get("target_levels", [])
    if levels:
        return " / ".join(levels)
    return "senior leadership"


def candidate_context() -> str:
    """Build the CANDIDATE CONTEXT block from config (no hardcoded personal data)."""
    identity = get("identity", {})
    primary = identity.get("primary", "")
    history = get("career_history", [])
    achievements = get("key_achievements", [])

    lines: list[str] = []
    if history:
        r = history[0]
        title = r.get("title", "")
        company = r.get("company", "")
        if title or company:
            lines.append(f"- Currently: {title}{' at ' + company if company else ''}")
    for a in achievements[:2]:
        lines.append(f"- {a}")
    if primary:
        lines.append(f"- Identity: {primary}")
    lines.append(f"- Targeting: {_seniority()} level")

    if not lines:
        return "- (Candidate context not configured — fill in config.yaml identity / career_history / key_achievements)"
    return "\n".join(lines)


# ── Panel prompt ──────────────────────────────────────────────────────────────

_PANEL_PROMPT = """\
You are preparing a comprehensive intelligence brief for a {seniority} candidate \
applying to {role} at {company}.

RESEARCH FINDINGS (5 research angles):

{intel_block}

JD KEY REQUIREMENTS:
{jd_excerpt}

CANDIDATE CONTEXT:
{candidate_context}

Extract structured insights across ALL of the following dimensions. \
Be specific — cite sources and dates from the research above where relevant. \
If a dimension has no clear signal in the research, say so explicitly.

1. COMPANY STRATEGY: Is this function a strategic priority, a cost center, or a turnaround? \
What does the company's public language signal about investment level?

2. ROLE CONTEXT: Where does this role sit? What is it expected to own and fix? \
What cross-functional relationships matter most?

3. DAY IN THE LIFE: Based on the JD and research, what does the actual week-to-week work look like? \
Key meetings, deliverables, and stakeholders.

4. INTERVIEW TALKING POINTS: 2-3 specific, sharp talking points that mirror this company's language \
and priorities. Not generic — grounded in what you found.

5. HIGH-SIGNAL QUESTION: One question the candidate could ask in an interview that signals \
analyst-level fluency. Must be specific enough that only someone who did real research would ask it.

6. RED FLAGS: Anything in the research that is a concern — leadership instability, function de-emphasis, \
culture issues, role churn, or ghost job signals.

7. TAILWINDS: Strong positive signals — strategic priority, funding, leadership championship, \
growth trajectory.

8. UNIQUE FINDS: Anything useful you surfaced that wasn't explicitly asked for above — \
competitive context, industry dynamics, timing considerations.

Under 500 words. Be direct and specific."""

# ── Aggregator prompt ─────────────────────────────────────────────────────────

_AGGREGATOR_PROMPT = """\
You are synthesizing three independent intelligence reviews about {role} at {company}.
Your output will become the core of an executive briefing document.

ORIGINAL RESEARCH:
{intel_block}

JD EXCERPT:
{jd_excerpt}

PANEL REVIEWS:
{reviews}

Produce a JSON object with exactly these keys:

"company_strategy": string — how leadership positions this function; is it a growth bet, cost center, or turnaround?
"role_context": string — what this role owns, who it reports to, what problem it solves, year-1 success
"day_in_the_life": string — specific week-to-week work reconstructed from JD + research (3-5 concrete sentences)
"interview_talking_points": array of 3 strings — specific, company-language-grounded, not generic
"high_signal_question": string — one question only someone who did real research would ask
"red_flags": string — concerns from research, or "None identified" if clean
"tailwinds": string — strong positive signals, or "None identified"
"unique_insights": array of 2-3 strings — useful finds beyond the standard dimensions
"consensus": string — what all 3 panel models agreed on (1-2 sentences)
"divergence": string — where models disagreed and the tie-break ruling (1-2 sentences), or "No significant divergence"

Return ONLY valid JSON. No markdown fences."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_error(text: str) -> bool:
    return not text or text.startswith("[ERROR:")


def _query_perplexity(client, query_key: str, query: str) -> tuple[str, str]:
    try:
        response = client.chat.completions.create(
            model="sonar-pro",
            messages=[{"role": "user", "content": query}],
            max_tokens=1500,
        )
        text = response.choices[0].message.content or ""
        logger.info("Perplexity %s: %d chars", query_key, len(text))
        return query_key, text
    except Exception as exc:
        logger.warning("Perplexity query %s failed: %s", query_key, exc)
        return query_key, f"[ERROR: {exc}]"


def _query_panel(client, model: str, prompt: str, max_tokens: int = 1000) -> tuple[str, str]:
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        text = response.choices[0].message.content or ""
        logger.info("Intel council %s: %d chars", model, len(text))
        return model, text
    except Exception as exc:
        msg = str(exc)
        logger.warning("Intel council %s failed: %s", model, msg)
        if "deprecated" in msg.lower() or "invalid_model" in msg.lower():
            print(f"\n  ⚠  INTEL COUNCIL — model '{model}' deprecated. Update PANEL_MODELS in exec_intel.py\n")
        return model, f"[ERROR: {exc}]"


def parse_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        cleaned = parts[1] if len(parts) > 1 else cleaned
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        return json.loads(cleaned.strip())
    except Exception:
        return {"raw": raw}


def format_intel_block(intel: dict[str, str]) -> str:
    sections = []
    for key, label in INTEL_LABELS.items():
        text = intel.get(key, "")
        if text and not _is_error(text):
            sections.append(f"--- {label} ---\n{text}")
    return "\n\n".join(sections)


def infer_function(role: str) -> str:
    role_lower = role.lower()
    if any(w in role_lower for w in ["supply chain", "logistics", "network", "distribution"]):
        return "supply chain and logistics"
    if any(w in role_lower for w in ["operations", "manufacturing", "industrial"]):
        return "operations"
    if any(w in role_lower for w in ["ai", "ml", "data", "analytics", "digital"]):
        return "data and AI"
    if any(w in role_lower for w in ["consulting", "transformation", "strategy"]):
        return "strategy and transformation"
    return "operations and strategy"


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_deep_intel(
    company: str,
    role: str,
    jd_text: str,
    function_area: str = "",
) -> dict[str, str]:
    """Run 5 parallel Perplexity queries. Returns dict of query_key → raw text.

    Falls back to the OSS backend (ddgs + local LLM) when PERPLEXITY_API_KEY is
    absent. Returns {} only when neither cloud nor OSS can produce anything.
    """
    api_key = os.getenv("PERPLEXITY_API_KEY", "")
    if not api_key:
        logger.info("PERPLEXITY_API_KEY not set — using OSS deep intel (ddgs + local LLM)")
        try:
            from pipeline.research.exec_intel_oss import fetch_deep_intel as oss_fetch
            return oss_fetch(company, role, jd_text, function_area)
        except ImportError:
            logger.debug("exec_intel_oss dependencies missing — exec intel skipped")
            return {}

    try:
        from openai import OpenAI
    except ImportError:
        return {}

    if not function_area:
        function_area = infer_function(role)

    client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")

    queries = {
        key: tmpl.format(company=company, role=role, function_area=function_area, seniority=_seniority())
        for key, tmpl in _QUERIES.items()
    }

    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(queries)) as executor:
        futures = {
            executor.submit(_query_perplexity, client, key, q): key
            for key, q in queries.items()
        }
        for future in as_completed(futures):
            key, text = future.result()
            results[key] = text

    good = sum(1 for v in results.values() if not _is_error(v))
    logger.info("Exec intel: %d/%d Perplexity queries succeeded", good, len(queries))
    return results


def run_intel_council(
    intel: dict[str, str],
    jd_text: str,
    company: str,
    role: str,
) -> dict:
    """3-panel council reviews all findings. Returns structured brief + panel outputs.

    Falls back to the OSS council (utils.oss_llm) when PERPLEXITY_API_KEY is absent.
    """
    api_key = os.getenv("PERPLEXITY_API_KEY", "")
    if not api_key:
        try:
            from pipeline.research.exec_intel_oss import run_intel_council as oss_council
            return oss_council(intel, jd_text, company, role)
        except ImportError:
            return {}

    if not intel:
        return {}

    try:
        from openai import OpenAI
    except ImportError:
        return {}

    client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")
    intel_block = format_intel_block(intel)

    panel_prompt = _PANEL_PROMPT.format(
        company=company,
        role=role,
        seniority=_seniority(),
        candidate_context=candidate_context(),
        intel_block=intel_block,
        jd_excerpt=jd_text[:2500],
    )

    # Fan out panel in parallel
    panel_results: dict[str, str] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(PANEL_MODELS)) as executor:
        futures = {
            executor.submit(_query_panel, client, model, panel_prompt): model
            for model in PANEL_MODELS
        }
        for future in as_completed(futures):
            model, response = future.result()
            panel_results[model] = response
            if _is_error(response):
                errors.append(f"{model}: {response}")

    good_reviews = {m: t for m, t in panel_results.items() if not _is_error(t)}
    if not good_reviews:
        return {"errors": errors, "panel": panel_results, "aggregation": {}}

    reviews_block = "\n\n".join(
        f"=== {m.upper()} ===\n{t}" for m, t in good_reviews.items()
    )

    agg_prompt = _AGGREGATOR_PROMPT.format(
        company=company,
        role=role,
        intel_block=intel_block[:6000],   # cap so aggregator prompt stays within limits
        jd_excerpt=jd_text[:1500],
        reviews=reviews_block,
    )

    _, agg_raw = _query_panel(client, AGGREGATOR_MODEL, agg_prompt, max_tokens=2500)
    if _is_error(agg_raw):
        errors.append(f"Aggregator {AGGREGATOR_MODEL}: {agg_raw}")
        print(f"  ⚠  Intel council aggregator failed — trying fallback ({AGGREGATOR_FALLBACK})")
        _, agg_raw = _query_panel(client, AGGREGATOR_FALLBACK, agg_prompt, max_tokens=2500)

    agg = parse_json(agg_raw)
    result = {"panel": panel_results, "aggregation": agg, "raw_intel": intel}
    if errors:
        result["errors"] = errors
    return result
