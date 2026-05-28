"""Generate a self-contained HTML pipeline dashboard from application_tracker.json."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pipeline.tracker.tracker import (
    CLOSED_STATUSES, compute_next_action, load, _has_warm_referral
)
from utils.config import get

_VERDICT_LABEL = {
    "STRONG_FIT": "STRONG FIT",
    "STRETCH": "STRETCH",
    "HARD_PASS": "HARD PASS↑",
}
_VERDICT_CLASS = {
    "STRONG_FIT": "strong",
    "STRETCH": "stretch",
    "HARD_PASS": "hardpass",
}
_STATUS_CLASS = {
    "prompted": "status-prompted",
    "applied": "status-applied",
    "phone_screen": "status-screen",
    "interview": "status-interview",
    "final_round": "status-final",
    "offer": "status-offer",
    "accepted": "status-accepted",
    "rejected": "status-closed",
    "declined": "status-closed",
    "ghosted": "status-closed",
    "withdrawn": "status-closed",
}
_STATUS_LABEL = {
    "prompted": "Prompted",
    "applied": "Applied",
    "phone_screen": "Phone Screen",
    "interview": "Interview",
    "final_round": "Final Round",
    "offer": "Offer",
    "accepted": "Accepted",
    "rejected": "Rejected",
    "declined": "Declined",
    "ghosted": "Ghosted",
    "withdrawn": "Withdrawn",
}
_CONTACT_TYPE_LABEL = {
    "warm_referral": "Warm Referral",
    "hiring_manager": "Hiring Manager",
    "recruiter": "Recruiter",
    "intel_contact": "Research Contact",
}
_URGENCY_CLASS = {
    "overdue": "urgency-overdue",
    "urgent": "urgency-urgent",
    "upcoming": "urgency-upcoming",
    "ok": "urgency-ok",
    "none": "",
}


def generate(output_path: Path | None = None) -> Path:
    entries = load()
    today = date.today()
    out = output_path or Path(get("paths.outputs_dir", "outputs")) / "pipeline_view.html"
    out.parent.mkdir(parents=True, exist_ok=True)

    active = [e for e in entries if e.get("status") not in CLOSED_STATUSES]
    closed = [e for e in entries if e.get("status") in CLOSED_STATUSES]

    scores = [e["fit_score"] for e in entries if e.get("fit_score", 0) > 0]
    avg_score = sum(scores) / len(scores) if scores else 0
    strong_count = sum(1 for e in entries if e.get("fit_verdict") == "STRONG_FIT")
    stretch_count = sum(1 for e in entries if e.get("fit_verdict") == "STRETCH")
    override_count = sum(1 for e in entries if e.get("fit_verdict") == "HARD_PASS")

    # Collect today's action items
    action_items = []
    for e in active:
        abd = e.get("apply_by_date", "")
        if abd:
            try:
                delta = (date.fromisoformat(abd) - today).days
                if delta <= 7:
                    label = "PAST DUE" if delta < 0 else f"{delta} day{'s' if delta != 1 else ''} left"
                    action_items.append({
                        "company": e["company"],
                        "role": e["role"],
                        "text": f"Apply by deadline: {abd}",
                        "detail": label,
                        "urgency": "overdue" if delta < 0 else ("urgent" if delta <= 2 else "upcoming"),
                    })
            except ValueError:
                pass
        action = compute_next_action(e)
        if action["urgency"] in ("overdue", "urgent", "upcoming"):
            action_items.append({
                "company": e["company"],
                "role": e["role"],
                "text": action["text"],
                "detail": f"Due: {action['due_date']}" if action.get("due_date") else "",
                "urgency": action["urgency"],
            })

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FORGE Pipeline — {get("person.name", "")}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --navy:    #1B3A6B;
    --navy-lt: #2a5298;
    --green:   #16a34a;
    --amber:   #d97706;
    --red:     #dc2626;
    --orange:  #ea580c;
    --blue:    #2563eb;
    --gray:    #6b7280;
    --bg:      #f1f5f9;
    --card:    #ffffff;
    --border:  #e2e8f0;
    --text:    #1e293b;
    --text-sm: #475569;
  }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    font-size: 14px;
    line-height: 1.5;
  }}

  header {{
    background: var(--navy);
    color: white;
    padding: 24px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 16px;
  }}
  header h1 {{ font-size: 22px; font-weight: 700; letter-spacing: 0.5px; }}
  header .meta {{ font-size: 12px; opacity: 0.7; margin-top: 2px; }}

  .stats-bar {{
    display: flex;
    gap: 24px;
    flex-wrap: wrap;
  }}
  .stat {{
    text-align: center;
  }}
  .stat-value {{ font-size: 22px; font-weight: 700; }}
  .stat-label {{ font-size: 11px; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.5px; }}

  main {{ max-width: 1100px; margin: 0 auto; padding: 24px 16px; }}

  section {{ margin-bottom: 32px; }}
  section h2 {{
    font-size: 13px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--text-sm);
    margin-bottom: 12px;
    padding-bottom: 6px;
    border-bottom: 2px solid var(--border);
  }}

  /* Action items */
  .action-list {{ display: flex; flex-direction: column; gap: 8px; }}
  .action-item {{
    display: flex;
    align-items: flex-start;
    gap: 12px;
    background: var(--card);
    border-radius: 8px;
    padding: 12px 16px;
    border-left: 4px solid var(--gray);
  }}
  .action-item.urgency-overdue {{ border-left-color: var(--red); }}
  .action-item.urgency-urgent  {{ border-left-color: var(--orange); }}
  .action-item.urgency-upcoming {{ border-left-color: var(--amber); }}
  .action-icon {{ font-size: 18px; line-height: 1; }}
  .action-body {{ flex: 1; }}
  .action-company {{ font-weight: 600; font-size: 13px; }}
  .action-role {{ font-size: 12px; color: var(--text-sm); }}
  .action-text {{ font-size: 13px; margin-top: 2px; }}
  .action-detail {{ font-size: 11px; color: var(--text-sm); margin-top: 2px; }}

  /* Application cards */
  .app-grid {{ display: flex; flex-direction: column; gap: 12px; }}
  .app-card {{
    background: var(--card);
    border-radius: 10px;
    border: 1px solid var(--border);
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  }}
  .app-card-header {{
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    padding: 16px 20px 12px;
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
  }}
  .app-card-header.strong  {{ border-left: 4px solid var(--green); }}
  .app-card-header.stretch {{ border-left: 4px solid var(--amber); }}
  .app-card-header.hardpass {{ border-left: 4px solid var(--orange); }}

  .company-name {{ font-size: 16px; font-weight: 700; color: var(--navy); }}
  .role-name {{ font-size: 13px; color: var(--text-sm); margin-top: 2px; }}

  .badges {{ display: flex; gap: 6px; flex-wrap: wrap; align-items: center; flex-shrink: 0; }}
  .badge {{
    font-size: 11px;
    font-weight: 700;
    padding: 3px 8px;
    border-radius: 20px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    white-space: nowrap;
  }}
  .badge-strong  {{ background: #dcfce7; color: #166534; }}
  .badge-stretch {{ background: #fef3c7; color: #92400e; }}
  .badge-hardpass {{ background: #ffedd5; color: #9a3412; }}
  .badge-score   {{ background: var(--bg); color: var(--text-sm); font-weight: 600; }}

  .status-badge {{ padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 700; }}
  .status-prompted  {{ background: #e0f2fe; color: #0369a1; }}
  .status-applied   {{ background: #dbeafe; color: #1d4ed8; }}
  .status-screen    {{ background: #ede9fe; color: #6d28d9; }}
  .status-interview {{ background: #f3e8ff; color: #7e22ce; }}
  .status-final     {{ background: #fce7f3; color: #9d174d; }}
  .status-offer     {{ background: #dcfce7; color: #166534; }}
  .status-accepted  {{ background: #166534; color: white; }}
  .status-closed    {{ background: #f1f5f9; color: #6b7280; }}

  .app-card-body {{ padding: 14px 20px; display: flex; gap: 24px; flex-wrap: wrap; }}

  .app-meta {{
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 160px;
  }}
  .meta-row {{ font-size: 12px; color: var(--text-sm); }}
  .meta-row span {{ font-weight: 600; color: var(--text); }}

  .app-engagement {{ flex: 1; min-width: 260px; }}
  .engagement-title {{
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-sm);
    margin-bottom: 8px;
  }}

  .contact-list {{ display: flex; flex-direction: column; gap: 4px; margin-bottom: 10px; }}
  .contact-row {{ display: flex; align-items: center; gap: 8px; font-size: 12px; }}
  .contact-type {{
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 10px;
    background: var(--bg);
    color: var(--text-sm);
    font-weight: 600;
    text-transform: uppercase;
    white-space: nowrap;
  }}
  .contact-warm {{ background: #dcfce7; color: #166534; }}
  .contact-recruiter {{ background: #dbeafe; color: #1d4ed8; }}
  .contact-hiring {{ background: #f3e8ff; color: #7e22ce; }}
  .contact-name {{ font-weight: 600; }}
  .contact-detail {{ color: var(--text-sm); }}

  .no-contacts {{ font-size: 12px; color: var(--text-sm); font-style: italic; margin-bottom: 10px; }}

  .next-action {{
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    background: var(--bg);
    color: var(--text-sm);
  }}
  .next-action.urgency-overdue {{ background: #fef2f2; color: var(--red); }}
  .next-action.urgency-urgent  {{ background: #fff7ed; color: var(--orange); }}
  .next-action.urgency-upcoming {{ background: #fffbeb; color: #92400e; }}
  .next-action .action-due {{ font-weight: 400; font-size: 11px; margin-top: 1px; color: inherit; opacity: 0.8; }}

  /* History */
  .app-history {{
    padding: 0 20px 14px;
    border-top: 1px solid var(--border);
    margin-top: 4px;
  }}
  .history-toggle {{
    font-size: 11px;
    color: var(--navy);
    cursor: pointer;
    padding: 8px 0 4px;
    display: block;
    font-weight: 600;
    text-decoration: none;
    user-select: none;
  }}
  .history-list {{
    display: none;
    flex-direction: column;
    gap: 3px;
    margin-top: 6px;
  }}
  .history-list.open {{ display: flex; }}
  .history-row {{ font-size: 11px; color: var(--text-sm); padding: 2px 0; }}
  .history-date {{ font-weight: 600; margin-right: 6px; }}

  /* Closed section */
  .closed-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .closed-table th {{
    text-align: left;
    padding: 6px 12px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-sm);
    background: var(--card);
    border-bottom: 1px solid var(--border);
  }}
  .closed-table td {{ padding: 8px 12px; border-bottom: 1px solid var(--border); background: var(--card); }}
  .closed-table tr:last-child td {{ border-bottom: none; }}

  .empty-state {{ text-align: center; padding: 32px; color: var(--text-sm); font-size: 13px; }}

  footer {{
    text-align: center;
    padding: 24px;
    font-size: 11px;
    color: var(--text-sm);
  }}
</style>
</head>
<body>

<header>
  <div>
    <h1>FORGE Pipeline</h1>
    <div class="meta">{get("person.name", "")} &nbsp;·&nbsp; Generated {today.strftime('%B %d, %Y')}</div>
  </div>
  <div class="stats-bar">
    <div class="stat">
      <div class="stat-value">{len(active)}</div>
      <div class="stat-label">In Flight</div>
    </div>
    <div class="stat">
      <div class="stat-value">{strong_count}</div>
      <div class="stat-label">Strong Fit</div>
    </div>
    <div class="stat">
      <div class="stat-value">{stretch_count}</div>
      <div class="stat-label">Stretch</div>
    </div>
    <div class="stat">
      <div class="stat-value">{override_count}</div>
      <div class="stat-label">Override</div>
    </div>
    <div class="stat">
      <div class="stat-value">{avg_score:.1f}</div>
      <div class="stat-label">Avg Fit</div>
    </div>
  </div>
</header>

<main>
{_render_actions(action_items)}
{_render_active(active, today)}
{_render_closed(closed)}
</main>

<footer>
  PACE · Personal Applicant Competitive Engine &nbsp;·&nbsp; {today.strftime('%Y-%m-%d')}
</footer>

<script>
  document.querySelectorAll('.history-toggle').forEach(function(el) {{
    el.addEventListener('click', function() {{
      var list = this.nextElementSibling;
      list.classList.toggle('open');
      this.textContent = list.classList.contains('open') ? '▲ Hide history' : '▼ Show history';
    }});
  }});
</script>
</body>
</html>"""

    out.write_text(html, encoding="utf-8")
    return out


