"""Tests for pipeline/research/viability_checker.py"""

import pytest
from unittest.mock import patch, MagicMock
from pipeline.research.viability_checker import (
    check, display, should_block, _skipped, _structure_findings,
)


# _skipped

def test_skipped_has_correct_shape():
    s = _skipped()
    assert s["skipped"] is True
    assert s["ghost_risk"] == "unknown"
    assert isinstance(s["signals"], list)
    assert s["recommendation"] == "proceed"


# should_block

def test_should_block_high_risk():
    result = {"ghost_risk": "high", "skipped": False}
    assert should_block(result) is True


def test_should_block_medium_risk():
    result = {"ghost_risk": "medium", "skipped": False}
    assert should_block(result) is False


def test_should_block_low_risk():
    result = {"ghost_risk": "low", "skipped": False}
    assert should_block(result) is False


def test_should_block_skipped():
    assert should_block(_skipped()) is False


# check - no Perplexity key → skipped

def test_check_skips_without_perplexity_key():
    with patch.dict("os.environ", {}, clear=True):
        result = check("Acme Corp", "Director of Operations")
    assert result["skipped"] is True


# check, Perplexity available, haiku structures result

def _mock_viability_result():
    return {
        "ghost_risk": "low",
        "freshness_verdict": "active",
        "signals": [],
        "positive_signals": ["Company actively hiring at Director level per LinkedIn"],
        "recommendation": "proceed",
        "skipped": False,
    }


def test_check_returns_structured_result():
    with patch.dict("os.environ", {"PERPLEXITY_API_KEY": "fake-key", "ANTHROPIC_API_KEY": "fake-key"}):
        with patch("pipeline.research.viability_checker._fetch_viability_research", return_value="Company is actively hiring."):
            with patch("pipeline.research.viability_checker._structure_findings", return_value=_mock_viability_result()):
                result = check("Acme Corp", "Director of Operations")
    assert result["skipped"] is False
    assert result["ghost_risk"] == "low"
    assert "recommendation" in result


def test_check_skips_when_perplexity_returns_empty():
    with patch.dict("os.environ", {"PERPLEXITY_API_KEY": "fake-key"}):
        with patch("pipeline.research.viability_checker._fetch_viability_research", return_value=""):
            result = check("Acme Corp", "Director")
    assert result["skipped"] is True


# display, smoke tests

def test_display_skipped_prints_nothing(capsys):
    display(_skipped())
    out = capsys.readouterr().out
    assert out == ""


def test_display_low_risk(capsys):
    result = {
        "skipped": False,
        "ghost_risk": "low",
        "freshness_verdict": "active",
        "signals": [],
        "positive_signals": ["Actively hiring per LinkedIn"],
        "recommendation": "proceed",
    }
    display(result)
    out = capsys.readouterr().out
    assert "LOW" in out
    assert "ACTIVE" in out


def test_display_high_risk_shows_signals(capsys):
    result = {
        "skipped": False,
        "ghost_risk": "high",
        "freshness_verdict": "stale",
        "signals": ["Company announced 500-person layoff in March 2026"],
        "positive_signals": [],
        "recommendation": "verify_before_applying",
    }
    display(result)
    out = capsys.readouterr().out
    assert "HIGH" in out
    assert "layoff" in out


def test_display_medium_shows_caution(capsys):
    result = {
        "skipped": False,
        "ghost_risk": "medium",
        "freshness_verdict": "unknown",
        "signals": ["Posting has been live for 90 days without modification"],
        "positive_signals": [],
        "recommendation": "proceed_with_caution",
    }
    display(result)
    out = capsys.readouterr().out
    assert "caution" in out.lower() or "MEDIUM" in out
