"""Application tracker, reads and writes application_tracker.json.

Status progression:
  in_que → prompted → applied → phone_screen → interview → final_round → offer → accepted
                                                                                → declined
  (any stage) → rejected | ghosted | withdrawn

Contact types: warm_referral | hiring_manager | recruiter | intel_contact
Channels:      linkedin | email | phone | in_person
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from utils.config import get
from utils.logging import get_logger

logger = get_logger(__name__)

CLOSED_STATUSES = {"rejected", "declined", "ghosted", "withdrawn", "accepted"}
STATUS_ORDER = [
    "in_que", "prompted", "applied", "phone_screen", "interview",
    "final_round", "offer", "accepted",
]


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve(config_key: str, default: str) -> Path:
    """Resolve a configured file path.

    Expands ``~`` and anchors any relative path to the repo root, so the
    location is stable regardless of the current working directory.
    """
    path = Path(get(config_key, default)).expanduser()
    if not path.is_absolute():
        path = _REPO_ROOT / path
    return path


def _tracker_path() -> Path:
    return _resolve("paths.tracker_file", "outputs/application_tracker.json")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _norm(s) -> str:
    """Normalize a key field: tolerant of case and surrounding whitespace.

    Some roles carry trailing spaces from JD parsing, so the same logical
    (company, role) pair can render as distinct strings. Shared by csv_sync.
    """
    return (s or "").strip().lower()


def status_updated_for(entry: dict) -> str:
    """Date the status last changed.

    The most recent history event whose name is a status, falling back to
    date_added. Used to seed the status_updated field on older entries.
    """
    statuses = set(STATUS_ORDER) | CLOSED_STATUSES | {"prompted"}
    for h in reversed(entry.get("history", [])):
        if h.get("event") in statuses and h.get("date"):
            return h["date"]
    return entry.get("date_added", "")


def load() -> list[dict]:
    path = _tracker_path()
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(entries: list[dict]) -> None:
    path = _tracker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def add_entry(
    company: str,
    role: str,
    req_number: str = "",
    salary_range: str = "",
    ats_system: str = "",
    app_folder: str = "",
    fit_verdict: str = "",
    fit_score: int = 0,
    apply_by_date: str = "",
    notes: str = "",
    override_reason: str = "",
    status: str = "prompted",
) -> dict:
    entries = load()
    today = _today()
    status = status or "prompted"

    existing = _find_entry(entries, company, role)
    if existing:
        # Update in place — don't create a duplicate, don't reset status.
        # Downgrade guard: a re-add (e.g. a bulk pass tagging this as in_que)
        # must never lower the status of an entry that is already further along.
        # Only raise the status when the requested one ranks strictly higher.
        if _status_rank(status) > _status_rank(existing.get("status", "prompted")):
            existing["status"] = status
            existing["status_updated"] = today
            existing["history"].append(
                {"date": today, "event": status, "note": f"Status set to {status} on re-add"}
            )
        if app_folder:
            existing["app_folder"] = app_folder
        if fit_verdict:
            existing["fit_verdict"] = fit_verdict
        if fit_score:
            existing["fit_score"] = fit_score
        if apply_by_date:
            existing["apply_by_date"] = apply_by_date
        existing["history"].append({"date": today, "event": "pipeline_rerun", "note": "Pipeline rerun — materials regenerated"})
        save(entries)
        logger.info("Tracker entry updated (rerun): %s / %s", company, role)
        return existing

    entry = {
        "date_added": today,
        "company": company,
        "role": role,
        "req_number": req_number or "",
        "salary_range": salary_range or "",
        "ats_system": ats_system or "",
        "status": status,
        "status_updated": today,
        "fit_verdict": fit_verdict or "",
        "fit_score": fit_score,
        "apply_by_date": apply_by_date or "",
        "app_folder": app_folder or "",
        "notes": notes or "",
        "override_reason": override_reason or "",
        "recruiter_initiated": False,
        "contacts": [],
        "last_touchpoint": "",
        "history": [{"date": today, "event": status, "note": "Pipeline run — materials generated"}],
    }
    entries.append(entry)
    save(entries)
    logger.info("Tracker entry added: %s / %s  [%s %s/10]", company, role, fit_verdict, fit_score)
    return entry


def _find_entry(entries: list[dict], company: str, role: str) -> dict | None:
    cl, rl = company.lower(), role.lower()
    for e in entries:
        if e["company"].lower() == cl and e["role"].lower() == rl:
            return e
    return None


def update_status(company: str, role: str, status: str, note: str = "") -> bool:
    entries = load()
    entry = _find_entry(entries, company, role)
    if not entry:
        logger.warning("No tracker entry found for %s / %s", company, role)
        return False
    entry["status"] = status
    entry["status_updated"] = _today()
    entry["last_touchpoint"] = _today()
    entry["history"].append({"date": _today(), "event": status, "note": note})
    save(entries)
    logger.info("Status updated: %s / %s -> %s", company, role, status)
    return True


def add_contact(
    company: str,
    role: str,
    name: str,
    title: str = "",
    contact_type: str = "intel_contact",
    channel: str = "linkedin",
    notes: str = "",
) -> bool:
    """Log a new contact for an application. Updates last_touchpoint."""
    entries = load()
    entry = _find_entry(entries, company, role)
    if not entry:
        logger.warning("No tracker entry found for %s / %s", company, role)
        return False
    today = _today()
    existing = next((c for c in entry.get("contacts", []) if c["name"].lower() == name.lower()), None)
    if existing:
        existing["last_contact"] = today
        existing["channel"] = channel
        if notes:
            existing["notes"] = notes
    else:
        entry.setdefault("contacts", []).append({
            "name": name,
            "title": title,
            "type": contact_type,
            "channel": channel,
            "first_contact": today,
            "last_contact": today,
            "notes": notes,
        })
    entry["last_touchpoint"] = today
    entry["history"].append({
        "date": today,
        "event": "contact",
        "note": f"{name} ({contact_type}) via {channel}" + (f" — {notes}" if notes else ""),
    })
    if contact_type == "recruiter":
        entry["recruiter_initiated"] = True
    save(entries)
    logger.info("Contact logged: %s for %s / %s", name, company, role)
    return True


def log_touchpoint(company: str, role: str, note: str = "") -> bool:
    """Record a general touchpoint (call, email, etc.) without adding a new contact."""
    entries = load()
    entry = _find_entry(entries, company, role)
    if not entry:
        return False
    today = _today()
    entry["last_touchpoint"] = today
    entry["history"].append({"date": today, "event": "touchpoint", "note": note})
    save(entries)
    return True


def compute_next_action(entry: dict) -> dict:
    """
    Return the next recommended action for an application.
    Result keys: text, due_date (str), urgency (overdue|urgent|upcoming|ok|none)
    """
    today = date.today()
    status = entry.get("status", "prompted")

    if status in CLOSED_STATUSES:
        return {"text": "", "due_date": "", "urgency": "none"}

    # In Que is a pre-application backlog state: silent in follow-ups, no nags.
    if status == "in_que":
        return {"text": "", "due_date": "", "urgency": "none"}

    cfg = {
        "applied_cold_days":    int(get("followup.applied_cold_days", 7)),
        "applied_warm_days":    int(get("followup.applied_warm_days", 5)),
        "recruiter_quiet_days": int(get("followup.recruiter_quiet_days", 3)),
        "phone_screen_days":    int(get("followup.phone_screen_days", 5)),
        "interview_days":       int(get("followup.interview_days", 7)),
        "final_round_days":     int(get("followup.final_round_days", 5)),
        "max_silence_days":     int(get("followup.max_silence_days", 14)),
    }

    recruiter = entry.get("recruiter_initiated", False)
    last_tp = entry.get("last_touchpoint") or entry.get("date_added", str(today))
    try:
        last_date = date.fromisoformat(last_tp)
    except ValueError:
        last_date = today

    days_silent = (today - last_date).days

    # Max silence override
    if days_silent >= cfg["max_silence_days"] and status not in ("prompted",):
        due = last_date + timedelta(days=cfg["max_silence_days"])
        return {
            "text": f"No contact in {days_silent} days — escalate or close",
            "due_date": str(due),
            "urgency": _urgency(due, today),
        }

    if status == "prompted":
        date_added = date.fromisoformat(entry.get("date_added", str(today)))
        age = (today - date_added).days
        if age >= 3:
            return {"text": "Apply now — materials sitting 3+ days", "due_date": str(date_added + timedelta(days=3)), "urgency": "urgent"}
        return {"text": "Complete prompts and apply", "due_date": "", "urgency": "ok"}

    if status == "applied":
        window = cfg["recruiter_quiet_days"] if recruiter else (cfg["applied_warm_days"] if _has_warm_referral(entry) else cfg["applied_cold_days"])
        due = last_date + timedelta(days=window)
        if today < due:
            return {"text": f"Follow up in {(due - today).days}d if no response", "due_date": str(due), "urgency": _urgency(due, today)}
        return {"text": "Follow up now — no response", "due_date": str(due), "urgency": "overdue"}

    if status == "phone_screen":
        due = last_date + timedelta(days=cfg["phone_screen_days"])
        if today <= due:
            return {"text": f"Follow up by {due.strftime('%b %d')} if no next steps", "due_date": str(due), "urgency": _urgency(due, today)}
        return {"text": "Follow up now — no next steps after screen", "due_date": str(due), "urgency": "overdue"}

    if status == "interview":
        due = last_date + timedelta(days=cfg["interview_days"])
        return {"text": f"Follow up by {due.strftime('%b %d')} if no decision", "due_date": str(due), "urgency": _urgency(due, today)}

    if status == "final_round":
        due = last_date + timedelta(days=cfg["final_round_days"])
        return {"text": f"Follow up by {due.strftime('%b %d')} — decision pending", "due_date": str(due), "urgency": _urgency(due, today)}

    if status == "offer":
        return {"text": "Respond to offer or negotiate", "due_date": "", "urgency": "urgent"}

    return {"text": "", "due_date": "", "urgency": "ok"}


def _has_warm_referral(entry: dict) -> bool:
    return any(c.get("type") == "warm_referral" for c in entry.get("contacts", []))


def _urgency(due: date, today: date) -> str:
    delta = (due - today).days
    if delta < 0:
        return "overdue"
    if delta <= 2:
        return "urgent"
    if delta <= 5:
        return "upcoming"
    return "ok"


def list_active() -> list[dict]:
    return [e for e in load() if e.get("status", "") not in CLOSED_STATUSES]


def list_all() -> list[dict]:
    return load()


def cleanup_stale(days_threshold: int = 30, dry_run: bool = False) -> list[dict]:
    """
    Mark applications as 'ghosted' when no touchpoint in days_threshold days.

    Only targets entries with status in (applied, phone_screen, interview, final_round)
    that have gone silent. 'prompted' and 'in_que' entries are skipped — they just
    haven't been submitted yet (in_que is pre-application backlog, not employer
    silence). Returns list of entries that were (or would be) updated.
    """
    staleable = {"applied", "phone_screen", "interview", "final_round"}
    entries = load()
    today = date.today()
    updated = []

    for entry in entries:
        status = entry.get("status", "")
        if status not in staleable:
            continue
        last_tp = entry.get("last_touchpoint") or entry.get("date_added", str(today))
        try:
            last_date = date.fromisoformat(last_tp)
        except ValueError:
            continue
        days_silent = (today - last_date).days
        if days_silent >= days_threshold:
            updated.append({
                "company": entry["company"],
                "role": entry["role"],
                "status": status,
                "days_silent": days_silent,
            })
            if not dry_run:
                entry["status"] = "ghosted"
                entry["history"].append({
                    "date": str(today),
                    "event": "ghosted",
                    "note": f"Auto-marked ghosted after {days_silent} days of silence",
                })

    if updated and not dry_run:
        save(entries)
        logger.info("Stale cleanup: marked %d application(s) as ghosted", len(updated))
    elif updated and dry_run:
        logger.info("Stale cleanup (dry run): %d application(s) would be ghosted", len(updated))

    return updated


# ── Dedupe ────────────────────────────────────────────────────────────────────
#
# The tracker keys on (company, role) and the rest of the system assumes that
# pair is unique, but real data violates it (e.g. two identical entries for the
# same role). dedupe() groups by the normalized pair and either deletes exact
# duplicates or merges divergent ones into a single survivor without losing data.

# Scalar fields whose values must match (ignoring whitespace) for a group to be
# treated as a pure-delete rather than a merge. Nested/timestamp fields
# (contacts, history, last_touchpoint) deliberately do NOT block a delete: the
# richest of them is kept regardless.
_SUBSTANTIVE_FIELDS = (
    "status",
    "req_number",
    "salary_range",
    "ats_system",
    "fit_verdict",
    "fit_score",
    "apply_by_date",
    "app_folder",
    "notes",
    "override_reason",
    "recruiter_initiated",
)

# Scalar fields where, on a merge, a blank is filled from another entry and a
# real conflict is reported (survivor's value wins, the loser is not silenced).
_PREFER_NONEMPTY_FIELDS = (
    "req_number",
    "salary_range",
    "ats_system",
    "fit_verdict",
    "fit_score",
    "app_folder",
    "notes",
    "override_reason",
)


def _status_rank(status: str) -> int:
    """Order statuses so the most-advanced one wins a merge.

    In-progress statuses follow STATUS_ORDER. ``accepted`` is the terminal high.
    Other closed statuses (rejected, declined, ghosted, withdrawn) rank below all
    in-progress work: a live application carries more signal than a stale close,
    so an active entry survives over a closed one. Among closed-but-not-accepted
    statuses they tie at the bottom and the first-seen survivor is kept.
    """
    status = (status or "").strip().lower()
    if status == "accepted":
        return 1000
    if status in STATUS_ORDER:
        return STATUS_ORDER.index(status)
    if status in CLOSED_STATUSES:
        return -1
    return 0


def _norm_str(value) -> str:
    if isinstance(value, str):
        return value.strip()
    return value if value is not None else ""


def _is_empty(value) -> bool:
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, bool):
        return value is False
    return value in (None, 0, "")


def _min_date(dates: list[str]) -> str:
    valid = [d for d in dates if d]
    return min(valid) if valid else ""


def _max_date(dates: list[str]) -> str:
    valid = [d for d in dates if d]
    return max(valid) if valid else ""


def _merge_contacts(group: list[dict]) -> list[dict]:
    """Union contacts across the group, deduped by normalized name (first wins)."""
    merged: list[dict] = []
    seen: set[str] = set()
    for entry in group:
        for c in entry.get("contacts", []) or []:
            key = _norm(c.get("name"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(c)
    return merged


def _merge_history(group: list[dict]) -> list[dict]:
    """Concatenate history, drop exact-duplicate events, sort by date."""
    merged: list[dict] = []
    seen: set[tuple] = set()
    for entry in group:
        for h in entry.get("history", []) or []:
            key = (h.get("date", ""), h.get("event", ""), h.get("note", ""))
            if key in seen:
                continue
            seen.add(key)
            merged.append(h)
    merged.sort(key=lambda h: h.get("date", ""))
    return merged


def _group_is_identical(group: list[dict]) -> bool:
    """True when every entry shares the same substantive scalar values."""
    first = group[0]
    for other in group[1:]:
        for field in _SUBSTANTIVE_FIELDS:
            if _norm_str(first.get(field)) != _norm_str(other.get(field)):
                return False
    return True


def _merge_group(group: list[dict]) -> tuple[dict, list[str]]:
    """Merge a divergent group into one survivor entry.

    The survivor is the most-advanced by status. Scalars prefer non-empty
    values; genuine conflicts keep the survivor's value and are reported.
    Returns (survivor, conflict_notes).
    """
    survivor = max(group, key=lambda e: _status_rank(e.get("status", "")))
    notes: list[str] = []

    # Status / dates.
    survivor["status"] = max(
        (e.get("status", "") for e in group), key=_status_rank
    )
    survivor["date_added"] = _min_date([e.get("date_added", "") for e in group])
    survivor["last_touchpoint"] = _max_date(
        [e.get("last_touchpoint", "") for e in group]
    )
    survivor["apply_by_date"] = _min_date(
        [e.get("apply_by_date", "") for e in group]
    )

    # recruiter_initiated = True if any entry was recruiter-led.
    survivor["recruiter_initiated"] = any(
        e.get("recruiter_initiated") for e in group
    )

    # Prefer-non-empty scalars: fill blanks, report real conflicts.
    for field in _PREFER_NONEMPTY_FIELDS:
        chosen = survivor.get(field)
        for other in group:
            if other is survivor:
                continue
            val = other.get(field)
            if _is_empty(chosen) and not _is_empty(val):
                survivor[field] = val
                chosen = val
            elif (
                not _is_empty(chosen)
                and not _is_empty(val)
                and _norm_str(chosen) != _norm_str(val)
            ):
                notes.append(
                    f"{field}: kept '{chosen}', dropped conflicting '{val}'"
                )

    # Nested data.
    survivor["contacts"] = _merge_contacts(group)
    survivor["history"] = _merge_history(group)
    return survivor, notes


def dedupe(apply: bool = False) -> dict:
    """Find and resolve duplicate (company, role) tracker entries.

    Groups entries by the normalized (company, role) pair. For each group with
    more than one entry:

    * **Identical** substantive content -> collapse to one, delete the rest.
      Contacts and history from all copies are still unioned into the keeper, so
      a delete never loses nested data.
    * **Divergent** content -> merge into a single survivor (most-advanced
      status), preferring non-empty scalars and reporting real conflicts.

    Default is a PREVIEW: nothing is written. Pass ``apply=True`` to write the
    deduped list back (a timestamped JSON backup is saved first). Regenerating
    the CSV/HTML is the caller's responsibility (see csv_sync.sync()).

    Returns a summary dict::

        {
          "applied": bool,
          "total_before": int,
          "total_after": int,
          "deleted": int,           # entries removed by collapse
          "merged_groups": int,     # groups resolved by merge
          "deleted_groups": int,    # groups resolved by pure delete
          "backup": str,            # backup path, "" when preview
          "groups": [ {company, role, count, action, kept_status,
                       removed: int, conflicts: [str]} ]
        }
    """
    entries = load()
    groups: dict[tuple[str, str], list[dict]] = {}
    order: list[tuple[str, str]] = []
    for e in entries:
        key = (_norm(e.get("company")), _norm(e.get("role")))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(e)

    survivors: list[dict] = []
    summary = {
        "applied": apply,
        "total_before": len(entries),
        "total_after": 0,
        "deleted": 0,
        "merged_groups": 0,
        "deleted_groups": 0,
        "backup": "",
        "groups": [],
    }

    for key in order:
        group = groups[key]
        if len(group) == 1:
            survivors.append(group[0])
            continue

        company = group[0].get("company", "")
        role = group[0].get("role", "")
        removed = len(group) - 1

        if _group_is_identical(group):
            # Pure delete, but still preserve nested data on the keeper.
            keeper = max(group, key=lambda e: _status_rank(e.get("status", "")))
            keeper["contacts"] = _merge_contacts(group)
            keeper["history"] = _merge_history(group)
            survivors.append(keeper)
            summary["deleted"] += removed
            summary["deleted_groups"] += 1
            summary["groups"].append({
                "company": company,
                "role": role,
                "count": len(group),
                "action": "delete",
                "kept_status": keeper.get("status", ""),
                "removed": removed,
                "conflicts": [],
            })
        else:
            survivor, conflicts = _merge_group(group)
            survivors.append(survivor)
            summary["deleted"] += removed
            summary["merged_groups"] += 1
            summary["groups"].append({
                "company": company,
                "role": role,
                "count": len(group),
                "action": "merge",
                "kept_status": survivor.get("status", ""),
                "removed": removed,
                "conflicts": conflicts,
            })

    summary["total_after"] = len(survivors)

    if apply and summary["deleted"]:
        summary["backup"] = str(_backup_json())
        save(survivors)
        logger.info(
            "Dedupe applied: %d entries -> %d (%d merged group(s), %d delete group(s))",
            summary["total_before"],
            summary["total_after"],
            summary["merged_groups"],
            summary["deleted_groups"],
        )
    elif summary["deleted"]:
        logger.info(
            "Dedupe preview: %d duplicate group(s) found, %d entry(ies) would be removed",
            summary["merged_groups"] + summary["deleted_groups"],
            summary["deleted"],
        )
    else:
        logger.info("Dedupe: no duplicate (company, role) groups found")

    return summary


def backup_tracker(infix: str = "backup") -> Path:
    """Write a timestamped copy of the tracker JSON next to it. Returns the path.

    The copy is named `<stem>.<infix>_<YYYYMMDD_HHMMSS><suffix>`. `infix`
    distinguishes backup provenance (e.g. "backup" for dedupe/migration,
    "prebulk" for a pre-bulk-run snapshot). If the source does not exist yet the
    returned path is where the backup would have gone (nothing is written).
    """
    src = _tracker_path()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = src.with_name(f"{src.stem}.{infix}_{stamp}{src.suffix}")
    if src.exists():
        backup.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        logger.info("Tracker JSON backed up: %s", backup)
    return backup


def _backup_json() -> Path:
    """Back up the tracker JSON (dedupe/migration provenance)."""
    return backup_tracker("backup")


def display_pipeline() -> None:
    """Print quick pipeline summary to terminal."""
    entries = load()
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    in_que = [e for e in entries if e.get("status") == "in_que"]
    active = [
        e for e in entries
        if e.get("status") not in CLOSED_STATUSES and e.get("status") != "in_que"
    ]
    closed = [e for e in entries if e.get("status") in CLOSED_STATUSES]

    width = 72
    print(f"\n{'═' * width}")
    print(f"  FORGE PIPELINE                                  {today_str}")
    print(f"{'═' * width}")

    # Action flags
    flags = []
    for e in active:
        action = compute_next_action(e)
        if action["urgency"] in ("overdue", "urgent"):
            flags.append(f"  ⚡ {e['company'][:30]} — {action['text']}")
        abd = e.get("apply_by_date", "")
        if abd:
            try:
                delta = (date.fromisoformat(abd) - today).days
                if delta <= 7:
                    label = "PAST DUE" if delta < 0 else f"{delta}d left"
                    flags.append(f"  ⚡ {e['company'][:30]} — Apply by {abd} ({label})")
            except ValueError:
                pass

    if flags:
        print("\n  TODAY'S ACTIONS")
        print(f"  {'─' * (width - 4)}")
        for f in flags:
            print(f)

    print(f"\n  IN FLIGHT ({len(active)})")
    print(f"  {'─' * (width - 4)}")
    if active:
        print(f"  {'Company':<22} {'Fit':<8} {'Sc':>3}  {'Status':<14} {'Days':>4}  Contacts")
        print(f"  {'─' * (width - 6)}")
        for e in active:
            days = (today - date.fromisoformat(e.get("date_added", today_str))).days
            v = {"STRONG_FIT": "STRONG", "STRETCH": "STRETCH", "HARD_PASS": "HARD↑"}.get(e.get("fit_verdict", ""), "")
            contacts = len(e.get("contacts", []))
            print(f"  {e['company'][:21]:<22} {v:<8} {e.get('fit_score', 0):>3}  {e.get('status', '')[:13]:<14} {days:>4}d  {contacts} contact{'s' if contacts != 1 else ''}")
    else:
        print("  (none)")

    if in_que:
        print(f"\n  IN QUE ({len(in_que)})")
        print(f"  {'─' * (width - 4)}")
        print(f"  {'Company':<22} {'Fit':<8} {'Sc':>3}  Role")
        print(f"  {'─' * (width - 6)}")
        for e in in_que:
            v = {"STRONG_FIT": "STRONG", "STRETCH": "STRETCH", "HARD_PASS": "HARD↑"}.get(e.get("fit_verdict", ""), "")
            print(f"  {e['company'][:21]:<22} {v:<8} {e.get('fit_score', 0):>3}  {e.get('role', '')[:30]}")

    if closed:
        print(f"\n  CLOSED ({len(closed)})")
        print(f"  {'─' * (width - 4)}")
        for e in closed:
            print(f"  {e['company'][:30]:<30} {e.get('status', ''):<12} {e.get('fit_verdict', '')}")

    if entries:
        scores = [e["fit_score"] for e in entries if e.get("fit_score", 0) > 0]
        avg = sum(scores) / len(scores) if scores else 0
        strong = sum(1 for e in entries if e.get("fit_verdict") == "STRONG_FIT")
        stretch = sum(1 for e in entries if e.get("fit_verdict") == "STRETCH")
        override = sum(1 for e in entries if e.get("fit_verdict") == "HARD_PASS")
        print(f"\n  {'─' * (width - 4)}")
        print(f"  Total: {len(entries)}  Strong: {strong}  Stretch: {stretch}  Override: {override}  Avg: {avg:.1f}/10")
        print("\n  HTML dashboard: outputs/pipeline_view.html")
    print(f"{'═' * width}\n")
