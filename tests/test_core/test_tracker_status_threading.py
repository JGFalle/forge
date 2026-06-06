"""Stage 10 tracker-status threading.

These prove that `process_jd` passes `options.tracker_status` into the SINGLE
Stage 10 `add_entry` call, so the new tracker entry gets the intended status.
This is the gap that let the live bulk bug through: the orchestrator unit tests
mocked `process_jd`, so its internal `add_entry` never ran in tests, and nobody
asserted the status `process_jd` itself writes.

To reach Stage 10 without any API call, every API-backed stage between ingest
and the tracker write is monkeypatched. `add_entry` is replaced with a SPY so we
assert the exact `status` kwarg it receives (no real tracker is touched).
"""

from __future__ import annotations

import pytest

from pipeline.core import ProcessOptions

# ── ProcessOptions default ───────────────────────────────────────────────────


def test_tracker_status_defaults_to_prompted():
    """Default preserves single-JD behavior: new entries are 'prompted'."""
    assert ProcessOptions().tracker_status == "prompted"


# ── Drive process_jd to Stage 10 with everything stubbed ─────────────────────


class _FakeJD:
    company = "Acme Corp"
    role = "Director of Operations"
    raw_text = "job description text with many words"
    salary_range = "$200K"
    location = "Atlanta, GA"
    req_number = "REQ-1"
    ats_system = "Workday"
    apply_by_date = "2026-07-01"


def _make_assessment(verdict: str = "STRETCH"):
    from pipeline.assessment.fit_assessor import DimensionScore, FitAssessment

    dim = DimensionScore(score=7, rationale="ok")
    return FitAssessment(
        verdict=verdict,
        overall_score=7,
        identity_alignment=dim,
        scope_level=dim,
        comp_alignment=dim,
        company_tier=dim,
        hard_filter_triggered=False,
        hard_filter_reason=None,
        gut_check_questions=[],
        summary="summary",
    )


@pytest.fixture
def add_entry_spy(monkeypatch):
    """Replace tracker.add_entry with a spy capturing its kwargs."""
    import pipeline.tracker.tracker as trk

    calls: list[dict] = []

    def spy(**kwargs):
        calls.append(kwargs)
        return {"company": kwargs.get("company"), "status": kwargs.get("status")}

    monkeypatch.setattr(trk, "add_entry", spy)
    return calls


