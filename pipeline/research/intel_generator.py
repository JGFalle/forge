"""Generate people intel markdown via Claude API.

Replaces the manual Claude.ai paste step. Optionally enriched by
Perplexity live data injected into the prompt before calling Claude.
"""

from __future__ import annotations

import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from pipeline.ingest.jd_parser import ParsedJD
from pipeline.research.prompt_builder import build_people_intel_prompt
from utils.logging import get_logger

load_dotenv(Path(".env"))
logger = get_logger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def generate(
    jd: ParsedJD,
    company: str,
    role: str,
    slug: str,
    output_dir: Path,
    perplexity_context: str = "",
) -> Path:
    """
    Call Claude to generate a people intel markdown file.
    Returns the path to the saved .md file.

    If perplexity_context is provided it is appended to the prompt so
    Claude can ground its research in live data.
    """
    prompt = build_people_intel_prompt(jd, company, role, slug)

    if perplexity_context:
        prompt = (
            prompt
            + "\n\n---\nLIVE MARKET INTELLIGENCE (from Perplexity — use this to enrich "
            "all sections, especially Business Unit & Strategic Intelligence)\n---\n"
            + perplexity_context
        )

    logger.info("Generating people intel via API for %s / %s", company, role)
    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    content = response.content[0].text.strip()

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{slug}_people_intel.md"
    out_path.write_text(content, encoding="utf-8")
    logger.info("People intel saved: %s", out_path)
    return out_path
