"""Model council for resume summary and cover letter review.

Panel: three Perplexity models critique the Claude-generated draft against the JD.
Aggregator: sonar-reasoning receives the JD + original draft + panel findings,
            then applies ONLY the specific fixes the panel identified.
            It is a surgical editor, not a rewriter.

Design principle: the council enhances JD alignment: it does not replace
the Claude-generated content. Every word not flagged by the panel stays as-is.
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

from utils.logging import get_logger

load_dotenv(Path(".env"))
logger = get_logger(__name__)

PANEL_MODELS = ["sonar", "sonar-pro", "sonar-reasoning"]
AGGREGATOR_MODEL = "sonar-reasoning"

_PANEL_PROMPT = """\
You are reviewing a resume summary and cover letter against a specific job description.
Your only job is to identify specific problems — not to rewrite anything.

TARGET ROLE: {role} at {company}

JD (source of truth):
{jd_excerpt}

JD TERMS NOT YET IN THE DRAFT (keyword gap — incorporate these if they fit naturally):
{missing_keywords}

APPLICATION CONTEXT (why this role is being pursued — use to guide framing):
{application_context}

---
CLAUDE-GENERATED SUMMARY:
{summary}

CLAUDE-GENERATED COVER LETTER:
{cover_letter}
---

Flag only real problems. For each issue state: which field, what the problem is, \
and the minimal fix. Do not suggest stylistic changes or additions not grounded in the JD.

Check for:
1. JD ALIGNMENT: Key JD phrases or requirements missing from the summary/cover letter
2. KEYWORD GAPS: Which terms from the missing list above could naturally strengthen the draft?
3. IDENTITY: Does the summary lead with AI/ML ops or $65M automation? (never Lean as lead)
4. FIGURE INTEGRITY: Is any dollar figure repeated? ($18.7M must appear once only)
5. YEARS: Is any years-of-experience claim above 10 years? (career started Dec 2015 = 10 years)
6. PUNCTUATION: Any back-to-back punctuation (.,) or space-before-comma?
7. FABRICATION: Any claim not in the JD or the original draft? Flag it for removal.

Under 200 words. List issues only — no rewrites."""

_AGGREGATOR_PROMPT = """\
You are a surgical resume editor. Your job is to apply a short list of specific fixes \
to a Claude-generated summary and cover letter. You do NOT rewrite. You do NOT add content \
not already present in the original draft or explicitly required by the JD.

TARGET ROLE: {role} at {company}

JD (single source of truth — only use language and requirements from here):
{jd_text}

---
ORIGINAL CLAUDE DRAFT — SUMMARY:
{summary}

ORIGINAL CLAUDE DRAFT — COVER LETTER:
{cover_letter}
---

PANEL FINDINGS (the only changes you are authorised to make):
{reviews}
---

Rules:
- Apply ONLY the fixes the panel identified. Preserve every other word exactly.
- Do NOT introduce any person, company, PE firm, financial metric, or achievement \
that does not appear in either the JD text or the original Claude draft above.
- Do NOT change the lead sentence unless the panel explicitly flagged the identity framing.
- If the panel found no issues with a field, return that field unchanged.

HARD CONSTRAINTS (enforce even if panel did not flag):
- Summary: 500 characters max
- Cover letter: 150 words max
- No em dashes in either field
- No repeated dollar figures — if $18.7M already appears once, do not use it again
- Years of experience: 10 years or 10+ years maximum (career start Dec 2015)
- No back-to-back punctuation (.,) anywhere

Return ONLY a JSON object with exactly these five keys:
"agreements": array of 2-3 strings — issues all panel models agreed on
"disagreements": string — where panel diverged and your ruling
"final_summary": string — original summary with only panel-identified fixes applied
"final_cover_letter": string — original cover letter with only panel-identified fixes applied
"council_note": string — one sentence flagging the biggest remaining risk, or "None" if clean