def _render_actions(items: list[dict]) -> str:
    if not items:
        return ""
    icon_map = {"overdue": "🔴", "urgent": "⚡", "upcoming": "🟡", "ok": ""}
    rows = ""
    for item in items:
        icon = icon_map.get(item["urgency"], "•")
        urg = item["urgency"]
        rows += f"""
      <div class="action-item urgency-{urg}">
        <div class="action-icon">{icon}</div>
        <div class="action-body">
          <div class="action-company">{_esc(item['company'])}</div>
          <div class="action-role">{_esc(item['role'][:80])}</div>
          <div class="action-text">{_esc(item['text'])}</div>
          {"<div class='action-detail'>" + _esc(item['detail']) + "</div>" if item.get('detail') else ""}
        </div>
      </div>"""
    return f"""
  <section>
    <h2>Today's Actions ({len(items)})</h2>
    <div class="action-list">{rows}
    </div>
  </section>"""


def _render_active(entries: list[dict], today: date) -> str:
    if not entries:
        body = '<div class="empty-state">No active applications. Drop a JD PDF to get started.</div>'
    else:
        body = '<div class="app-grid">' + "".join(_render_card(e, today) for e in entries) + "</div>"
    return f"""
  <section>
    <h2>In Flight ({len(entries)})</h2>
    {body}
  </section>"""


