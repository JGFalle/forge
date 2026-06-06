"""Structural tests for the Phase 0 process_jd extraction.

These cover the new dataclasses (ProcessOptions / ProcessResult / AutoDecisions)
and the contract that mid-flow aborts return a ProcessResult instead of calling
sys.exit. They make zero API calls: the early pipeline stages are monkeypatched
so process_jd reaches a skip/return point before any network work.
"""

from __future__ import annotations

import pytest

from pipeline.core import (
    FAILURE,
    SKIP,
    SUCCESS,
    AutoDecisions,
    ProcessOptions,
    ProcessPrompts,
    ProcessResult,
)

# ── Dataclass behavior ───────────────────────────────────────────────────────

def test_process_result_outcome_properties():
    assert ProcessResult(SUCCESS).succeeded is True
    assert ProcessResult(SUCCESS).skipped is False
    assert ProcessResult(SUCCESS).failed is False

    assert ProcessResult(SKIP).skipped is True
    assert ProcessResult(SKIP).succeeded is False

    assert ProcessResult(FAILURE).failed is True
    assert ProcessResult(FAILURE).succeeded is False


def test_process_result_carries_skip_reason_and_error():
    skip = ProcessResult(SKIP, company="Acme", role="Director", skip_reason="dry_run")
    assert skip.skip_reason == "dry_run"
    assert skip.error == ""

    fail = ProcessResult(FAILURE, error="ghost_non_interactive")
    assert fail.error == "ghost_non_interactive"
    assert fail.skip_reason == ""


def test_process_options_defaults_are_interactive():
    opts = ProcessOptions()
    assert opts.interactive is True
    assert isinstance(opts.auto_decisions, AutoDecisions)
    assert isinstance(opts.prompts, ProcessPrompts)
    # Each ProcessOptions gets its own AutoDecisions/ProcessPrompts instance.
    assert ProcessOptions().auto_decisions is not opts.auto_decisions


def test_auto_decisions_defaults_match_north_star():
    auto = AutoDecisions()
    # Bulk must not silently push past ghost/hard-pass gates.
    assert auto.proceed_on_ghost_high is False
    assert auto.override_hard_pass is False
    # Exec summary off by default (cost); gdrive copy on (it's the point of bulk).
    assert auto.exec_summary is False
    assert auto.gdrive_copy is True


# ── process_jd returns rather than exits ─────────────────────────────────────

class _FakeJD:
    def __init__(self):
        self.company = "Acme Corp"
        self.role = "Director of Operations"
        self.raw_text = "job description text"
        self.salary_range = "$200K"
        self.location = "Atlanta, GA"
        self.req_number = ""
        self.ats_system = ""
        self.apply_by_date = ""


@pytest.fixture
def _stub_ingest(monkeypatch):
    """Stub ingest + duplicate-check so process_jd starts at the viability stage."""
    import pipeline.core as core
    import pipeline.ingest.jd_parser as jd_parser
    import pipeline.ingest.pdf_reader as pdf_reader

    monkeypatch.setattr(pdf_reader, "extract_text", lambda _p: "raw text")
    monkeypatch.setattr(jd_parser, "parse", lambda _t: _FakeJD())
    monkeypatch.setattr(core, "_check_for_duplicate", lambda *a, **k: None)


def _make_assessment(verdict: str):
    from pipeline.assessment.fit_assessor import DimensionScore, FitAssessment

    dim = DimensionScore(score=5, rationale="ok")
    return FitAssessment(
        verdict=verdict,
        overall_score=5,
        identity_alignment=dim,
        scope_level=dim,
        comp_alignment=dim,
        company_tier=dim,
        hard_filter_triggered=False,
        hard_filter_reason=None,
        gut_check_questions=[],
        summary="summary",
    )


