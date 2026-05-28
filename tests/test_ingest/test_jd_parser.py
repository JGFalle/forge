"""Tests for pipeline/ingest/jd_parser.py"""

import pytest
from pipeline.ingest.jd_parser import parse, _parse_date_string


# ── Salary extraction ─────────────────────────────────────────────────────────

def test_salary_dollar_range():
    jd = parse("The base salary range is $175,000 - $210,000 annually.")
    assert "$175,000" in jd.salary_range

def test_salary_not_present():
    jd = parse("Join our team and make an impact.")
    assert jd.salary_range == ""

def test_salary_k_notation():
    jd = parse("Compensation: $185k - $220k per year")
    assert jd.salary_range != ""


# ── Req number extraction ─────────────────────────────────────────────────────

def test_req_number_job_id():
    jd = parse("Job ID: JR-94821 | Director of Operations")
    assert jd.req_number == "JR-94821"

def test_req_number_requisition():
    jd = parse("Requisition #: 10042891")
    assert jd.req_number == "10042891"

def test_req_number_missing():
    jd = parse("No requisition number here.")
    assert jd.req_number == ""


# ── ATS system detection ──────────────────────────────────────────────────────

def test_ats_workday():
    jd = parse("Apply at careers.myworkdayjobs.com/company/job/123")
    assert jd.ats_system == "Workday"

def test_ats_greenhouse():
    jd = parse("Submit your application via boards.greenhouse.io/company")
    assert jd.ats_system == "Greenhouse"

def test_ats_none():
    jd = parse("Send your resume directly to hr@company.com")
    assert jd.ats_system == ""


# ── Remote detection ─────────────────────────────────────────────────────────

def test_remote_detected():
    jd = parse("This is a hybrid role based in Atlanta, GA.")
    assert jd.remote_ok is True

def test_remote_not_detected():
    jd = parse("This role requires on-site presence 5 days per week.")
    assert jd.remote_ok is False


# ── Apply-by-date extraction ──────────────────────────────────────────────────

def test_apply_by_full_date():
    jd = parse("Applications close May 30, 2026.")
    assert jd.apply_by_date == "2026-05-30"

def test_apply_by_slash_format():
    jd = parse("Apply by 06/15/2026.")
    assert jd.apply_by_date == "2026-06-15"

def test_apply_by_iso_format():
    jd = parse("Posting closes: 2026-07-01")
    assert jd.apply_by_date == "2026-07-01"

def test_apply_by_no_date():
    jd = parse("No deadline mentioned. Apply when ready.")
    assert jd.apply_by_date == ""


# ── _parse_date_string ────────────────────────────────────────────────────────

def test_parse_date_full_month():
    assert _parse_date_string("June 15, 2026") == "2026-06-15"

def test_parse_date_abbrev_month():
    assert _parse_date_string("Jun 15, 2026") == "2026-06-15"

def test_parse_date_ordinal():
    assert _parse_date_string("May 25th, 2026") == "2026-05-25"

def test_parse_date_slash():
    assert _parse_date_string("05/25/2026") == "2026-05-25"

def test_parse_date_iso():
    assert _parse_date_string("2026-05-25") == "2026-05-25"

def test_parse_date_invalid():
    assert _parse_date_string("next Tuesday") == ""


# ── raw_text preserved ────────────────────────────────────────────────────────

def test_raw_text_stored():
    text = "Director of Operations | Coca-Cola | Atlanta, GA"
    jd = parse(text)
    assert jd.raw_text == text
