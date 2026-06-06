"""Tests for the 'in_que' pre-application status in pipeline/tracker/tracker.py.

Covers: add_entry(status="in_que") seeding, the re-add downgrade guard, the
silent follow-up behavior (Requirement B), and cleanup_stale exclusion.
"""

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
        "status_updated": "2026-06-01",
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


# ── New-entry seeding ─────────────────────────────────────────────────────────

def test_add_entry_with_in_que_status_seeds_everything(tracker_files):
    entry = tracker.add_entry("Acme", "Director", status="in_que")
    assert entry["status"] == "in_que"
    assert entry["status_updated"] == tracker._today()
    # The seed history event uses the passed status, not a hardcoded "prompted".
    assert entry["history"][0]["event"] == "in_que"


def test_add_entry_default_status_is_prompted(tracker_files):
    entry = tracker.add_entry("Acme", "Director")
    assert entry["status"] == "prompted"
    assert entry["history"][0]["event"] == "prompted"


# ── Downgrade guard on re-add ─────────────────────────────────────────────────

def test_re_add_as_in_que_does_not_demote_applied(tracker_files):
    tracker.save([_entry("Acme", "Director", status="applied")])
    tracker.add_entry("Acme", "Director", status="in_que")
    e = tracker.load()[0]
    assert e["status"] == "applied"  # never demoted by a bulk re-add
    # No spurious status-change history event was appended.
    assert not any(h.get("event") == "in_que" for h in e["history"])


def test_re_add_as_in_que_does_not_demote_prompted(tracker_files):
    tracker.save([_entry("Acme", "Director", status="prompted")])
    tracker.add_entry("Acme", "Director", status="in_que")
    assert tracker.load()[0]["status"] == "prompted"


def test_re_add_in_que_over_in_que_stays_in_que(tracker_files):
    tracker.save([_entry("Acme", "Director", status="in_que")])
    tracker.add_entry("Acme", "Director", status="in_que")
    assert tracker.load()[0]["status"] == "in_que"


def test_re_add_can_raise_status_when_higher(tracker_files):
    # Sanity check the guard is a downgrade guard, not a freeze.
    tracker.save([_entry("Acme", "Director", status="in_que")])
    tracker.add_entry("Acme", "Director", status="prompted")
    e = tracker.load()[0]
    assert e["status"] == "prompted"
    assert any(h.get("event") == "prompted" for h in e["history"])


# ── Silent in follow-ups (Requirement B) ──────────────────────────────────────

def test_compute_next_action_is_silent_for_in_que():
    action = tracker.compute_next_action(_entry("Acme", "Director", status="in_que"))
    assert action == {"text": "", "due_date": "", "urgency": "none"}


def test_in_que_never_gets_apply_now_nag():
    # Even an old in_que entry must stay silent (no "sitting 3+ days" urgency).
    action = tracker.compute_next_action(
        _entry("Acme", "Director", status="in_que", date_added="2020-01-01")
    )
    assert action["urgency"] == "none"
    assert action["text"] == ""


# ── cleanup_stale exclusion ───────────────────────────────────────────────────

def test_cleanup_stale_leaves_in_que_untouched(tracker_files):
    # An ancient in_que entry must NOT be auto-ghosted: it is Jack's backlog,
    # not an employer who went silent.
    tracker.save([_entry("Acme", "Director", status="in_que",
                         last_touchpoint="2020-01-01")])
    updated = tracker.cleanup_stale(days_threshold=30)
    assert updated == []
    assert tracker.load()[0]["status"] == "in_que"