@pytest.fixture
def stub_pipeline_to_stage10(monkeypatch, tmp_path):
    """Stub every API/IO stage so process_jd runs ingest -> Stage 10 offline.

    No network, no real Drive copy, no real tracker. Stage 10's `add_entry` is
    spied separately; the gdrive copy + submit prompt after it are disabled.
    """
    import json as _json

    import pipeline.assessment.fit_assessor as fa
    import pipeline.core as core
    import pipeline.cover_letter.generate as cl_gen
    import pipeline.cover_letter.validator as cl_val
    import pipeline.ingest.jd_parser as jd_parser
    import pipeline.ingest.pdf_reader as pdf_reader
    import pipeline.output.folder_manager as fm
    import pipeline.output.resume_mods as resume_mods
    import pipeline.people_intel.outreach_extractor as outreach
    import pipeline.people_intel.pdf_renderer as pdf_renderer
    import pipeline.research.intel_generator as intel_gen
    import pipeline.research.keyword_gap as kgap
    import pipeline.research.perplexity_client as perplexity
    import pipeline.research.prompt_builder as prompt_builder
    import pipeline.research.tailor_generator as tailor_gen
    import pipeline.tailoring.ats_checker as ats
    import pipeline.tailoring.json_validator as json_validator
    import pipeline.tailoring.tailor as tailor

    # Stage 1: ingest
    monkeypatch.setattr(pdf_reader, "extract_text", lambda _p: "raw text")
    monkeypatch.setattr(jd_parser, "parse", lambda _t: _FakeJD())
    monkeypatch.setattr(core, "_check_for_duplicate", lambda *a, **k: None)
    # _FakeJD posts a salary, so the salary_intel fetch branch is skipped (no API).

    # Stage 1.5: viability check, no block
    import pipeline.research.viability_checker as vc
    monkeypatch.setattr(vc, "check", lambda *a, **k: {"signals": []})
    monkeypatch.setattr(vc, "display", lambda *a, **k: None)
    monkeypatch.setattr(vc, "should_block", lambda *a, **k: False)

    # Stage 2: fit assessment -> STRETCH (proceeds without prompt under non-interactive)
    monkeypatch.setattr(fa, "assess", lambda *a, **k: _make_assessment("STRETCH"))
    monkeypatch.setattr(fa, "display", lambda *a, **k: None)

    # Stage 3: folder + prompts (write a real tailoring JSON for later reads)
    app_folder = tmp_path / "app"
    for sub in ("research", "tailoring_json", "people_intel", "resume", "cover_letter", "jd"):
        (app_folder / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fm, "create_application_folder", lambda *a, **k: app_folder)
    monkeypatch.setattr(fm, "copy_file", lambda *a, **k: None)
    monkeypatch.setattr(core, "_save_assessment", lambda *a, **k: None)
    monkeypatch.setattr(prompt_builder, "build_tailoring_prompt", lambda *a, **k: "p")
    monkeypatch.setattr(prompt_builder, "build_people_intel_prompt", lambda *a, **k: "p")
    monkeypatch.setattr(prompt_builder, "write_prompts", lambda *a, **k: None)

    tailoring_path = app_folder / "tailoring_json" / "tailoring.json"
    tailoring_path.write_text(
        _json.dumps({"summary": "s", "cover_letter": "c"}), encoding="utf-8"
    )

    # Stage 4: tailoring JSON
    monkeypatch.setattr(
        tailor_gen, "generate", lambda *a, **k: (None, tailoring_path)
    )
    # Stage 4a: gap + grammar pre-scan
    monkeypatch.setattr(
        kgap, "compute_gap",
        lambda *a, **k: {"coverage_pct": 90.0, "missing": []},
    )
    monkeypatch.setattr(json_validator, "grammar_check", lambda *a, **k: [])
    # Council off: PERPLEXITY_API_KEY unset -> branch skipped
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)

    # Stage 5: people intel
    monkeypatch.setattr(perplexity, "fetch_company_intel", lambda *a, **k: "")
    intel_md = app_folder / "people_intel" / "intel.md"
    intel_md.write_text("# Intel\nNo hiring manager here.\n", encoding="utf-8")
    monkeypatch.setattr(intel_gen, "generate", lambda *a, **k: intel_md)
    monkeypatch.setattr(outreach, "extract", lambda *a, **k: [])
    monkeypatch.setattr(outreach, "save", lambda *a, **k: None)
    monkeypatch.setattr(outreach, "display", lambda *a, **k: None)

    # Stage 6: resume tailoring + ATS
    monkeypatch.setattr(tailor, "run", lambda *a, **k: None)  # no resume -> skip ATS
    monkeypatch.setattr(ats, "check", lambda *a, **k: {})
    monkeypatch.setattr(ats, "display_report", lambda *a, **k: None)
    monkeypatch.setattr(ats, "save_report", lambda *a, **k: None)

    # Stage 7: cover letter (none -> skip quality gate)
    monkeypatch.setattr(cl_gen, "run", lambda *a, **k: None)
    monkeypatch.setattr(cl_gen, "run_txt", lambda *a, **k: None)
    monkeypatch.setattr(cl_val, "validate", lambda *a, **k: {})
    monkeypatch.setattr(cl_val, "display_result", lambda *a, **k: None)
    monkeypatch.setattr(cl_val, "save_result", lambda *a, **k: None)

    # Stage 8: intel PDF
    monkeypatch.setattr(pdf_renderer, "run", lambda *a, **k: app_folder / "intel.pdf")

    # Stage 9: gap report
    monkeypatch.setattr(kgap, "display_gap", lambda *a, **k: None)
    monkeypatch.setattr(kgap, "save_gap_report", lambda *a, **k: None)

    # Stage 9b: resume mods PDF
    monkeypatch.setattr(
        resume_mods, "generate", lambda *a, **k: app_folder / "mods.pdf"
    )

    return app_folder


def _opts(**overrides) -> ProcessOptions:
    base = {
        "company": "Acme Corp",
        "role": "Director of Operations",
        "context": "",
        "interactive": False,
        "gdrive_target": "",
    }
    base.update(overrides)
    opts = ProcessOptions(**base)
    # exec summary + gdrive copy off so nothing runs after Stage 10.
    opts.auto_decisions.exec_summary = False
    opts.auto_decisions.gdrive_copy = False
    return opts


def test_process_jd_threads_in_que_status_into_add_entry(
    stub_pipeline_to_stage10, add_entry_spy, tmp_path
):
    """tracker_status='in_que' -> the single add_entry is called with status=in_que."""
    from pipeline import core

    result = core.process_jd(
        tmp_path / "jd.pdf", options=_opts(tracker_status="in_que")
    )

    assert result.succeeded
    # Exactly one add_entry, with the threaded status.
    assert len(add_entry_spy) == 1
    assert add_entry_spy[0]["status"] == "in_que"
    assert add_entry_spy[0]["company"] == "Acme Corp"


def test_process_jd_defaults_to_prompted_status_into_add_entry(
    stub_pipeline_to_stage10, add_entry_spy, tmp_path
):
    """Default (single-JD) path: add_entry is called with status='prompted'."""
    from pipeline import core

    result = core.process_jd(tmp_path / "jd.pdf", options=_opts())

    assert result.succeeded
    assert len(add_entry_spy) == 1
    assert add_entry_spy[0]["status"] == "prompted"
