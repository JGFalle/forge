"""Two-way sync between the Excel-editable CSV and the canonical tracker JSON.

The JSON (application_tracker.json) is the source of truth. The CSV is a flat,
spreadsheet-friendly view of the scalar fields. Nested data (contacts, history)
lives only in the JSON and is preserved across imports.

Flow:
  - export_csv()  JSON  ->  CSV   (regenerate the spreadsheet from the truth)
  - import_csv()  CSV   ->  JSON  (fold spreadsheet edits back into the truth)
  - sync()        reconciles both: if the CSV was edited more recently than the
                  JSON, import first; then always re-export CSV + HTML so all
                  three files agree.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from pipeline.tracker.html_renderer import _STATUS_LABEL
from pipeline.tracker.html_renderer import generate as generate_html
from pipeline.tracker.tracker import (
    CLOSED_STATUSES,
    STATUS_ORDER,
    _norm,
    _resolve,
    _today,
    load,
    save,
)
from utils.logging import get_logger

logger = get_logger(__name__)

# (CSV column header, JSON key) — order defines the spreadsheet column order.
# "Date Updated" maps to status_updated: the date the status last changed. It is
# auto-maintained (not user-editable) and stays put once a row is closed/rejected.
_COLUMNS = [
    ("Company", "company"),
    ("Role", "role"),
    ("Status", "status"),
    ("Date Updated", "status_updated"),
    ("Fit Verdict", "fit_verdict"),
    ("Fit Score", "fit_score"),
    ("Salary Range", "salary_range"),
    ("Req Number", "req_number"),
    ("ATS", "ats_system"),
    ("Apply By", "apply_by_date"),
    ("Date Added", "date_added"),
    ("Last Touchpoint", "last_touchpoint"),
    ("Recruiter Led", "recruiter_initiated"),
    ("Notes", "notes"),
    ("App Folder", "app_folder"),
]

# Fields a user may edit in Excel and have flow back into the JSON.
_EDITABLE = {
    "status",
    "fit_verdict",
    "fit_score",
    "salary_range",
    "req_number",
    "ats_system",
    "apply_by_date",
    "notes",
    "recruiter_initiated",
}

_VALID_STATUSES = set(STATUS_ORDER) | CLOSED_STATUSES | {"prompted"}
_TRUE = {"yes", "y", "true", "1", "x", "✓"}

# Map any reasonable status the user might type in Excel (Title-Case labels like
# "Phone Screen", synonyms like "Interviewing") back to a canonical status.
_STATUS_ALIASES = {}
for _canon, _label in _STATUS_LABEL.items():
    _STATUS_ALIASES[_canon] = _canon
    _STATUS_ALIASES[_label.lower()] = _canon
_STATUS_ALIASES["interviewing"] = "interview"
_STATUS_ALIASES["lead"] = "prompted"


def _to_canonical_status(raw: str, current):
    """Normalize a free-typed status to canonical, or None if unrecognized."""
    raw = (raw or "").strip()
    if not raw:
        return current
    return _STATUS_ALIASES.get(raw.lower())


def _match(entries: list[dict], company: str, role: str, claimed: set[int]) -> dict | None:
    """Find the next unclaimed entry by company + role.

    Matching is tolerant of case and surrounding whitespace (some roles carry
    trailing spaces from JD parsing). The tracker keys on company + role but the
    data can contain duplicate pairs (the same role applied to twice). Claiming
    each matched entry maps the Nth duplicate CSV row to the Nth duplicate JSON
    entry, so a re-export/re-import round-trips cleanly instead of collapsing
    duplicates onto one record.
    """
    key = (_norm(company), _norm(role))
    for e in entries:
        if id(e) in claimed:
            continue
        if (_norm(e.get("company")), _norm(e.get("role"))) == key:
            return e
    return None


def _csv_path() -> Path:
    return _resolve("paths.tracker_csv", "outputs/application_tracker.csv")


def _json_path() -> Path:
    return _resolve("paths.tracker_file", "outputs/application_tracker.json")


def _fmt(key: str, value) -> str:
    if key == "recruiter_initiated":
        return "yes" if value else "no"
    if key == "status":
        return _STATUS_LABEL.get(value, str(value or "").replace("_", " ").title())
    if value is None:
        return ""
    return str(value)


def export_csv(path: Path | None = None) -> Path:
    """Write every tracker entry to a flat CSV (overwrites)."""
    entries = load()
    out = path or _csv_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([col for col, _ in _COLUMNS])
        for e in entries:
            writer.writerow([_fmt(key, e.get(key, "")) for _, key in _COLUMNS])
    logger.info("Tracker CSV exported: %s (%d rows)", out, len(entries))
    return out


def _coerce(key: str, raw: str, current):
    """Turn a CSV cell into the JSON-typed value, falling back to current."""
    if key == "status":
        return _to_canonical_status(raw, current)
    raw = (raw or "").strip()
    if key == "recruiter_initiated":
        return raw.lower() in _TRUE
    if key == "fit_score":
        try:
            return int(float(raw)) if raw else 0
        except ValueError:
            return current
    return raw


def _new_entry(values: dict, today: str) -> dict:
    """Build a tracker entry for a row typed directly into the spreadsheet."""
    status = values.get("status") if values.get("status") in _VALID_STATUSES else "applied"
    return {
        "date_added": values.get("date_added") or today,
        "company": values["company"],
        "role": values["role"],
        "req_number": values.get("req_number", ""),
        "salary_range": values.get("salary_range", ""),
        "ats_system": values.get("ats_system", ""),
        "status": status,
        "status_updated": today,
        "fit_verdict": values.get("fit_verdict", ""),
        "fit_score": values.get("fit_score", 0) or 0,
        "apply_by_date": values.get("apply_by_date", ""),
        "app_folder": values.get("app_folder", ""),
        "notes": values.get("notes", ""),
        "override_reason": "",
        "recruiter_initiated": bool(values.get("recruiter_initiated", False)),
        "contacts": [],
        "last_touchpoint": today,
        "history": [{"date": today, "event": status, "note": "Added via CSV"}],
    }


def import_csv(path: Path | None = None) -> dict:
    """Fold spreadsheet edits back into the JSON. Returns a summary dict.

    Matching is by company + role (case-insensitive), the same key the rest of
    the tracker uses. Rows missing from the CSV are left untouched in the JSON
    (Excel users delete rows freely; silent deletion would lose data).
    """
    src = path or _csv_path()
    summary = {"changed": 0, "added": 0, "warnings": []}
    if not src.exists():
        logger.warning("No tracker CSV to import: %s", src)
        return summary

    entries = load()
    today = _today()
    claimed: set[int] = set()
    dirty = False

    with open(src, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for line_no, row in enumerate(reader, start=2):  # row 1 is the header
            company = (row.get("Company") or "").strip()
            role = (row.get("Role") or "").strip()
            if not company or not role:
                continue

            values = {"company": company, "role": role}
            entry = _match(entries, company, role, claimed)
            if entry is not None:
                claimed.add(id(entry))
            for col, key in _COLUMNS:
                if key in ("company", "role") or key not in _EDITABLE:
                    if entry is None and key not in ("company", "role"):
                        values[key] = _coerce(key, row.get(col, ""), None)
                    continue
                values[key] = _coerce(key, row.get(col, ""), entry.get(key) if entry else None)
                if key == "status" and (row.get(col) or "").strip() and values[key] is None:
                    summary["warnings"].append(
                        f"Row {line_no} ({company} / {role}): unknown status "
                        f"'{row.get(col).strip()}' ignored"
                    )

            if entry is None:
                entries.append(_new_entry(values, today))
                summary["added"] += 1
                dirty = True
                logger.info("Tracker entry added via CSV: %s / %s", company, role)
                continue

            row_changed = False
            for key in _EDITABLE:
                new_val = values.get(key)
                if new_val is None:
                    continue
                cur = entry.get(key)
                if new_val == cur:
                    continue
                if isinstance(cur, str) and new_val == cur.strip():
                    continue  # whitespace-only difference, not a real edit
                entry[key] = new_val
                row_changed = True
                if key == "status":
                    entry["status_updated"] = today
                    entry["last_touchpoint"] = today
                    entry.setdefault("history", []).append(
                        {"date": today, "event": new_val, "note": f"Status set via CSV (was {cur})"}
                    )

            if row_changed:
                summary["changed"] += 1
                dirty = True

    if dirty:
        save(entries)
    return summary


def sync() -> dict:
    """Reconcile CSV and JSON, then regenerate CSV + HTML so all three agree.

    If the CSV is newer than the JSON, the user edited it in Excel: import those
    edits first. Then always re-export the CSV and HTML from the (now current)
    JSON, and stamp the JSON as newest so the next sync doesn't re-import a file
    it just wrote.
    """
    json_path = _json_path()
    csv_path = _csv_path()
    result = {"direction": "export", "changed": 0, "added": 0, "warnings": []}

    csv_newer = (
        csv_path.exists()
        and json_path.exists()
        and csv_path.stat().st_mtime > json_path.stat().st_mtime + 1
    )
    if csv_newer:
        imported = import_csv(csv_path)
        result.update(imported)
        result["direction"] = "import+export"
        if imported["changed"] or imported["added"]:
            logger.info(
                "CSV newer than JSON: imported %d change(s), %d new entry(ies)",
                imported["changed"],
                imported["added"],
            )

    result["csv"] = export_csv(csv_path)
    result["html"] = generate_html()
    # Make the JSON the most-recently-touched file so a no-op sync after this
    # exports rather than re-importing the CSV we just wrote.
    if json_path.exists():
        os.utime(json_path, None)
    return result
