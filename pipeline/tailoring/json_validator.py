from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import jsonschema

logger = logging.getLogger(__name__)


def _build_schema() -> dict:
    """Build validation schema with role_identifiers pulled from config."""
    from utils.config import get
    history = get("career_history", [])
    valid_ids = [r["id"] for r in history if "id" in r] or ["current_role", "prev_role"]

    return {
        "type": "object",
        "properties": {
            "company": {"type": "string"},
            "role": {"type": "string"},
            "filename": {"type": "string"},
            "date_generated": {"type": "string"},
            "headline": {"type": "string"},
            "summary": {"type": "string"},
            "competencies": {
                "type": "array",
                "minItems": 4,
                "maxItems": 4,
                "items": {"type": "string"},
            },
            "experience_modifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role_identifier": {
                            "type": "string",
                            "enum": valid_ids,
                        },
                        "company": {"type": "string"},
                        "replacement_lead_in": {"type": "string"},
                        "replacement_bullets": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["role_identifier"],
                    "additionalProperties": False,
                },
            },
            "technical_skills": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {"type": "string"},
            },
            "cover_letter": {"type": "string"},
            "notes": {"type": "string"},
        },
        "required": ["filename"],
        "additionalProperties": True,
    }


def load_json(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_schema(data: dict) -> str | None:
    # Returns error string or None if valid — safe for retry loops
    try:
        jsonschema.validate(instance=data, schema=_build_schema())
        return None
    except jsonschema.ValidationError as e:
        path = " > ".join(str(p) for p in e.path) if e.path else "root"
        return f"{e.message} (at: {path})"


def validate_json(data: dict) -> None:
    error = check_schema(data)
    if error:
        logger.error("JSON validation failed: %s", error)
        sys.exit(1)


def grammar_check(data: dict) -> dict:
    try:
        from utils.grammar import check_json_fields
        return check_json_fields(data)
    except Exception as exc:
        logger.warning("Grammar check skipped: %s", exc)
        return {}
