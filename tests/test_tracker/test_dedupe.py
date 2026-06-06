"""Tests for tracker.dedupe — duplicate detection, deletion, and merging."""

import pytest

from pipeline.tracker import tracker


@pytest.fixture
def tracker_files(tmp_path, monkeypatch):
    """Point all tracker paths at a temp dir so real Drive data is untouched."""
    json_path = tmp_path / "application_tracker.json"
    csv_path = tmp_path / "application_tracker.csv"
    html_path = tmp_path / "pipeline_view.html"
    mapping = {
        "paths.tracker_file": str(json_path),
        "paths.tracker_csv": str(csv_path),
        "paths.tracker_html": str(html_path),
    }
    monkeypatch.setattr(tracker, "get", lambda key, default=None: mapping.get(key, default))
    return {"json": json_path, "csv": csv_path, "html": html_path}


def _entry(company, role, status="prompted", **extra):
    e = {
        "date_added": "2026-06-01",
        "company": company,
        "role": role,
        "req_number": "",
        "salary_range": "",
        "ats_system": "",
        "status": status,
        "fit_verdict": "STRONG_FIT",
        "fit_score": 8,
        "apply_by_date": "",
        "app_folder": "",
        "notes": "",
        "override_reason": "",
        "recruiter_initiated": False,
        "contacts": [],
        "last_touchpoint": "",
        "history": [{"date": "2026-06-01", "event": status, "note": "seed"}],
    }
    e.update(extra)
    return e


# ── No duplicates ───────────────────────────────────────────────────────────--

def test_no_duplicates_is_noop(tracker_files):
    tracker.save([_entry("Acme", "Director"), _entry("Globex", "VP")])
    summary = tracker.dedupe(apply=False)
    assert summary["groups"] == []
    assert summary["deleted"] == 0
    assert summary["total_before"] == summary["total_after"] == 2


# ── Preview never writes ──────────────────────────────────────────────────────

def test_preview_does_not_write(tracker_files):
    tracker.save([_entry("Acme", "Director"), _entry("Acme", "Director")])
    summary = tracker.dedupe(apply=False)
    assert summary["applied"] is False
    assert summary["backup"] == ""
    assert len(tracker.load()) == 2  # unchanged on disk


# ── Identical entries collapse (delete) ───────────────────────────────────────

def test_identical_entries_are_deleted(tracker_files):
    tracker.save([_entry("Coca-Cola", "Director RGM"), _entry("Coca-Cola", "Director RGM")])
    summary = tracker.dedupe(apply=True)
    assert summary["deleted"] == 1
    assert summary["deleted_groups"] == 1
    assert summary["merged_groups"] == 0
    assert len(tracker.load()) == 1
    assert summary["groups"][0]["action"] == "delete"


def test_delete_unions_nested_data_from_copies(tracker_files):
    a = _entry("Acme", "Director", contacts=[{"name": "Pat", "type": "recruiter"}])
    b = _entry("Acme", "Director", contacts=[{"name": "Sam", "type": "hiring_manager"}],
               history=[{"date": "2026-06-02", "event": "note", "note": "x"}])
    tracker.save([a, b])
    tracker.dedupe(apply=True)
    keeper = tracker.load()[0]
    names = sorted(c["name"] for c in keeper["contacts"])
    assert names == ["Pat", "Sam"]  # nested data preserved despite "delete"


# ── Case / whitespace tolerance ───────────────────────────────────────────────

def test_matching_tolerates_case_and_whitespace(tracker_files):
    tracker.save([_entry("Acme", "Director of Ops"), _entry("acme ", "Director of Ops ")])
    summary = tracker.dedupe(apply=True)
    assert summary["deleted"] == 1
    assert len(tracker.load()) == 1


# ── Divergent entries merge ───────────────────────────────────────────────────

def test_divergent_entries_merge_keeps_most_advanced_status(tracker_files):
    tracker.save([
        _entry("Coca-Cola", "Director RGM", status="applied"),
        _entry("Coca-Cola", "Director RGM", status="interview"),
    ])
    summary = tracker.dedupe(apply=True)
    assert summary["merged_groups"] == 1
    assert summary["deleted_groups"] == 0
    survivor = tracker.load()[0]
    assert survivor["status"] == "interview"


def test_status_rank_orders_in_que_below_prompted_and_applied():
    assert tracker._status_rank("in_que") < tracker._status_rank("prompted")
    assert tracker._status_rank("prompted") < tracker._status_rank("applied")


