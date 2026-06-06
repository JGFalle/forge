"""Tests for pipeline/tracker/csv_sync.py — the CSV <-> JSON <-> HTML bridge."""

import csv
import os

import pytest

from pipeline.tracker import csv_sync, html_renderer, tracker


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
        # FORGE's html_renderer writes to <outputs_dir>/pipeline_view.html.
        "paths.outputs_dir": str(tmp_path),
    }
    _fake_get = lambda key, default=None: mapping.get(key, default)  # noqa: E731
    # tracker.get drives _resolve (tracker + csv_sync paths); html_renderer.get
    # resolves its own output path independently.
    monkeypatch.setattr(tracker, "get", _fake_get)
    monkeypatch.setattr(html_renderer, "get", _fake_get)
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


def _read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ── Export ────────────────────────────────────────────────────────────────────

def test_export_writes_header_and_rows(tracker_files):
    tracker.save([_entry("Acme", "Director of Ops"), _entry("Globex", "VP Supply Chain")])
    out = csv_sync.export_csv()
    rows = _read_csv(out)
    assert len(rows) == 2
    assert rows[0]["Company"] == "Acme"
    assert rows[0]["Status"] == "Prompted"  # exported as a Title-Case label


def test_export_renders_recruiter_bool_as_yes_no(tracker_files):
    tracker.save([_entry("Acme", "Director", recruiter_initiated=True)])
    rows = _read_csv(csv_sync.export_csv())
    assert rows[0]["Recruiter Led"] == "yes"


def test_export_has_date_updated_column_after_status(tracker_files):
    tracker.save([_entry("Acme", "Director", status="applied",
                         status_updated="2026-05-20")])
    rows = _read_csv(csv_sync.export_csv())
    cols = list(rows[0].keys())
    assert cols[2:4] == ["Status", "Date Updated"]
    assert rows[0]["Date Updated"] == "2026-05-20"


# ── Import ──────────────────────────────────────────────────────────────────--

