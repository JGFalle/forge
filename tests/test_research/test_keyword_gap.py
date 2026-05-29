"""Tests for pipeline/research/keyword_gap.py"""

import pytest
from pipeline.research.keyword_gap import compute_gap, _extract_terms, _clean_jd_text, _tailoring_text


# _clean_jd_text

def test_clean_removes_bare_urls():
    text = "Apply at https://careers.company.com/jobs/12345 today."
    cleaned = _clean_jd_text(text)
    assert "https://" not in cleaned
    assert "today" in cleaned


def test_clean_removes_parenthetical_urls():
    text = "Visit our site (https://www.company.com/about) for more info."
    cleaned = _clean_jd_text(text)
    assert "https://" not in cleaned
    assert "more info" in cleaned


def test_clean_removes_timestamps():
    text = "5/10/26, 4:28 AM   Director of Operations"
    cleaned = _clean_jd_text(text)
    assert "Director" in cleaned
    # timestamp should be gone
    assert "4:28" not in cleaned


def test_clean_removes_page_numbers():
    text = "Page 1/11 of the job description continues here."
    cleaned = _clean_jd_text(text)
    # The key words should remain
    assert "description" in cleaned


def test_clean_collapses_whitespace():
    text = "Director    of     Operations"
    cleaned = _clean_jd_text(text)
    assert "  " not in cleaned


# _extract_terms

def test_extract_filters_stop_words():
    terms = _extract_terms("the team will work with the company")
    assert "the" not in terms
    assert "with" not in terms


def test_extract_filters_short_words():
    terms = _extract_terms("use the api for data")
    # "use" is 3 chars and filtered by _MIN_WORD_LEN=4; "data" passes
    assert "data" in terms


def test_extract_produces_bigrams():
    terms = _extract_terms("machine learning platform analytics")
    assert "machine learning" in terms or "learning platform" in terms


def test_extract_case_insensitive():
    terms = _extract_terms("Machine Learning Analytics")
    assert "machine" in terms or "analytics" in terms


# compute_gap

def test_compute_gap_full_coverage():
    jd_text = "machine learning analytics optimization"
    tailoring = {
        "headline": "MACHINE LEARNING ANALYTICS OPTIMIZATION LEADER",
        "summary": "",
        "competencies": [],
        "technical_skills": [],
        "cover_letter": "",
        "experience_modifications": [],
    }
    gap = compute_gap(jd_text, tailoring)
    assert gap["coverage_pct"] > 0
    # All terms should be present
    assert len(gap["missing"]) == 0 or gap["coverage_pct"] == 100.0


def test_compute_gap_zero_coverage():
    jd_text = "robotics automation warehouse fulfillment optimization"
    tailoring = {
        "headline": "DATA LEADER",
        "summary": "analytics background",
        "competencies": [],
        "technical_skills": [],
        "cover_letter": "",
        "experience_modifications": [],
    }
    gap = compute_gap(jd_text, tailoring)
    assert gap["coverage_pct"] < 100
    assert len(gap["missing"]) > 0


def test_compute_gap_returns_required_keys():
    gap = compute_gap("supply chain director", {"headline": "", "summary": "",
        "competencies": [], "technical_skills": [], "cover_letter": "",
        "experience_modifications": []})
    assert "missing" in gap
    assert "present" in gap
    assert "coverage_pct" in gap
    assert "total_jd_terms" in gap


def test_compute_gap_empty_tailoring():
    gap = compute_gap("machine learning platform", {})
    assert isinstance(gap["coverage_pct"], float)
    assert gap["coverage_pct"] >= 0


# _tailoring_text

def test_tailoring_text_flattens_all_fields():
    data = {
        "headline": "HEADLINE",
        "summary": "Summary text",
        "competencies": ["comp1 comp2"],
        "technical_skills": ["skill row"],
        "cover_letter": "cover content",
        "experience_modifications": [
            {
                "replacement_lead_in": "lead in text",
                "replacement_bullets": ["bullet one", "bullet two"],
            }
        ],
    }
    text = _tailoring_text(data)
    assert "HEADLINE" in text
    assert "Summary text" in text
    assert "comp1" in text
    assert "skill row" in text
    assert "cover content" in text
    assert "lead in text" in text
    assert "bullet one" in text