def test_merge_applied_beats_in_que(tracker_files):
    tracker.save([
        _entry("Acme", "Director", status="in_que"),
        _entry("Acme", "Director", status="applied"),
    ])
    tracker.dedupe(apply=True)
    assert tracker.load()[0]["status"] == "applied"


def test_merge_active_status_beats_closed(tracker_files):
    tracker.save([
        _entry("Acme", "Director", status="rejected"),
        _entry("Acme", "Director", status="phone_screen"),
    ])
    tracker.dedupe(apply=True)
    assert tracker.load()[0]["status"] == "phone_screen"


def test_merge_accepted_is_terminal_high(tracker_files):
    tracker.save([
        _entry("Acme", "Director", status="interview"),
        _entry("Acme", "Director", status="accepted"),
    ])
    tracker.dedupe(apply=True)
    assert tracker.load()[0]["status"] == "accepted"


def test_merge_fills_blanks_and_reports_conflicts(tracker_files):
    tracker.save([
        _entry("Acme", "Director", req_number="", salary_range="$180k", notes="from a"),
        _entry("Acme", "Director", req_number="REQ-7", salary_range="$190k", notes=""),
    ])
    summary = tracker.dedupe(apply=True)
    survivor = tracker.load()[0]
    # Blank filled from the other entry.
    assert survivor["req_number"] == "REQ-7"
    # Conflicting non-empty salary: survivor's kept, loser reported.
    conflicts = " ".join(summary["groups"][0]["conflicts"])
    assert "salary_range" in conflicts
    assert "$190k" in conflicts or "$180k" in conflicts


def test_merge_recruiter_initiated_is_or(tracker_files):
    tracker.save([
        _entry("Acme", "Director", status="applied", recruiter_initiated=False),
        _entry("Acme", "Director", status="phone_screen", recruiter_initiated=True),
    ])
    tracker.dedupe(apply=True)
    assert tracker.load()[0]["recruiter_initiated"] is True


def test_merge_dates(tracker_files):
    tracker.save([
        _entry("Acme", "Director", status="applied", date_added="2026-06-05",
               last_touchpoint="2026-06-10", apply_by_date="2026-07-01"),
        _entry("Acme", "Director", status="interview", date_added="2026-06-01",
               last_touchpoint="2026-06-20", apply_by_date="2026-06-15"),
    ])
    tracker.dedupe(apply=True)
    s = tracker.load()[0]
    assert s["date_added"] == "2026-06-01"      # earliest
    assert s["last_touchpoint"] == "2026-06-20"  # latest
    assert s["apply_by_date"] == "2026-06-15"    # earliest non-empty


def test_merge_history_concat_dedupe_sorted(tracker_files):
    a = _entry("Acme", "Director", status="applied",
               history=[{"date": "2026-06-03", "event": "applied", "note": "x"},
                        {"date": "2026-06-01", "event": "prompted", "note": "seed"}])
    b = _entry("Acme", "Director", status="interview",
               history=[{"date": "2026-06-01", "event": "prompted", "note": "seed"},
                        {"date": "2026-06-05", "event": "interview", "note": "y"}])
    tracker.save([a, b])
    tracker.dedupe(apply=True)
    hist = tracker.load()[0]["history"]
    dates = [h["date"] for h in hist]
    assert dates == sorted(dates)
    # Exact-duplicate "prompted" event appears only once.
    assert sum(1 for h in hist if h["event"] == "prompted") == 1


def test_merge_unions_contacts_dedupe_by_name(tracker_files):
    a = _entry("Acme", "Director", status="applied",
               contacts=[{"name": "Pat", "type": "recruiter"}])
    b = _entry("Acme", "Director", status="interview",
               contacts=[{"name": "pat", "type": "recruiter"},  # same name, diff case
                         {"name": "Sam", "type": "hiring_manager"}])
    tracker.save([a, b])
    tracker.dedupe(apply=True)
    names = sorted(c["name"].lower() for c in tracker.load()[0]["contacts"])
    assert names == ["pat", "sam"]


# ── Backup ────────────────────────────────────────────────────────────────────

def test_apply_writes_backup(tracker_files):
    tracker.save([_entry("Acme", "Director"), _entry("Acme", "Director")])
    summary = tracker.dedupe(apply=True)
    assert summary["backup"]
    from pathlib import Path
    backup = Path(summary["backup"])
    assert backup.exists()
    assert "backup_" in backup.name


def test_no_backup_when_nothing_to_do(tracker_files):
    tracker.save([_entry("Acme", "Director")])
    summary = tracker.dedupe(apply=True)
    assert summary["backup"] == ""