def test_ghost_high_decline_returns_skip_not_exit(monkeypatch, _stub_ingest, tmp_path):
    """A declined ghost-risk prompt returns SKIP rather than raising SystemExit."""
    import pipeline.research.viability_checker as vc

    monkeypatch.setattr(vc, "check", lambda *a, **k: {"signals": ["repost 90d"]})
    monkeypatch.setattr(vc, "display", lambda *a, **k: None)
    monkeypatch.setattr(vc, "should_block", lambda *a, **k: True)

    from pipeline import core

    opts = ProcessOptions(
        interactive=True,
        prompts=ProcessPrompts(ghost_proceed=lambda _s: False),
    )
    result = core.process_jd(tmp_path / "jd.pdf", options=opts)

    assert result.outcome == SKIP
    assert result.skip_reason == "ghost_declined"
    assert result.company == "Acme Corp"


def test_ghost_high_non_interactive_returns_failure(monkeypatch, _stub_ingest, tmp_path):
    """A non-interactive ghost prompt (callback returns None) yields FAILURE."""
    import pipeline.research.viability_checker as vc

    monkeypatch.setattr(vc, "check", lambda *a, **k: {"signals": []})
    monkeypatch.setattr(vc, "display", lambda *a, **k: None)
    monkeypatch.setattr(vc, "should_block", lambda *a, **k: True)

    from pipeline import core

    opts = ProcessOptions(
        interactive=True,
        prompts=ProcessPrompts(ghost_proceed=lambda _s: None),
    )
    result = core.process_jd(tmp_path / "jd.pdf", options=opts)

    assert result.outcome == FAILURE
    assert result.error == "ghost_non_interactive"


def test_ghost_high_bulk_skips_without_callback(monkeypatch, _stub_ingest, tmp_path):
    """Non-interactive (bulk) ghost-HIGH skips via auto_decisions, no prompt."""
    import pipeline.research.viability_checker as vc

    monkeypatch.setattr(vc, "check", lambda *a, **k: {"signals": ["multi-city"]})
    monkeypatch.setattr(vc, "display", lambda *a, **k: None)
    monkeypatch.setattr(vc, "should_block", lambda *a, **k: True)

    from pipeline import core

    opts = ProcessOptions(interactive=False, auto_decisions=AutoDecisions())
    result = core.process_jd(tmp_path / "jd.pdf", options=opts)

    assert result.outcome == SKIP
    assert result.skip_reason == "ghost_high"


def test_hard_pass_decline_returns_skip(monkeypatch, _stub_ingest, tmp_path):
    """A declined HARD_PASS override returns SKIP carrying the verdict."""
    import pipeline.assessment.fit_assessor as fa
    import pipeline.research.viability_checker as vc

    monkeypatch.setattr(vc, "check", lambda *a, **k: {"signals": []})
    monkeypatch.setattr(vc, "display", lambda *a, **k: None)
    monkeypatch.setattr(vc, "should_block", lambda *a, **k: False)
    monkeypatch.setattr(fa, "assess", lambda *a, **k: _make_assessment("HARD_PASS"))
    monkeypatch.setattr(fa, "display", lambda *a, **k: None)

    from pipeline import core

    opts = ProcessOptions(
        interactive=True,
        prompts=ProcessPrompts(hard_pass_override=lambda: (False, "")),
    )
    result = core.process_jd(tmp_path / "jd.pdf", options=opts)

    assert result.outcome == SKIP
    assert result.skip_reason == "hard_pass_declined"
    assert result.fit_verdict == "HARD_PASS"


def test_hard_pass_bulk_skips(monkeypatch, _stub_ingest, tmp_path):
    """Non-interactive HARD_PASS skips by default (override_hard_pass False)."""
    import pipeline.assessment.fit_assessor as fa
    import pipeline.research.viability_checker as vc

    monkeypatch.setattr(vc, "check", lambda *a, **k: {"signals": []})
    monkeypatch.setattr(vc, "display", lambda *a, **k: None)
    monkeypatch.setattr(vc, "should_block", lambda *a, **k: False)
    monkeypatch.setattr(fa, "assess", lambda *a, **k: _make_assessment("HARD_PASS"))
    monkeypatch.setattr(fa, "display", lambda *a, **k: None)

    from pipeline import core

    opts = ProcessOptions(interactive=False)
    result = core.process_jd(tmp_path / "jd.pdf", options=opts)

    assert result.outcome == SKIP
    assert result.skip_reason == "hard_pass"