No markdown fences. No explanation outside the JSON."""


def _sanitize(text: str) -> str:
    """Deterministic backstop: double punctuation, duplicate dollar figures, inflated years.

    Runs after every LLM output. Catches anything the model missed despite prompt rules.
    """
    # Double-punctuation collapse
    text = re.sub(r'\.,', '.', text)
    text = re.sub(r',\.', '.', text)
    text = re.sub(r'\.{2,}', '.', text)
    text = re.sub(r',{2,}', ',', text)
    text = re.sub(r'\s,', ',', text)
    text = re.sub(r'([.!?,;:])\s*([.!?,;:])', r'\1', text)

    # Deduplicate dollar figures, keep first mention, drop any sentence reusing the same figure
    seen: set[str] = set()
    sentences = re.split(r'(?<=[.!?])\s+', text)
    kept: list[str] = []
    for sentence in sentences:
        figures = re.findall(r'\$[\d,.]+[MBKmb+]*', sentence)
        normalised = {re.sub(r'\+$', '', f) for f in figures}
        if normalised & seen:
            continue
        seen.update(normalised)
        kept.append(sentence)
    text = ' '.join(kept)

    # Cap years-of-experience, hard max 10+ years (career start Dec 2015)
    text = re.sub(r'\b1[1-9]\+?\s*year', '10+ year', text, flags=re.IGNORECASE)
    text = re.sub(r'\b[2-9]\d\+?\s*year', '10+ year', text, flags=re.IGNORECASE)

    return text.strip()


def _parse_json(raw: str) -> dict:
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


def _query_model(client, model: str, prompt: str, max_tokens: int = 700) -> tuple[str, str]:
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        text = response.choices[0].message.content or ""
        logger.info("Council %s: %d chars", model, len(text))
        return model, text
    except Exception as exc:
        logger.warning("Council %s failed: %s", model, exc)
        return model, f"[ERROR: {exc}]"


def run_council(
    summary: str,
    cover_letter: str,
    jd_text: str,
    company: str,
    role: str,
    missing_keywords: list[str] | None = None,
    application_context: str = "",
) -> dict:
    """Run the council. Falls back to OSS (Groq/Gemini/Ollama) when Perplexity is unavailable."""
    api_key = os.getenv("PERPLEXITY_API_KEY", "")
    if not api_key:
        logger.info("PERPLEXITY_API_KEY not set — using OSS council (Groq/Gemini/Ollama)")
        try:
            from pipeline.tailoring.council_oss import run as oss_run
            return oss_run(
                company=company,
                role=role,
                summary=summary,
                cover_letter=cover_letter,
                jd_excerpt=jd_text[:3000],
                missing_keywords=missing_keywords or [],
                application_context=application_context,
            )
        except ImportError:
            logger.warning("council_oss dependencies not installed — council skipped")
            return {}
        except Exception as exc:
            logger.warning("OSS council failed: %s — skipping", exc)
            return {}

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package missing — council skipped")
        return {}

    client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")

    missing_str = ", ".join(missing_keywords) if missing_keywords else "None identified"
    ctx_str = application_context.strip() or "Standard application — no override context."
    panel_prompt = _PANEL_PROMPT.format(
        role=role,
        company=company,
        summary=summary,
        cover_letter=cover_letter,
        jd_excerpt=jd_text[:3000],
        missing_keywords=missing_str,
        application_context=ctx_str,
    )

    # Fan out panel in parallel
    panel_results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(PANEL_MODELS)) as executor:
        futures = {
            executor.submit(_query_model, client, model, panel_prompt): model
            for model in PANEL_MODELS
        }
        for future in as_completed(futures):
            model, response = future.result()
            panel_results[model] = response

    reviews_block = "\n\n".join(
        f"=== {model.upper()} ===\n{text}"
        for model, text in panel_results.items()
    )

    agg_prompt = _AGGREGATOR_PROMPT.format(
        role=role,
        company=company,
        jd_text=jd_text[:4000],
        summary=summary,
        cover_letter=cover_letter,
        reviews=reviews_block,
    )

    _, agg_raw = _query_model(client, AGGREGATOR_MODEL, agg_prompt, max_tokens=1200)
    agg = _parse_json(agg_raw)

    # Sanitize, deterministic backstop regardless of model output
    if "final_summary" in agg:
        agg["final_summary"] = _sanitize(agg["final_summary"])
    if "final_cover_letter" in agg:
        agg["final_cover_letter"] = _sanitize(agg["final_cover_letter"])

    # Safety check: if council output introduces new dollar figures not in the original,
    # fall back to the original for that field
    original_figures = set(re.findall(r'\$[\d,.]+[MBKmb+]*', summary + cover_letter))
    for field, original_text in [("final_summary", summary), ("final_cover_letter", cover_letter)]:
        if field not in agg:
            continue
        new_figures = set(re.findall(r'\$[\d,.]+[MBKmb+]*', agg[field]))
        novel = new_figures - {re.sub(r'\+$', '', f) for f in original_figures} - original_figures
        if novel:
            logger.warning("Council introduced new figures %s in %s — reverting field to original", novel, field)
            agg[field] = _sanitize(original_text)
            agg.setdefault("council_note", "")
            agg["council_note"] += f" [Auto-reverted {field}: introduced novel figures {novel}]"

    return {"panel": panel_results, "aggregation": agg}


def save_report(
    result: dict,
    summary_original: str,
    cover_letter_original: str,
    company: str,
    role: str,
    output_dir: Path,
    slug: str,
) -> Path | None:
    if not result:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"summary_council_{slug}.md"

    agg = result.get("aggregation", {})
    final_summary = agg.get("final_summary", "")
    final_cover = agg.get("final_cover_letter", "")
    agreements = agg.get("agreements", [])
    disagreements = agg.get("disagreements", "")
    council_note = agg.get("council_note", "")

    lines: list[str] = [
        "# Council Report",
        f"**{company} / {role}**",
        "",
        "## Original Summary",
        f"> {summary_original}",
        f"*({len(summary_original)} chars)*",
        "",
        "## Original Cover Letter",
        f"> {cover_letter_original}",
        f"*({len(cover_letter_original.split())} words)*",
        "",
        "---",
        "## Panel Findings",
    ]

    for model, text in result.get("panel", {}).items():
        lines += [f"### {model}", "", text, ""]

    lines += ["---", "## Synthesis"]

    if isinstance(agreements, list) and agreements:
        lines += ["", "### Consensus Issues"]
        for item in agreements:
            lines.append(f"- {item}")

    if disagreements:
        lines += ["", "### Panel Divergence", disagreements]

    if council_note and council_note != "None":
        lines += ["", f"> **Note:** {council_note}"]

    lines += ["", "---", "## Final Revised Summary"]
    if final_summary:
        char_count = len(final_summary)
        flag = "  **WARNING: over 500 char limit**" if char_count > 500 else ""
        lines += [f"> {final_summary}", f"*({char_count} chars{flag})*"]
    else:
        lines.append("_Council returned no revised summary — original used._")

    lines += ["", "## Final Revised Cover Letter"]
    if final_cover:
        word_count = len(final_cover.split())
        flag = "  **WARNING: over 150 word limit**" if word_count > 150 else ""
        lines += [f"> {final_cover}", f"*({word_count} words{flag})*"]
    else:
        lines.append("_Council returned no revised cover letter — original used._")

    if "raw" in agg:
        lines += ["", "### Raw Aggregator Output", "```", agg["raw"], "```"]

    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Council report (MD) saved: %s", path)

    try:
        from pipeline.people_intel.pdf_renderer import run as render_pdf
        pdf_path = render_pdf(path, path.with_suffix(".pdf"))
        logger.info("Council report (PDF) saved: %s", pdf_path)
        path.unlink()
        return pdf_path
    except Exception as exc:
        logger.warning("Council PDF render failed, keeping MD: %s", exc)
        return path


def display_result(result: dict, summary_original: str, cover_letter_original: str) -> None:
    if not result:
        return

    agg = result.get("aggregation", {})
    final_summary = agg.get("final_summary", "")
    final_cover = agg.get("final_cover_letter", "")
    agreements = agg.get("agreements", [])
    note = agg.get("council_note", "")

    print()
    print("  COUNCIL VERDICT")
    if isinstance(agreements, list):
        for item in agreements:
            print(f"    + {item}")
    if note and note != "None":
        print(f"    ! {note}")

    if final_summary:
        delta = len(final_summary) - len(summary_original)
        sign = "+" if delta > 0 else ""
        print(f"\n  REVISED SUMMARY ({len(final_summary)} chars, {sign}{delta} vs original):")
        print(f"  {final_summary}")

    if final_cover:
        orig_words = len(cover_letter_original.split())
        new_words = len(final_cover.split())
        delta = new_words - orig_words
        sign = "+" if delta > 0 else ""
        print(f"\n  REVISED COVER LETTER ({new_words} words, {sign}{delta} vs original):")
        print(f"  {final_cover}")

    print()