def test_import_status_change_updates_json_and_logs_history(tracker_files):
    tracker.save([_entry("Acme", "Director", status="applied",
                         contacts=[{"name": "Pat", "type": "recruiter"}])])
    csv_sync.export_csv()

    # Simulate an Excel edit: bump status to phone_screen.
    rows = _read_csv(tracker_files["csv"])
    rows[0]["Status"] = "phone_screen"
    with open(tracker_files["csv"], "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    summary = csv_sync.import_csv()
    assert summary["changed"] == 1

    entry = tracker.load()[0]
    assert entry["status"] == "phone_screen"
    assert entry["last_touchpoint"]  # stamped on status change
    assert any(h["event"] == "phone_screen" for h in entry["history"])
    # Nested data the CSV does not carry must survive the round-trip.
    assert entry["contacts"] == [{"name": "Pat", "type": "recruiter"}]


def test_import_adds_new_row_typed_in_excel(tracker_files):
    tracker.save([_entry("Acme", "Director")])
    csv_sync.export_csv()
    with open(tracker_files["csv"], "a", encoding="utf-8-sig", newline="") as f:
        # Company, Role, Status, ... (only first columns matter here)
        f.write("Initech,Ops Manager,applied,,,,,,,,,,,\n")

    summary = csv_sync.import_csv()
    assert summary["added"] == 1
    entries = tracker.load()
    initech = next(e for e in entries if e["company"] == "Initech")
    assert initech["status"] == "applied"
    assert initech["history"][0]["note"] == "Added via CSV"


def test_import_rejects_unknown_status(tracker_files):
    tracker.save([_entry("Acme", "Director", status="applied")])
    csv_sync.export_csv()
    rows = _read_csv(tracker_files["csv"])
    rows[0]["Status"] = "totally_made_up"
    with open(tracker_files["csv"], "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    summary = csv_sync.import_csv()
    assert summary["changed"] == 0
    assert summary["warnings"]
    assert tracker.load()[0]["status"] == "applied"  # unchanged


def test_import_missing_row_is_not_deleted(tracker_files):
    tracker.save([_entry("Acme", "Director"), _entry("Globex", "VP")])
    csv_sync.export_csv()
    # Drop Globex from the CSV (user deleted the spreadsheet row).
    rows = [r for r in _read_csv(tracker_files["csv"]) if r["Company"] == "Acme"]
    with open(tracker_files["csv"], "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    csv_sync.import_csv()
    companies = {e["company"] for e in tracker.load()}
    assert companies == {"Acme", "Globex"}  # nothing lost


def test_import_accepts_titlecase_and_synonym_statuses(tracker_files):
    tracker.save([
        _entry("Acme", "Director", status="applied"),
        _entry("Globex", "VP", status="applied"),
    ])
    csv_sync.export_csv()
    rows = _read_csv(csv_sync._csv_path())
    # User types human-friendly statuses in Excel.
    rows[0]["Status"] = "Rejected"      # Title-case label
    rows[1]["Status"] = "Interviewing"  # synonym for interview
    with open(csv_sync._csv_path(), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    summary = csv_sync.import_csv()
    assert summary["changed"] == 2
    assert not summary["warnings"]
    by = {e["company"]: e for e in tracker.load()}
    assert by["Acme"]["status"] == "rejected"
    assert by["Globex"]["status"] == "interview"


def test_import_stamps_date_updated_on_status_change(tracker_files):
    tracker.save([_entry("Acme", "Director", status="applied",
                         status_updated="2026-05-01")])
    csv_sync.export_csv()
    rows = _read_csv(csv_sync._csv_path())
    rows[0]["Status"] = "Rejected"
    with open(csv_sync._csv_path(), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    csv_sync.import_csv()
    e = tracker.load()[0]
    assert e["status"] == "rejected"
    assert e["status_updated"] == tracker._today()  # stamped to today on change


def test_import_warns_on_unrecognized_status(tracker_files):
    tracker.save([_entry("Acme", "Director", status="applied")])
    csv_sync.export_csv()
    rows = _read_csv(csv_sync._csv_path())
    rows[0]["Status"] = "Ghosting Me"
    with open(csv_sync._csv_path(), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    summary = csv_sync.import_csv()
    assert summary["changed"] == 0
    assert summary["warnings"]
    assert tracker.load()[0]["status"] == "applied"  # unchanged


def test_import_is_idempotent_with_trailing_space_role(tracker_files):
    # Role carries a trailing space (seen in real JD-parsed data).
    tracker.save([_entry("Acme", "Director of Ops ")])
    csv_sync.export_csv()
    s1 = csv_sync.import_csv()
    s2 = csv_sync.import_csv()
    assert (s1["changed"], s1["added"]) == (0, 0)
    assert (s2["changed"], s2["added"]) == (0, 0)
    assert len(tracker.load()) == 1  # not duplicated


def test_duplicate_company_role_round_trips_distinctly(tracker_files):
    # Two entries share company + role but differ in other fields.
    tracker.save([
        _entry("Coca-Cola", "Director RGM", status="applied", salary_range="$180k"),
        _entry("Coca-Cola", "Director RGM", status="prompted", salary_range="$25"),
    ])
    csv_sync.export_csv()
    summary = csv_sync.import_csv()
    assert (summary["changed"], summary["added"]) == (0, 0)
    entries = tracker.load()
    assert len(entries) == 2
    # Each kept its own distinct values — neither collapsed onto the other.
    pairs = sorted((e["status"], e["salary_range"]) for e in entries)
    assert pairs == [("applied", "$180k"), ("prompted", "$25")]


# ── In Que round-trip ─────────────────────────────────────────────────────────

def test_in_que_exports_as_label_and_reimports(tracker_files):
    tracker.save([_entry("Acme", "Director", status="in_que")])
    rows = _read_csv(csv_sync.export_csv())
    assert rows[0]["Status"] == "In Que"  # exported as the human label

    # Re-import the exact file we just wrote: status stays in_que (no drift).
    summary = csv_sync.import_csv()
    assert (summary["changed"], summary["added"]) == (0, 0)
    assert tracker.load()[0]["status"] == "in_que"


def test_in_que_typed_in_excel_coerces_back(tracker_files):
    tracker.save([_entry("Acme", "Director", status="applied")])
    csv_sync.export_csv()
    rows = _read_csv(csv_sync._csv_path())
    rows[0]["Status"] = "In Que"  # user types the human label in Excel
    with open(csv_sync._csv_path(), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    summary = csv_sync.import_csv()
    assert summary["changed"] == 1
    assert not summary["warnings"]
    assert tracker.load()[0]["status"] == "in_que"


def test_in_que_is_a_valid_status(tracker_files):
    assert "in_que" in csv_sync._VALID_STATUSES


# ── Sync orchestration ──────────────────────────────────────────────────────--

def test_sync_imports_when_csv_is_newer(tracker_files):
    tracker.save([_entry("Acme", "Director", status="applied")])
    csv_sync.export_csv()
    rows = _read_csv(tracker_files["csv"])
    rows[0]["Status"] = "interview"
    with open(tracker_files["csv"], "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    # Force the CSV to look newer than the JSON.
    future = os.path.getmtime(tracker_files["json"]) + 100
    os.utime(tracker_files["csv"], (future, future))

    result = csv_sync.sync()
    assert result["direction"] == "import+export"
    assert result["changed"] == 1
    assert tracker.load()[0]["status"] == "interview"
    assert tracker_files["html"].exists()


def test_sync_export_only_when_json_is_newer(tracker_files):
    tracker.save([_entry("Acme", "Director")])
    result = csv_sync.sync()
    assert result["direction"] == "export"
    assert tracker_files["csv"].exists()
    assert tracker_files["html"].exists()
