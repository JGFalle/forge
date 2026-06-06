"""Weekly pipeline digest, executive summary of the active job search.

Prints to terminal and saves an HTML version to outputs/.
Triggered by: python run.py --digest
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from pipeline.tracker.tracker import (
    CLOSED_STATUSES,
    STATUS_ORDER,
    compute_next_action,
    load,
)
from utils.logging import get_logger

logger = get_logger(__name__)

# Tier classification - mirrors CLAUDE.md
_TIER1 = {"home depot", "the coca-cola company", "coca-cola", "americold", "delta air lines", "delta", "ups", "georgia-pacific"}
_TIER2 = {"manhattan associates", "blue yonder", "greyorange", "symbotic", "autostore", "dematic", "o9 solutions", "kinaxis"}
_TIER3 = {"ey", "deloitte", "accenture", "4flow", "maine pointe", "alixpartners"}


def _tier(company: str) -> str:
    c = company.lower()
    if c in _TIER1:
        return "1"
    if c in _TIER2:
        return "2"
    if c in _TIER3:
        return "3"
    return "—"


def _days_since(date_str: str) -> int:
    try:
        return (date.today() - date.fromisoformat(date_str)).days
    except (ValueError, TypeError):
        return 0


def _status_emoji(status: str) -> str:
    return {
        "prompted": "○",
        "applied": "→",
        "phone_screen": "☎",
        "interview": "★",
        "final_round": "◆",
        "offer": "✓",
        "accepted": "✓✓",
        "rejected": "✗",
        "ghosted": "…",
        "declined": "↩",
        "withdrawn": "↩",
    }.get(status, "?")


def generate() -> dict:
    """Compute digest data from tracker. Returns structured dict."""
    entries = load()
    today = date.today()
    today_str = str(today)
    week_ago = today - timedelta(days=7)

    active = [e for e in entries if e.get("status") not in CLOSED_STATUSES]
    closed = [e for e in entries if e.get("status") in CLOSED_STATUSES]

    # Status distribution
    status_counts = Counter(e.get("status", "prompted") for e in active)

    # Urgent actions
    urgent = []
    for e in active:
        action = compute_next_action(e)
        if action["urgency"] in ("overdue", "urgent"):
            urgent.append({
                "company": e["company"],
                "role": e["role"],
                "action": action["text"],
                "urgency": action["urgency"],
                "due_date": action.get("due_date", ""),
            })

    # Applications added this week
    new_this_week = [
        e for e in entries
        if _days_since(e.get("date_added", today_str)) <= 7
    ]

    # Interviews scheduled / in flight
    interviews = [e for e in active if e.get("status") in ("interview", "final_round", "phone_screen")]

    # Tier coverage
    tier_counts = Counter(_tier(e["company"]) for e in active)

    # Conversion funnel (closed only, need enough data to be meaningful)
    applied_count = len([e for e in entries if e.get("status") not in ("prompted",)])
    phone_count = sum(
        1 for e in entries
        if any(h.get("event") in ("phone_screen",) for h in e.get("history", []))
    )
    interview_count = sum(
        1 for e in entries
        if any(h.get("event") in ("interview", "final_round") for h in e.get("history", []))
    )
    offer_count = sum(
        1 for e in entries
        if any(h.get("event") == "offer" for h in e.get("history", []))
    )
    accepted_count = len([e for e in closed if e.get("status") == "accepted"])
    rejected_count = len([e for e in closed if e.get("status") in ("rejected", "ghosted")])

    # Avg days in pipeline for active entries
    avg_days_active = 0.0
    if active:
        days_list = [_days_since(e.get("date_added", today_str)) for e in active]
        avg_days_active = sum(days_list) / len(days_list)

    # Days since last application submitted
    applied_entries = [
        e for e in entries
        if any(h.get("event") == "applied" for h in e.get("history", []))
    ]
    days_since_last_app = None
    if applied_entries:
        latest = max(
            (
                next(
                    (h["date"] for h in reversed(e.get("history", [])) if h.get("event") == "applied"),
                    e.get("date_added", "2000-01-01"),
                )
                for e in applied_entries
            )
        )
        days_since_last_app = _days_since(latest)

    # Avg score for active entries
    scores = [e.get("fit_score", 0) for e in active if e.get("fit_score", 0) > 0]
    avg_score = sum(scores) / len(scores) if scores else 0

    return {
        "date": today_str,
        "total": len(entries),
        "active_count": len(active),
        "closed_count": len(closed),
        "status_counts": dict(status_counts),
        "urgent": urgent,
        "new_this_week": len(new_this_week),
        "interview_count_active": len(interviews),
        "tier_counts": dict(tier_counts),
        "applied_count": applied_count,
        "phone_count": phone_count,
        "interview_count_all": interview_count,
        "offer_count": offer_count,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "avg_days_active": round(avg_days_active, 1),
        "days_since_last_app": days_since_last_app,
        "avg_score": round(avg_score, 1),
        "active": active,
    }


def display(data: dict) -> None:
    """Print digest to terminal."""
    width = 72
    print(f"\n{'═' * width}")
    print(f"  FORGE WEEKLY DIGEST                             {data['date']}")
    print(f"{'═' * width}")

    # Velocity
    days_last = data.get("days_since_last_app")
    days_msg = f"{days_last}d ago" if days_last is not None else "none tracked"
    print(f"\n  VELOCITY")
    print(f"  {'─' * (width - 4)}")
    print(f"  Active applications:  {data['active_count']}")
    print(f"  Added this week:      {data['new_this_week']}")
    print(f"  Last application:     {days_msg}")
    print(f"  Avg days in pipeline: {data['avg_days_active']}d")
    print(f"  Avg fit score:        {data['avg_score']}/10")

    # Urgent actions
    if data["urgent"]:
        print(f"\n  TODAY'S ACTIONS  ({'─' * 4} need attention {'─' * 4})")
        print(f"  {'─' * (width - 4)}")
        for item in data["urgent"]:
            flag = "OVERDUE" if item["urgency"] == "overdue" else "URGENT"
            print(f"  [{flag}] {item['company'][:28]} — {item['action']}")

    # Pipeline by stage
    print(f"\n  PIPELINE BY STAGE ({data['active_count']} active)")
    print(f"  {'─' * (width - 4)}")
    for status in STATUS_ORDER:
        count = data["status_counts"].get(status, 0)
        if count > 0:
            bar = "█" * count
            print(f"  {_status_emoji(status)} {status:<14} {bar} {count}")

    # Company tier coverage
    print(f"\n  TIER COVERAGE")
    print(f"  {'─' * (width - 4)}")
    for tier_label, tier_key in [("Tier 1 (F500 operator)", "1"), ("Tier 2 (tech/automation)", "2"), ("Tier 3 (consulting)", "3"), ("Other", "—")]:
        n = data["tier_counts"].get(tier_key, 0)
        if n > 0:
            print(f"  {tier_label:<26} {n} active")

    # Funnel (only show when there's data)
    if data["applied_count"] > 0:
        print(f"\n  CONVERSION FUNNEL")
        print(f"  {'─' * (width - 4)}")
        _funnel_row("Applied", data["applied_count"], data["applied_count"])
        _funnel_row("Phone screen", data["phone_count"], data["applied_count"])
        _funnel_row("Interview", data["interview_count_all"], data["applied_count"])
        _funnel_row("Offer", data["offer_count"], data["applied_count"])
        _funnel_row("Accepted", data["accepted_count"], data["applied_count"])
        _funnel_row("Rejected / ghosted", data["rejected_count"], data["applied_count"])

    # Health check callouts
    health_notes = _health_check(data)
    if health_notes:
        print(f"\n  SEARCH HEALTH")
        print(f"  {'─' * (width - 4)}")
        for note in health_notes:
            print(f"  {note}")

    print(f"\n{'═' * width}\n")


def _funnel_row(label: str, count: int, base: int) -> None:
    if count == 0:
        return
    pct = round(count / base * 100) if base else 0
    print(f"  {label:<22} {count:>3}  ({pct}%)")


def _health_check(data: dict) -> list[str]:
    notes = []
    if data.get("days_since_last_app") is not None and data["days_since_last_app"] > 7:
        notes.append(f"! No application submitted in {data['days_since_last_app']} days — cadence slipping.")
    if data["active_count"] == 0:
        notes.append("! No active applications. Pipeline is empty.")
    if data["active_count"] > 0 and data["tier_counts"].get("1", 0) == 0:
        notes.append("! No Tier 1 companies in active pipeline — add at least one F500 target.")
    if data["phone_count"] > 0 and data["interview_count_all"] == 0:
        notes.append("! Phone screens converting to 0 interviews — review pitch for screen-to-interview gap.")
    if data["avg_score"] > 0 and data["avg_score"] < 6.0:
        notes.append(f"! Average fit score is low ({data['avg_score']}/10) — targeting too many stretch roles.")
    return notes


def save_html(data: dict, output_dir: Path) -> Path:
    """Save digest as HTML to outputs/ for archiving."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"digest_{data['date']}.html"

    funnel_rows = ""
    funnel_pairs = [
        ("Applied", data["applied_count"]),
        ("Phone screen", data["phone_count"]),
        ("Interview", data["interview_count_all"]),
        ("Offer", data["offer_count"]),
        ("Accepted", data["accepted_count"]),
    ]
    base = max(data["applied_count"], 1)
    for label, count in funnel_pairs:
        if count > 0:
            pct = round(count / base * 100)
            funnel_rows += f"<tr><td>{label}</td><td>{count}</td><td>{pct}%</td></tr>\n"

    urgent_rows = ""
    for item in data.get("urgent", []):
        flag = "OVERDUE" if item["urgency"] == "overdue" else "URGENT"
        urgent_rows += f"<tr class='urgent'><td>[{flag}]</td><td>{item['company']}</td><td>{item['role'][:40]}</td><td>{item['action']}</td></tr>\n"

    pipeline_rows = ""
    for e in data.get("active", []):
        tier = _tier(e["company"])
        score = e.get("fit_score", 0)
        days = _days_since(e.get("date_added", data["date"]))
        contacts = len(e.get("contacts", []))
        status = e.get("status", "")
        pipeline_rows += (
            f"<tr>"
            f"<td>{e['company']}</td>"
            f"<td>{e['role'][:45]}</td>"
            f"<td>{_status_emoji(status)} {status}</td>"
            f"<td>T{tier}</td>"
            f"<td>{score}/10</td>"
            f"<td>{days}d</td>"
            f"<td>{contacts}</td>"
            f"</tr>\n"
        )

    days_last = data.get("days_since_last_app")
    days_last_str = f"{days_last}d ago" if days_last is not None else "none"

    health_notes = _health_check(data)
    health_html = "".join(f"<li>{n}</li>" for n in health_notes) if health_notes else "<li>No issues flagged.</li>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>FORGE Digest — {data['date']}</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f1117; color: #e2e8f0; margin: 0; padding: 32px; }}
  h1 {{ color: #93c5fd; font-size: 1.4rem; margin-bottom: 4px; }}
  .sub {{ color: #64748b; font-size: 0.85rem; margin-bottom: 32px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 20px; margin-bottom: 32px; }}
  .card {{ background: #1e2230; border-radius: 8px; padding: 20px; }}
  .card h2 {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: .08em; color: #64748b; margin: 0 0 12px; }}
  .stat {{ font-size: 2rem; font-weight: 700; color: #93c5fd; }}
  .stat-label {{ font-size: 0.8rem; color: #94a3b8; margin-top: 2px; }}
  table {{ width: 100%; border-collapse: collapse; background: #1e2230; border-radius: 8px; overflow: hidden; margin-bottom: 24px; }}
  th {{ background: #0f1117; color: #64748b; font-size: 0.7rem; text-transform: uppercase; letter-spacing: .06em; padding: 10px 12px; text-align: left; }}
  td {{ padding: 9px 12px; font-size: 0.85rem; border-top: 1px solid #2d3348; }}
  tr.urgent td {{ color: #f87171; }}
  h3 {{ color: #93c5fd; font-size: 0.9rem; margin: 24px 0 8px; text-transform: uppercase; letter-spacing: .06em; }}
  ul {{ color: #fbbf24; font-size: 0.85rem; padding-left: 20px; }}
  ul li {{ margin-bottom: 6px; }}
</style>
</head>
<body>
<h1>FORGE Weekly Digest</h1>
<div class="sub">{data['date']} &nbsp;|&nbsp; {data['active_count']} active &nbsp;|&nbsp; {data['total']} total</div>

<div class="grid">
  <div class="card">
    <h2>Active</h2>
    <div class="stat">{data['active_count']}</div>
    <div class="stat-label">{data['new_this_week']} added this week</div>
  </div>
  <div class="card">
    <h2>Last Application</h2>
    <div class="stat">{days_last_str}</div>
    <div class="stat-label">Avg {data['avg_days_active']}d in pipeline</div>
  </div>
  <div class="card">
    <h2>Avg Fit Score</h2>
    <div class="stat">{data['avg_score']}/10</div>
    <div class="stat-label">Active applications only</div>
  </div>
  <div class="card">
    <h2>Offers</h2>
    <div class="stat">{data['offer_count']}</div>
    <div class="stat-label">{data['accepted_count']} accepted &nbsp;|&nbsp; {data['rejected_count']} closed lost</div>
  </div>
</div>

{"<h3>Urgent Actions</h3><table><tr><th></th><th>Company</th><th>Role</th><th>Action</th></tr>" + urgent_rows + "</table>" if urgent_rows else ""}

<h3>Active Pipeline</h3>
<table>
  <tr><th>Company</th><th>Role</th><th>Status</th><th>Tier</th><th>Fit</th><th>Age</th><th>Contacts</th></tr>
  {pipeline_rows}
</table>

<h3>Conversion Funnel</h3>
<table>
  <tr><th>Stage</th><th>Count</th><th>vs Applied</th></tr>
  {funnel_rows}
</table>

<h3>Search Health</h3>
<ul>{health_html}</ul>

</body>
</html>"""

    out.write_text(html, encoding="utf-8")
    logger.info("Digest saved: %s", out)
    return out
