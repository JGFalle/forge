"""Tests for pipeline/tailoring/json_validator.py"""

import pytest
from pipeline.tailoring.json_validator import validate_json, check_schema


def _minimal() -> dict:
    return {"filename": "Jack_Falle_Resume.docx"}


def _full() -> dict:
    return {
        "company": "Acme Corp",
        "role": "Director of Operations",
        "filename": "Jack_Falle_Resume.docx",
        "date_generated": "2026-05-10",
        "headline": "DATA-DRIVEN OPERATIONS LEADER",
        "summary": "Short summary here.",
        "competencies": ["Row 1", "Row 2", "Row 3", "Row 4"],
        "experience_modifications": [
            {
                "role_identifier": "jmrs",
                "company": "JMRS Consulting LLC",
                "replacement_lead_in": "Lead-in sentence.",
                "replacement_bullets": ["Bullet 1", "Bullet 2"],
            }
        ],
        "technical_skills": ["Row 1", "Row 2"],
        "cover_letter": "Four sentence cover letter here.",
        "notes": "Internal notes.",
    }


# ── Should pass ───────────────────────────────────────────────────────────────

def test_minimal_valid():
    validate_json(_minimal())  # should not raise


def test_full_valid():
    validate_json(_full())  # should not raise


def test_extra_fields_allowed():
    data = _minimal()
    data["unexpected_field"] = "some value"
    validate_json(data)  # additionalProperties=True


def test_competencies_exactly_four():
    data = _full()
    data["competencies"] = ["A", "B", "C", "D"]
    validate_json(data)


# ── Should fail ───────────────────────────────────────────────────────────────

def test_missing_filename_fails(capsys):
    with pytest.raises(SystemExit):
        validate_json({})


def test_competencies_wrong_count_fails(capsys):
    data = _full()
    data["competencies"] = ["A", "B", "C"]  # only 3 — minItems is 4
    with pytest.raises(SystemExit):
        validate_json(data)


def test_invalid_role_identifier_fails(capsys):
    data = _full()
    data["experience_modifications"][0]["role_identifier"] = "invalid_role"
    with pytest.raises(SystemExit):
        validate_json(data)


def test_extra_keys_in_mod_fails(capsys):
    data = _full()
    data["experience_modifications"][0]["unknown_key"] = "oops"
    with pytest.raises(SystemExit):
        validate_json(data)


def test_technical_skills_too_many_fails(capsys):
    data = _full()
    data["technical_skills"] = ["R1", "R2", "R3", "R4"]  # maxItems is 3
    with pytest.raises(SystemExit):
        validate_json(data)


# ── check_schema (non-exit version for retry loop) ───────────────────────────

def test_check_schema_valid_returns_none():
    assert check_schema(_full()) is None


def test_check_schema_missing_filename_returns_error():
    error = check_schema({})
    assert error is not None
    assert isinstance(error, str)
    assert len(error) > 0


def test_check_schema_bad_competencies_returns_error():
    data = _full()
    data["competencies"] = ["only three", "items", "here"]
    error = check_schema(data)
    assert error is not None


def test_check_schema_does_not_exit():
    # Unlike validate_json, check_schema must never call sys.exit
    import sys
    original_exit = sys.exit
    exited = []
    sys.exit = lambda code=0: exited.append(code)
    try:
        check_schema({})
    finally:
        sys.exit = original_exit
    assert exited == [], "check_schema called sys.exit — it must not"