def _render_card(e: dict, today: date) -> str:
    verdict = e.get("fit_verdict", "")
    vc = _VERDICT_CLASS.get(verdict, "")
    vl = _VERDICT_LABEL.get(verdict, verdict)
    score = e.get("fit_score", 0)
    status = e.get("status", "prompted")
    sc = _STATUS_CLASS.get(status, "status-prompted")
    sl = _STATUS_LABEL.get(status, status.replace("_", " ").title())
    days = (today - date.fromisoformat(e.get("date_added", str(today)))).days

    # Meta block
    meta = f"""
      <div class="app-meta">
        <div class="meta-row">Comp: <span>{_esc(e.get('salary_range') or 'Not listed')}</span></div>
        <div class="meta-row">Req: <span>{_esc(e.get('req_number') or '—')}</span></div>
        <div class="meta-row">ATS: <span>{_esc(e.get('ats_system') or '—')}</span></div>
        <div class="meta-row">In stage: <span>{days}d</span></div>
        {"<div class='meta-row'>Apply by: <span>" + _esc(e.get('apply_by_date', '')) + "</span></div>" if e.get('apply_by_date') else ""}
        {"<div class='meta-row'>Recruiter-led: <span>Yes</span></div>" if e.get('recruiter_initiated') else ""}
      </div>"""

    # Contacts
    contacts = e.get("contacts", [])
    if contacts:
        contact_rows = ""
        for c in contacts:
            ct = c.get("type", "intel_contact")
            ct_class = {"warm_referral": "contact-warm", "recruiter": "contact-recruiter", "hiring_manager": "contact-hiring"}.get(ct, "")
            ct_label = _CONTACT_TYPE_LABEL.get(ct, ct.replace("_", " ").title())
            channel = c.get("channel", "")
            last = c.get("last_contact", "")
            since = ""
            if last:
                try:
                    d = (today - date.fromisoformat(last)).days
                    since = f" · {d}d ago" if d > 0 else " · today"
                except ValueError:
                    pass
            notes = f" — {c['notes']}" if c.get("notes") else ""
            contact_rows += f"""
            <div class="contact-row">
              <span class="contact-type {ct_class}">{ct_label}</span>
              <span class="contact-name">{_esc(c['name'])}</span>
              {("<span class='contact-detail'>" + _esc(c.get('title','')) + "</span>") if c.get('title') else ""}
              <span class="contact-detail">{channel}{since}{_esc(notes)}</span>
            </div>"""
        contacts_html = f'<div class="contact-list">{contact_rows}</div>'
    else:
        contacts_html = '<div class="no-contacts">No contacts logged yet</div>'

    # Next action
    action = compute_next_action(e)
    action_urg = action.get("urgency", "ok")
    action_html = ""
    if action.get("text"):
        due_html = f'<div class="action-due">{_esc(action.get("due_date",""))}</div>' if action.get("due_date") else ""
        action_html = f"""
        <div class="next-action {_URGENCY_CLASS.get(action_urg, '')}">
          <div>
            <div>{_esc(action['text'])}</div>
            {due_html}
          </div>
        </div>"""

    engagement = f"""
      <div class="app-engagement">
        <div class="engagement-title">Engagement</div>
        {contacts_html}
        {action_html}
      </div>"""

    # History
    history = e.get("history", [])
    history_rows = "".join(
        f'<div class="history-row"><span class="history-date">{h.get("date","")}</span>{_esc(h.get("event",""))} {_esc(h.get("note",""))}</div>'
        for h in reversed(history)
    )
    history_html = f"""
    <div class="app-history">
      <a class="history-toggle">▼ Show history ({len(history)} events)</a>
      <div class="history-list">{history_rows}</div>
    </div>""" if history else ""

    return f"""
    <div class="app-card">
      <div class="app-card-header {vc}">
        <div>
          <div class="company-name">{_esc(e['company'])}</div>
          <div class="role-name">{_esc(e['role'])}</div>
        </div>
        <div class="badges">
          <span class="badge badge-{vc}">{vl}</span>
          <span class="badge badge-score">{score}/10</span>
          <span class="badge status-badge {sc}">{sl}</span>
        </div>
      </div>
      <div class="app-card-body">
        {meta}
        {engagement}
      </div>
      {history_html}
    </div>"""


