"""Orchestrator tests — bulk batch wiring against tmp_path.

`process_jd` is MONKEYPATCHED in every test so NO API is called and NO real
Drive is touched. The Que and Applications dirs live under tmp_path. The tracker
path is redirected to tmp_path so no real tracker is mutated. `csv_sync.sync` is
patched to a counter.

Layout used everywhere:
    tmp/Applications (by company)/
        Que/<Company>/<role>.pdf
A discovered company's permanent home is `tmp/Applications (by company)/<Company>/`
(i.e. `que.parent / <Company>`), matching the orchestrator's `apps_dir = que.parent`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.bulk import orchestrator, report
from pipeline.core import FAILURE, SKIP, SUCCESS, ProcessResult

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def que(tmp_path):
    """A real on-disk Que under an Applications root, returned as a Path."""
    apps = tmp_path / "Applications (by company)"
    que_dir = apps / "Que"
    que_dir.mkdir(parents=True)
    return que_dir


def _make_company(que_dir: Path, company: str, jd_names: list[str]) -> Path:
    folder = que_dir / company
    folder.mkdir(parents=True, exist_ok=True)
    for name in jd_names:
        # Non-zero content so verify_copy passes on the live path.
        (folder / name).write_bytes(b"%PDF-1.4 fake jd\n")
    return folder


@pytest.fixture
def patch_que(monkeypatch, que):
    """Make orchestrator._resolve_que return our tmp Que (read-only resolve)."""
    monkeypatch.setattr(orchestrator.discovery, "que_dir", lambda: que)
    return que


@pytest.fixture
def role_map(monkeypatch):
    """Deterministic role ingest keyed by JD filename stem. No PDF parsing."""
    mapping: dict[str, str] = {}

    def fake_ingest(jd_pdf: Path) -> str:
        return mapping.get(jd_pdf.name, jd_pdf.stem)

    monkeypatch.setattr(orchestrator, "_ingest_role", fake_ingest)
    return mapping


@pytest.fixture
def tracker_to_tmp(monkeypatch, tmp_path):
    """Redirect the tracker JSON to tmp so add_entry never touches real Drive."""
    from pipeline.tracker import tracker as trk

    json_path = tmp_path / "application_tracker.json"
    monkeypatch.setattr(
        trk, "get", lambda key, default=None: {
            "paths.tracker_file": str(json_path),
        }.get(key, default)
    )
    return json_path


@pytest.fixture
def sync_counter(monkeypatch):
    """Count csv_sync.sync() calls; never run the real sync."""
    calls = {"n": 0}
    import pipeline.tracker.csv_sync as cs

    def fake_sync():
        calls["n"] += 1
        return {"csv": Path("x.csv"), "html": Path("x.html"), "changed": 0, "added": 0}

    monkeypatch.setattr(cs, "sync", fake_sync)
    return calls


def _success_result(company, role, app_folder):
    return ProcessResult(
        SUCCESS, company=company, role=role, app_folder=app_folder,
        fit_verdict="STRETCH", fit_score=7.0,
    )


def _simulate_process_success(jd_pdf, *, options):
    """Stand in for a SUCCESSFUL `process_jd`.

    Mirrors the two side effects the orchestrator now relies on the real
    `process_jd` to perform: (1) write the completion sentinel into the dest, and
    (2) write the tracker entry with `status=options.tracker_status` (the single
    Stage 10 `add_entry`). The orchestrator no longer adds the tracker entry
    itself, so a mock that omits this would never write one.
    """
    from pipeline.bulk.lifecycle import mark_deliverables_complete
    from pipeline.tracker.tracker import add_entry

    dest = Path(options.gdrive_target)
    mark_deliverables_complete(dest)
    add_entry(
        company=options.company,
        role=options.role,
        app_folder=str(dest),
        fit_verdict="STRETCH",
        fit_score=7,
        status=options.tracker_status,
    )
    return _success_result(options.company, options.role, dest)


# ── Tracker-status threading (the bug this fixes) ────────────────────────────


def test_bulk_options_request_in_que_status(tmp_path):
    """Bulk builds ProcessOptions with tracker_status='in_que' so process_jd's
    single Stage 10 add_entry writes the entry as in_que directly."""
    opts = orchestrator._bulk_options("HP", "Director, Planning CI", tmp_path / "HP")
    assert opts.tracker_status == "in_que"
    # Sanity: still non-interactive bulk with ghost/hard-pass -> SKIP, exec off.
    assert opts.interactive is False
    assert opts.auto_decisions.proceed_on_ghost_high is False
    assert opts.auto_decisions.override_hard_pass is False


def test_no_separate_post_hoc_in_que_add_entry_remains():
    """The redundant orchestrator-side in_que add_entry (`_add_in_que`) is gone.

    The in_que write now happens inside process_jd's single add_entry, driven by
    options.tracker_status. A second post-hoc add_entry would be a no-op anyway
    (defeated by the Phase 1 downgrade guard) and must not be re-introduced.
    """
    assert not hasattr(orchestrator, "_add_in_que")


def test_live_success_writes_in_que_via_process_jd_not_orchestrator(
    patch_que, que, role_map, tracker_to_tmp, sync_counter, monkeypatch
):
    """REGRESSION: the in_que entry must come from process_jd's threaded status.

    The orchestrator no longer calls add_entry itself. We assert (1) the
    orchestrator passes tracker_status='in_que' into process_jd, and (2) the only
    tracker entry written has status 'in_que' — coming from that single threaded
    write, not a post-hoc orchestrator add.
    """
    _make_company(que, "HP", ["hp.pdf"])
    role_map["hp.pdf"] = "Director, Planning CI"

    seen_status: list[str] = []

    def fake_process(jd_pdf, *, options):
        seen_status.append(options.tracker_status)
        return _simulate_process_success(jd_pdf, options=options)

    monkeypatch.setattr("pipeline.core.process_jd", fake_process)

    orchestrator.run_bulk(dry_run=False, limit=None)

    assert seen_status == ["in_que"]
    from pipeline.tracker.tracker import load
    entries = load()
    assert len(entries) == 1
    assert entries[0]["status"] == "in_que"


def test_old_bug_path_would_have_stranded_prompted(tracker_to_tmp):
    """REGRESSION pin for the OLD bug: writing 'prompted' first then a separate
    in_que add_entry leaves the entry stuck at 'prompted' (downgrade guard).

    This proves WHY the single-write fix is needed: the two-write path the
    orchestrator used to do cannot reach in_que. The new path (one add_entry with
    status='in_que') does."""
    from pipeline.tracker.tracker import add_entry, load

    # Old path: process_jd wrote prompted, then orchestrator tried in_que.
    add_entry(company="HP", role="Director", status="prompted")
    add_entry(company="HP", role="Director", status="in_que")  # defeated by guard
    assert load()[0]["status"] == "prompted"  # the bug: stuck at prompted

    # New path: a fresh company, single write as in_que -> in_que.
    add_entry(company="Clorox", role="Director", status="in_que")
    new = next(e for e in load() if e["company"] == "Clorox")
    assert new["status"] == "in_que"


# ── Dry-run ──────────────────────────────────────────────────────────────────


def test_dry_run_writes_nothing(patch_que, que, role_map, monkeypatch):
    """Dry-run discovers, plans, writes nothing: no folders, no tracker, no sync."""
    _make_company(que, "HP", ["director_ci.pdf"])
    _make_company(que, "Clorox", ["dir_csc.pdf", "sr_mgr_network.pdf"])
    role_map.update({
        "director_ci.pdf": "Director, Planning Continuous Improvement",
        "dir_csc.pdf": "Director, Customer Supply Chain",
        "sr_mgr_network.pdf": "Senior Manager - Network Planning",
    })

    # Guard: process_jd / add_entry / sync must NEVER be called in dry-run.
    monkeypatch.setattr(
        "pipeline.core.process_jd",
        lambda *a, **k: pytest.fail("process_jd called in dry-run"),
    )

    rep = orchestrator.run_bulk(dry_run=True, limit=None)

    assert rep.dry_run is True
    assert rep.companies_discovered == 2
    assert rep.companies_processed == 2
    assert rep.total_jds == 3
    assert rep.planned == 3
    assert rep.sync_ran is False

    # Apps root has ONLY the Que folder — no company homes were created.
    apps_dir = que.parent
    created = {p.name for p in apps_dir.iterdir()}
    assert created == {"Que"}
    # Que PDFs untouched.
    assert (que / "HP" / "director_ci.pdf").exists()
    assert (que / "Clorox" / "dir_csc.pdf").exists()

    text = report.format_report(rep)
    assert "DRY RUN" in text
    assert "would add in_que" in text


def test_dry_run_resolves_role_from_filename_when_parse_empty(patch_que, que, monkeypatch):
    """When the local parser finds no role, the filename-derived role is used.

    This is the Reader-View case: the regex parser returns "" but the filename
    stem (marker stripped) is a clean, readable role. The dry-run plan must show
    it, not "(role unparsed)"/"Untitled".
    """
    _make_company(que, "Mystery", ["Director, Supply Chain __ Reader View.pdf"])
    # Local parser returns nothing; resolver should fall back to the filename.
    monkeypatch.setattr(orchestrator, "_ingest_role", lambda jd: "")

    rep = orchestrator.run_bulk(dry_run=True, limit=None)
    assert rep.total_jds == 1
    assert rep.results[0].role == "Director, Supply Chain"
    text = report.format_report(rep)
    assert "Director, Supply Chain" in text
    assert "(role unparsed)" not in text


def test_dry_run_real_reader_view_clorox_4_distinct_readable_subfolders(
    patch_que, que, monkeypatch
):
    """The real Reader-View Clorox names yield 4 distinct READABLE subfolders.

    Uses the REAL resolver (no role_map): fake-byte PDFs make the local parser
    return "" so `_resolve_role` falls back to the filename (marker stripped).
    The dry-run plan must show real roles, not "(role unparsed)"/"Untitled", and
    Clorox's 4 JDs must plan to 4 distinct, human-readable subfolders.
    """
    _make_company(que, "Clorox", [
        "Director - Logistics, Value Transformation Office __ Reader View.pdf",
        "Director, Customer Supply Chain __ Reader View.pdf",
        "Senior Manager – Network Planning Process Excellence __ Reader View.pdf",
        "Senior Manager – S&OE Process Excellence __ Reader View.pdf",
    ])
    # process_jd must never run in dry-run.
    monkeypatch.setattr(
        "pipeline.core.process_jd",
        lambda *a, **k: pytest.fail("process_jd called in dry-run"),
    )

    rep = orchestrator.run_bulk(dry_run=True, limit=None)

    assert rep.total_jds == 4
    roles = {r.role for r in rep.results}
    assert roles == {
        "Director - Logistics, Value Transformation Office",
        "Director, Customer Supply Chain",
        "Senior Manager – Network Planning Process Excellence",
        "Senior Manager – S&OE Process Excellence",
    }
    # 4 distinct, non-Untitled dest subfolders.
    dest_names = {r.dest_dir.name for r in rep.results}
    assert len(dest_names) == 4
    assert all(name != "Untitled" for name in dest_names)

    text = report.format_report(rep)
    assert "(role unparsed)" not in text
    assert "Untitled" not in text
    assert "Director, Customer Supply Chain" in text


def test_live_options_role_set_from_resolved_filename_role(
    patch_que, que, tracker_to_tmp, sync_counter, monkeypatch
):
    """Live: options.role is the filename-resolved role and placement/tracker agree.

    No role_map: the real resolver feeds the filename role into process_jd via
    options.role, so the dest folder, result.role, and the tracker entry all use
    one readable role.
    """
    _make_company(que, "Clorox", [
        "Director, Customer Supply Chain __ Reader View.pdf",
        "Senior Manager – S&OE Process Excellence __ Reader View.pdf",
    ])
    seen_roles: list[str] = []
    seen_dests: list[str] = []

    def fake_process(jd_pdf, *, options):
        seen_roles.append(options.role)
        seen_dests.append(options.gdrive_target)
        # process_jd echoes options.role back as result.role (short-circuit) and
        # writes the tracker entry with options.tracker_status.
        return _simulate_process_success(jd_pdf, options=options)

    monkeypatch.setattr("pipeline.core.process_jd", fake_process)

    rep = orchestrator.run_bulk(dry_run=False, limit=None)

    assert rep.generated == 2
    assert set(seen_roles) == {
        "Director, Customer Supply Chain",
        "Senior Manager – S&OE Process Excellence",
    }
    # Dest folder names match the resolved roles (sanitized), not "Untitled".
    dest_names = {Path(d).name for d in seen_dests}
    assert "Untitled" not in dest_names
    assert len(dest_names) == 2

    # Tracker entries carry the readable roles.
    from pipeline.tracker.tracker import load
    tracked_roles = {e["role"] for e in load()}
    assert tracked_roles == {
        "Director, Customer Supply Chain",
        "Senior Manager – S&OE Process Excellence",
    }


# ── Live: single + multi placement ───────────────────────────────────────────


def test_live_single_jd_placed_and_finalized(
    patch_que, que, role_map, tracker_to_tmp, sync_counter, monkeypatch
):
    """Single-JD success: JD in company folder, in_que entry, Que finalized."""
    _make_company(que, "HP", ["director_ci.pdf"])
    role_map["director_ci.pdf"] = "Director, Planning Continuous Improvement"
    apps_dir = que.parent

    monkeypatch.setattr("pipeline.core.process_jd", _simulate_process_success)

    rep = orchestrator.run_bulk(dry_run=False, limit=None)

    assert rep.generated == 1
    hp = apps_dir / "HP"
    assert hp.is_dir()
    # JD copied into the company folder (single-JD: dest == company folder).
    assert (hp / "director_ci.pdf").exists()
    # Que company folder finalized (removed) since its only JD succeeded.
    assert not (que / "HP").exists()

    # Tracker got an in_que entry.
    from pipeline.tracker.tracker import load
    entries = load()
    assert len(entries) == 1
    assert entries[0]["status"] == "in_que"
    assert entries[0]["company"] == "HP"

    assert sync_counter["n"] == 1


def test_live_multi_jd_distinct_subfolders_with_seeded_taken(
    patch_que, que, role_map, tracker_to_tmp, sync_counter, monkeypatch
):
    """Clorox-style multi-JD: distinct subfolders; taken seeded from a pre-existing
    on-disk subfolder so a cross-run different-role collision is avoided."""
    _make_company(que, "Clorox", ["a.pdf", "b.pdf"])
    # Two roles that sanitize to the SAME folder name -> must diverge.
    role_map.update({"a.pdf": "Director / Logistics", "b.pdf": "Director  Logistics"})
    apps_dir = que.parent

    # Pre-existing on-disk subfolder from a prior run claiming "Director Logistics".
    company_dir = apps_dir / "Clorox"
    prior = company_dir / "Director Logistics"
    prior.mkdir(parents=True)
    (prior / ".forge_complete").write_text("old\n")  # NOT one of our incoming roles

    placed_targets: list[str] = []

    def fake_process(jd_pdf, *, options):
        placed_targets.append(options.gdrive_target)
        return _simulate_process_success(jd_pdf, options=options)

    monkeypatch.setattr("pipeline.core.process_jd", fake_process)

    rep = orchestrator.run_bulk(dry_run=False, limit=None)

    assert rep.generated == 2
    # Two distinct dest dirs, neither equal to the pre-existing one.
    dests = {Path(t) for t in placed_targets}
    assert len(dests) == 2
    assert prior not in dests
    for d in dests:
        assert d.parent == company_dir
        assert d.is_dir()


# ── Continue-on-error ────────────────────────────────────────────────────────


def test_continue_on_error_one_jd_fails_batch_completes(
    patch_que, que, role_map, tracker_to_tmp, sync_counter, monkeypatch
):
    """A JD that raises or returns FAILURE does not abort; failing company keeps
    its Que folder; other companies still processed."""
    _make_company(que, "Clorox", ["good.pdf", "bad.pdf"])
    _make_company(que, "HP", ["hp.pdf"])
    role_map.update({"good.pdf": "Good Role", "bad.pdf": "Bad Role", "hp.pdf": "HP Role"})
    apps_dir = que.parent

    def fake_process(jd_pdf, *, options):
        if jd_pdf.name == "bad.pdf":
            raise RuntimeError("ingest exploded")
        return _simulate_process_success(jd_pdf, options=options)

    monkeypatch.setattr("pipeline.core.process_jd", fake_process)

    rep = orchestrator.run_bulk(dry_run=False, limit=None)

    assert rep.generated == 2  # good.pdf + hp.pdf
    assert rep.failed == 1
    # Clorox keeps its Que folder (bad.pdf remains), but good.pdf was pruned.
    assert (que / "Clorox").exists()
    assert (que / "Clorox" / "bad.pdf").exists()
    assert not (que / "Clorox" / "good.pdf").exists()
    # HP fully done -> Que folder removed.
    assert not (que / "HP").exists()
    # HP still placed despite Clorox's failure.
    assert (apps_dir / "HP" / "hp.pdf").exists()


def test_failure_result_recorded_not_raised(
    patch_que, que, role_map, tracker_to_tmp, sync_counter, monkeypatch
):
    """A FAILURE ProcessResult (not a raise) is recorded and the batch continues."""
    _make_company(que, "Acme", ["x.pdf", "y.pdf"])
    role_map.update({"x.pdf": "X Role", "y.pdf": "Y Role"})

    def fake_process(jd_pdf, *, options):
        if jd_pdf.name == "x.pdf":
            return ProcessResult(FAILURE, company=options.company, role=options.role,
                                 error="empty text")
        return _simulate_process_success(jd_pdf, options=options)

    monkeypatch.setattr("pipeline.core.process_jd", fake_process)

    rep = orchestrator.run_bulk(dry_run=False, limit=None)
    assert rep.failed == 1
    assert rep.generated == 1
    assert rep.failures[0].detail == "empty text"
    assert (que / "Acme").exists()  # PARTIAL: x.pdf remains


# ── Ghost / hard-pass skips ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reason,kind,count_attr",
    [
        ("ghost_high", report.SKIP_GHOST, "skipped_ghost"),
        ("hard_pass", report.SKIP_HARD_PASS, "skipped_hard_pass"),
    ],
)
def test_skip_leaves_jd_in_que_no_tracker_advance(
    patch_que, que, role_map, tracker_to_tmp, sync_counter, monkeypatch,
    reason, kind, count_attr,
):
    """ghost-HIGH / hard-pass -> SKIP: JD left in Que, reported, no tracker entry."""
    _make_company(que, "GhostCo", ["g.pdf"])
    role_map["g.pdf"] = "Suspicious Role"

    def fake_process(jd_pdf, *, options):
        return ProcessResult(SKIP, company=options.company, role=options.role,
                             skip_reason=reason)

    monkeypatch.setattr("pipeline.core.process_jd", fake_process)

    rep = orchestrator.run_bulk(dry_run=False, limit=None)

    assert getattr(rep, count_attr) == 1
    assert rep.results[0].kind == kind
    # JD left in Que (not pruned), folder kept.
    assert (que / "GhostCo" / "g.pdf").exists()
    # No tracker entry, no sync (nothing generated).
    from pipeline.tracker.tracker import load
    assert load() == []
    assert sync_counter["n"] == 0
    # Surfaced loudly.
    assert rep.loud_skips and rep.loud_skips[0].company == "GhostCo"


# ── No stranded empty dest on SKIP ───────────────────────────────────────────


@pytest.mark.parametrize("reason", ["ghost_high", "hard_pass"])
def test_skip_leaves_no_empty_single_jd_dest(
    patch_que, que, role_map, tracker_to_tmp, sync_counter, monkeypatch, reason
):
    """Single-JD ghost/hard-pass SKIP must not strand an empty company folder."""
    _make_company(que, "GhostCo", ["g.pdf"])
    role_map["g.pdf"] = "Suspicious Role"
    apps_dir = que.parent

    def fake_process(jd_pdf, *, options):
        return ProcessResult(SKIP, company=options.company, role=options.role,
                             skip_reason=reason)

    monkeypatch.setattr("pipeline.core.process_jd", fake_process)

    rep = orchestrator.run_bulk(dry_run=False, limit=None)

    assert rep.loud_skips
    # No empty company folder left behind in Applications.
    assert not (apps_dir / "GhostCo").exists()
    # JD still in Que (not pruned).
    assert (que / "GhostCo" / "g.pdf").exists()


@pytest.mark.parametrize("reason", ["ghost_high", "hard_pass"])
def test_skip_leaves_no_empty_multi_jd_subfolder(
    patch_que, que, role_map, tracker_to_tmp, sync_counter, monkeypatch, reason
):
    """Multi-JD SKIP removes only the empty leaf subfolder; a successful sibling
    keeps its dest and the company folder survives."""
    _make_company(que, "Clorox", ["good.pdf", "skip.pdf"])
    role_map.update({"good.pdf": "Good Role", "skip.pdf": "Skip Role"})
    apps_dir = que.parent

    def fake_process(jd_pdf, *, options):
        if jd_pdf.name == "skip.pdf":
            return ProcessResult(SKIP, company=options.company, role=options.role,
                                 skip_reason=reason)
        return _simulate_process_success(jd_pdf, options=options)

    monkeypatch.setattr("pipeline.core.process_jd", fake_process)

    rep = orchestrator.run_bulk(dry_run=False, limit=None)

    assert rep.generated == 1
    company_dir = apps_dir / "Clorox"
    # Company folder survives (it has the successful sibling).
    assert company_dir.is_dir()
    # The skipped JD's subfolder was removed (no empty leaf stranded).
    assert (company_dir / "Skip Role").exists() is False
    # The successful JD's subfolder is present.
    assert (company_dir / "Good Role").is_dir()


def test_skip_does_not_remove_preexisting_company_folder(
    patch_que, que, role_map, tracker_to_tmp, sync_counter, monkeypatch
):
    """A single-JD ghost/hard-pass SKIP must never remove a PRE-EXISTING company
    folder (only an empty leaf we created this run)."""
    _make_company(que, "GhostCo", ["g.pdf"])
    role_map["g.pdf"] = "Suspicious Role"
    apps_dir = que.parent
    # Pre-existing company folder with prior content.
    company_dir = apps_dir / "GhostCo"
    company_dir.mkdir(parents=True)
    (company_dir / "prior.txt").write_text("keep me\n")

    def fake_process(jd_pdf, *, options):
        return ProcessResult(SKIP, company=options.company, role=options.role,
                             skip_reason="ghost_high")

    monkeypatch.setattr("pipeline.core.process_jd", fake_process)

    orchestrator.run_bulk(dry_run=False, limit=None)

    # Pre-existing folder + its content untouched.
    assert company_dir.is_dir()
    assert (company_dir / "prior.txt").read_text() == "keep me\n"


# ── Skip-if-present (sentinel) ───────────────────────────────────────────────


def test_skip_if_present_sentinel(
    patch_que, que, role_map, tracker_to_tmp, sync_counter, monkeypatch
):
    """A JD whose dest already has the completion sentinel is skipped + reported."""
    _make_company(que, "HP", ["hp.pdf"])
    role_map["hp.pdf"] = "HP Role"
    apps_dir = que.parent
    # Pre-create the company folder with a completion sentinel (single-JD: dest
    # is the company folder itself).
    company_dir = apps_dir / "HP"
    company_dir.mkdir(parents=True)
    (company_dir / ".forge_complete").write_text("done\n")

    monkeypatch.setattr(
        "pipeline.core.process_jd",
        lambda *a, **k: pytest.fail("process_jd called for an already-present JD"),
    )

    rep = orchestrator.run_bulk(dry_run=False, limit=None)

    assert rep.skipped_present == 1
    assert rep.generated == 0
    from pipeline.tracker.tracker import load
    assert load() == []
    assert sync_counter["n"] == 0


# ── Limit ────────────────────────────────────────────────────────────────────


def test_bulk_limit_one_company(
    patch_que, que, role_map, tracker_to_tmp, sync_counter, monkeypatch
):
    """--bulk-limit 1 processes only the first company (alphabetical)."""
    _make_company(que, "Alpha", ["a.pdf"])
    _make_company(que, "Beta", ["b.pdf"])
    role_map.update({"a.pdf": "A Role", "b.pdf": "B Role"})

    seen: list[str] = []

    def fake_process(jd_pdf, *, options):
        seen.append(options.company)
        return _simulate_process_success(jd_pdf, options=options)

    monkeypatch.setattr("pipeline.core.process_jd", fake_process)

    rep = orchestrator.run_bulk(dry_run=False, limit=1)

    assert rep.companies_discovered == 2
    assert rep.companies_processed == 1
    assert seen == ["Alpha"]
    # Beta untouched, still in Que.
    assert (que / "Beta" / "b.pdf").exists()


# ── End-of-batch sync (once, not per JD) ─────────────────────────────────────


def test_one_sync_at_end_not_per_jd(
    patch_que, que, role_map, tracker_to_tmp, sync_counter, monkeypatch
):
    """Multiple successful JDs -> exactly ONE csv_sync.sync() at the end."""
    _make_company(que, "Clorox", ["a.pdf", "b.pdf", "c.pdf"])
    role_map.update({"a.pdf": "Role A", "b.pdf": "Role B", "c.pdf": "Role C"})

    monkeypatch.setattr("pipeline.core.process_jd", _simulate_process_success)

    rep = orchestrator.run_bulk(dry_run=False, limit=None)
    assert rep.generated == 3
    assert sync_counter["n"] == 1


# ── Pre-bulk tracker auto-backup ─────────────────────────────────────────────


def _prebulk_backups(tracker_json: Path) -> list[Path]:
    return sorted(tracker_json.parent.glob(f"{tracker_json.stem}.prebulk_*{tracker_json.suffix}"))


def test_live_batch_creates_exactly_one_prebulk_backup(
    patch_que, que, role_map, tracker_to_tmp, sync_counter, monkeypatch
):
    """A live batch snapshots the tracker once before the first write."""
    # Seed an existing tracker so there is something to back up.
    tracker_to_tmp.write_text('[{"company": "Old", "role": "x"}]', encoding="utf-8")

    _make_company(que, "Clorox", ["a.pdf", "b.pdf"])
    role_map.update({"a.pdf": "Role A", "b.pdf": "Role B"})
    monkeypatch.setattr("pipeline.core.process_jd", _simulate_process_success)

    orchestrator.run_bulk(dry_run=False, limit=None)

    backups = _prebulk_backups(tracker_to_tmp)
    assert len(backups) == 1
    # The snapshot captured the PRE-run state (before bulk added entries).
    assert backups[0].read_text(encoding="utf-8") == '[{"company": "Old", "role": "x"}]'


def test_dry_run_creates_no_prebulk_backup(
    patch_que, que, role_map, tracker_to_tmp, monkeypatch
):
    """Dry-run never backs up the tracker (it writes nothing)."""
    tracker_to_tmp.write_text("[]", encoding="utf-8")
    _make_company(que, "HP", ["hp.pdf"])
    role_map["hp.pdf"] = "Director CI"

    orchestrator.run_bulk(dry_run=True, limit=None)

    assert _prebulk_backups(tracker_to_tmp) == []


# ── Hard-fail on missing Que ─────────────────────────────────────────────────


def test_missing_que_hard_fails(monkeypatch, tmp_path):
    """A missing Que dir hard-fails fast (never falls back to outputs/)."""
    missing = tmp_path / "no_such_que"
    monkeypatch.setattr(orchestrator.discovery, "que_dir", lambda: missing)
    with pytest.raises(FileNotFoundError):
        orchestrator.run_bulk(dry_run=True, limit=None)
