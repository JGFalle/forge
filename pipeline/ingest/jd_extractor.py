"""Extract company name and role title from raw JD text via Claude.

Fires only when regex parsing in jd_parser.py leaves company/role empty.
Uses haiku for speed and cost - this is a simple extraction task.
Returns ("", "") on failure so the caller can fall back to interactive prompt.
"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv

from utils.logging import get_logger

load_dotenv()
logger = get_logger(__name__)

_PROMPT = """Extract the company name and job title from this job description.
Return only JSON with this exact structure: {{"company": "...", "role": "..."}}
Use the official company name. Use the exact job title as posted. No explanation.

Job description (first 2000 chars):
{text}"""


def extract_company_role(raw_text: str) -> tuple[str, str]:
    """
    Call Claude haiku to extract company and role from raw JD text.
    Returns (company, role) - either or both may be "" on failure.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "", ""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=128,
            messages=[{
                "role": "user",
                "content": _PROMPT.format(text=raw_text[:2000]),
            }],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        company = str(data.get("company", "")).strip()
        role = str(data.get("role", "")).strip()
        logger.info("Extracted via Claude: company=%r  role=%r", company, role)
        return company, role

    except Exception as exc:
        logger.warning("Company/role extraction failed: %s", exc)
        return "", ""
