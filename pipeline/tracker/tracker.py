"""Application tracker — reads and writes application_tracker.json.

Status progression:
  prompted → applied → phone_screen → interview → final_round → offer → accepted
                                                                        → declined
  (any stage) → rejected | ghosted | withdrawn

Contact types: warm_referral | hiring_manager | recruiter | intel_contact
Channels:      linkedin | email | phone | in_person
"""

from __future__ import annotations

import json
from datetime import datetime, date, timedelta
from pathlib import Path

from utils.config import get
from utils.logging import get_logger

logger = get_logger(__name__)

CLOSED_STATUSES = {"rejected", "declined", "ghosted", "withdrawn", "accepted"}
STATUS_ORDER = [
    "prompted", "applied", "phone_screen", "interview",
    "final_round", "offer", "accepted",
]


def _tracker_path() -> Path:
    return Path(get("paths.tracker_file", "outputs/application_tracker.json"))


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


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
) -> dict:
    entries = load()
    today = _today()

    existing = _find_entry(entries, company, role)
    if existing:
        # Update in place — don't create a duplicate, don't reset status
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
        "status": "prompted",
        "fit_verdict": fit_verdict or "",
        "fit_score": fit_score,
        "apply_by_date": apply_by_date or "",
        "app_folder": app_folder or "",
        "notes": notes or "",
        "override_reason": override_reason or "",
        "recruiter_initiated": False,
        "contacts": [],
        "last_touchpoint": "",
        "history": [{"date": today, "event": "prompted", "note": "Pipeline run — materials generated"}],
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
    that have gone silent. 'prompted' entries are skipped — they just haven't been
    submitted yet. Returns list of entries that were (or would be) updated.
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


def display_pipeline() -> None:
    """Print quick pipeline summary to terminal."""
    entries = load()
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    active = [e for e in entries if e.get("status") not in CLOSED_STATUSES]
    closed = [e for e in entries if e.get("status") in CLOSED_STATUSES]

    width = 72
    print(f"\n{'═' * width}")
    print(f"  PACE PIPELINE                                   {today_str}")
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
        print(f"\n  TODAY'S ACTIONS")
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
        print(f"\n  HTML dashboard: outputs/pipeline_view.html")
    print(f"{'═' * width}\n")
