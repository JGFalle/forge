"""Generate tailoring JSON via Claude API - replaces the manual Claude.ai paste step.

Calls the same prompt template used for human-in-the-loop mode, but routes
the output through the API directly. Strips em dashes, validates schema,
and saves to the specified output directory.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from pipeline.ingest.jd_parser import ParsedJD
from pipeline.research.prompt_builder import build_tailoring_prompt
from utils.config import get
from utils.logging import get_logger

load_dotenv(Path(".env"))
logger = get_logger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


_MAX_RETRIES = 3


def generate(
    jd: ParsedJD,
    company: str,
    role: str,
    slug: str,
    application_context: str = "",
    output_dir: Path | None = None,
) -> tuple[dict, Path]:
    """
    Call Claude to generate a validated tailoring JSON.
    Retries up to 3 times, feeding schema errors back to Claude on each failure.
    Returns (data_dict, saved_json_path).
    """
    from pipeline.tailoring.json_validator import check_schema

    today = datetime.now().strftime("%Y-%m-%d")
    prompt = build_tailoring_prompt(jd, company, role, slug, today, application_context)
    messages: list[dict] = [{"role": "user", "content": prompt}]

    last_error: str = ""
    data: dict = {}

    for attempt in range(_MAX_RETRIES):
        if attempt > 0:
            logger.info(
                "Retrying tailoring JSON (attempt %d/%d) — last error: %s",
                attempt + 1, _MAX_RETRIES, last_error,
            )

        response = _get_client().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=messages,
        )
        raw_response = response.content[0].text.strip()
        messages.append({"role": "assistant", "content": raw_response})

        cleaned = _strip_fences(raw_response)
        cleaned = _strip_em_dashes(cleaned)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            last_error = f"Invalid JSON — {e}"
            messages.append({
                "role": "user",
                "content": (
                    f"Your response was not valid JSON. Parse error: {e}. "
                    "Return only the JSON object with no explanation or markdown."
                ),
            })
            continue

        schema_error = check_schema(data)
        if schema_error:
            last_error = schema_error
            messages.append({
                "role": "user",
                "content": (
                    f"The JSON failed schema validation: {schema_error}. "
                    "Return a corrected JSON object only."
                ),
            })
            continue

        break  # valid JSON and schema passes
    else:
        raise ValueError(
            f"Tailoring JSON generation failed after {_MAX_RETRIES} attempts. "
            f"Last error: {last_error}"
        )

    data.setdefault("filename", get("person.resume_filename", "YourName_Resume.docx"))
    data.setdefault("date_generated", today)

    if output_dir is None:
        output_dir = Path(get("paths.inputs_dir", "inputs/"))
    output_dir.mkdir(parents=True, exist_ok=True)

    out_path = output_dir / f"tailoring_{slug}.json"
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Tailoring JSON saved: %s", out_path)
    return data, out_path


def _strip_fences(text: str) -> str:
    if text.startswith("```"):
        parts = text.split("```")
        content = parts[1]
        if content.startswith("json"):
            content = content[4:]
        return content.strip()
    return text


def _strip_em_dashes(text: str) -> str:
    return text.replace("—", " -").replace("–", "-")