def _render_closed(entries: list[dict]) -> str:
    if not entries:
        return f"""
  <section>
    <h2>Closed (0)</h2>
    <div class="empty-state">Nothing closed yet.</div>
  </section>"""

    rows = ""
    for e in entries:
        verdict = e.get("fit_verdict", "")
        vc = _VERDICT_CLASS.get(verdict, "")
        vl = _VERDICT_LABEL.get(verdict, "")
        status = e.get("status", "")
        sc = _STATUS_CLASS.get(status, "status-closed")
        sl = _STATUS_LABEL.get(status, status)
        rows += f"""
        <tr>
          <td><strong>{_esc(e['company'])}</strong></td>
          <td>{_esc(e['role'][:60])}</td>
          <td><span class="badge badge-{vc}" style="font-size:10px">{vl}</span></td>
          <td><span class="badge status-badge {sc}">{sl}</span></td>
          <td>{e.get('date_added','')}</td>
        </tr>"""

    return f"""
  <section>
    <h2>Closed ({len(entries)})</h2>
    <table class="closed-table">
      <thead>
        <tr>
          <th>Company</th><th>Role</th><th>Fit</th><th>Outcome</th><th>Date</th>
        </tr>
      </thead>
      <tbody>{rows}
      </tbody>
    </table>
  </section>"""


def _esc(s: str | None) -> str:
    if not s:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
