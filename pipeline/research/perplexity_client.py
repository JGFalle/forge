"""Perplexity Sonar API client — live company and market intelligence.

Uses PERPLEXITY_API_KEY from .env. Returns empty string gracefully if
the key is missing or the request fails — the pipeline continues without it.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from utils.logging import get_logger

load_dotenv(Path(".env"))
logger = get_logger(__name__)


def fetch_company_intel(company: str, role: str) -> str:
    """
    Query Perplexity for live intelligence about the company and role.
    Returns raw enrichment text to inject into the people intel prompt.
    Returns empty string if PERPLEXITY_API_KEY is not set or request fails.
    """
    api_key = os.getenv("PERPLEXITY_API_KEY", "")
    if not api_key:
        logger.debug("PERPLEXITY_API_KEY not set — skipping live intel fetch")
        return ""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")

        query = (
            f"Research {company} for a job candidate applying to the {role} position. "
            f"Provide concise, specific intelligence on: "
            f"(1) How senior leadership talks about this function or business unit publicly "
            f"in earnings calls, investor days, or annual reports from the last 18 months — "
            f"include direct quotes with dates. "
            f"(2) Key strategic initiatives relevant to this role in 2025-2026. "
            f"(3) Any recent leadership changes, reorgs, or M&A affecting this function. "
            f"(4) Analyst or trade press coverage: is this a growth area, cost center, or turnaround? "
            f"Be specific. Cite sources and dates."
        )

        response = client.chat.completions.create(
            model="sonar-pro",
            messages=[{"role": "user", "content": query}],
            max_tokens=2000,
        )
        result = response.choices[0].message.content or ""
        logger.info("Perplexity intel fetched for %s (%d chars)", company, len(result))
        return result

    except Exception as exc:
        logger.warning("Perplexity fetch failed for %s: %s", company, exc)
        return ""
