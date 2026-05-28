"""Generate HTML LinkedIn optimization report from optimizer JSON output."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any


def _get_name() -> str:
    from utils.config import get
    return get("person.name", "")


def generate(result: dict, profile: dict[str, Any], output_dir: Path, today: str) -> Path:
    body = _build_body(result, profile)
    page = _wrap_page(body, today)
    out = output_dir / f"linkedin_optimization_{today}.html"
    out.write_text(page, encoding="utf-8")
    return out


# ── Section builders ──────────────────────────────────────────────────────────

def _build_body(result: dict, profile: dict) -> str:
    sections = []

    # Header
    sections.append(_header(result))

    # Implementation checklist (top of page — most actionable)
    sections.append(_checklist(result.get("implementation_checklist", [])))

    # Headline
    sections.append(_headline_section(result, profile))

    # About
    sections.append(_about_section(result, profile))

    # Experience
    sections.append(_experience_section(result, profile))

    # Skills
    sections.append(_skills_section(result, profile))

    # Featured recommendations
    sections.append(_featured_section(result.get("featured_recommendations", [])))

    # Keywords
    sections.append(_keywords_section(result))

    return "\n".join(sections)


def _header(result: dict) -> str:
    notes = html.escape(result.get("strategic_notes", ""))
    return f"""
<div class="header">
  <div class="header-title">LinkedIn Profile Optimization</div>
  <div class="header-sub">{html.escape(_get_name())} — Profile Optimization</div>
  <div class="strategy-note">{notes}</div>
</div>"""


def _checklist(items: list[dict]) -> str:
    if not items:
        return ""
    rows = ""
    for item in sorted(items, key=lambda x: x.get("priority", 99)):
        action = html.escape(item.get("action", ""))
        section = html.escape(item.get("section", ""))
        effort = html.escape(item.get("effort", ""))
        rows += f"""
      <tr>
        <td class="priority-cell">{item.get('priority', '')}</td>
        <td><strong>{section}</strong></td>
        <td>{effort}</td>
        <td>{action}</td>
      </tr>"""
    return f"""
<div class="card">
  <h2>Implementation Checklist</h2>
  <table class="checklist-table">
    <thead><tr><th>#</th><th>Section</th><th>Effort</th><th>Action</th></tr></thead>
    <tbody>{rows}
    </tbody>
  </table>
</div>"""


def _headline_section(result: dict, profile: dict) -> str:
    current = html.escape(profile.get("headline", ""))
    recommended = html.escape(result.get("headline", ""))
    rec_len = len(result.get("headline", ""))
    cur_len = len(profile.get("headline", ""))
    limit = 220
    target = 120

    current_block = (
        f'<div class="label">Current ({cur_len} chars)</div>'
        f'<div class="current-text">{current or "<em>(not provided)</em>"}</div>'
        if current else
        '<div class="label">Current</div><div class="current-text"><em>Not provided</em></div>'
    )

    return f"""
<div class="card">
  <h2>Headline</h2>
  <div class="two-col">
    <div class="col-current">
      {current_block}
    </div>
    <div class="col-recommended">
      <div class="label">Recommended ({rec_len} chars / {limit} max, target {target})</div>
      {_char_bar(rec_len, limit, target)}
      <div class="copy-block">{recommended}</div>
    </div>
  </div>
</div>"""


def _about_section(result: dict, profile: dict) -> str:
    current = html.escape(profile.get("about", ""))
    recommended = html.escape(result.get("about", ""))
    rec_len = len(result.get("about", ""))
    cur_len = len(profile.get("about", ""))
    limit = 2600
    target = 2200

    current_block = (
        f'<div class="label">Current ({cur_len} chars)</div>'
        f'<div class="current-text preserve-lines">{current}</div>'
        if current else
        '<div class="label">Current</div><div class="current-text"><em>Not provided</em></div>'
    )

    return f"""
<div class="card">
  <h2>About Section</h2>
  <div class="two-col">
    <div class="col-current">
      {current_block}
    </div>
    <div class="col-recommended">
      <div class="label">Recommended ({rec_len} chars / {limit} max)</div>
      {_char_bar(rec_len, limit, target)}
      <div class="copy-block preserve-lines">{recommended}</div>
    </div>
  </div>
</div>"""


def _experience_section(result: dict, profile: dict) -> str:
    exp = result.get("experience", {})
    blocks = []

    from utils.config import get
    history = get("career_history", [])
    role_map = [
        (r.get("id", ""), f"{r.get('company', '')} — {r.get('title', '')}")
        for r in history[:3]
    ]

    # Index current positions for lookup
    current_by_company: dict[str, str] = {}
    for pos in profile.get("positions", []):
        key = pos["company"].lower()
        if pos["description"]:
            current_by_company[key] = pos["description"]

    for key, label in role_map:
        role_data = exp.get(key, {})
        recommended_text = role_data.get("recommended", "")
        note = role_data.get("positioning_note", "")
        rec_len = len(recommended_text)

        current_desc = ""
        if "jmrs" in key:
            current_desc = current_by_company.get("jmrs consulting llc", "") or current_by_company.get("jmrs", "")
        else:
            current_desc = current_by_company.get("ryerson", "")

        cur_len = len(current_desc)

        current_block = (
            f'<div class="label">Current ({cur_len} chars)</div>'
            f'<div class="current-text preserve-lines">{html.escape(current_desc)}</div>'
            if current_desc else
            '<div class="label">Current</div><div class="current-text"><em>Not provided</em></div>'
        )

        blocks.append(f"""
  <h3>{label}</h3>
  {f'<div class="positioning-note">Positioning: {html.escape(note)}</div>' if note else ""}
  <div class="two-col">
    <div class="col-current">
      {current_block}
    </div>
    <div class="col-recommended">
      <div class="label">Recommended ({rec_len} chars / 2000 max)</div>
      {_char_bar(rec_len, 2000, 1400)}
      <div class="copy-block preserve-lines">{html.escape(recommended_text)}</div>
    </div>
  </div>""")

    return f"""
<div class="card">
  <h2>Experience</h2>
  {''.join(blocks)}
</div>"""


def _skills_section(result: dict, profile: dict) -> str:
    recommended = result.get("skills_recommended", [])
    current = set(s.lower() for s in profile.get("skills", []))

    rows = ""
    for i, skill in enumerate(recommended, 1):
        in_current = skill.lower() in current
        badge = '<span class="badge-present">In profile</span>' if in_current else '<span class="badge-missing">Add this</span>'
        top3 = ' <span class="badge-top3">Top 3</span>' if i <= 3 else ""
        rows += f"<tr><td>{i}</td><td>{html.escape(skill)}{top3}</td><td>{badge}</td></tr>"

    current_not_recommended = [s for s in profile.get("skills", []) if s.lower() not in {r.lower() for r in recommended}]
    keep_block = ""
    if current_not_recommended:
        keep_block = f'<div class="keep-note">Other current skills (keep or remove at discretion): {html.escape(", ".join(current_not_recommended[:20]))}</div>'

    return f"""
<div class="card">
  <h2>Skills</h2>
  <p>Reorder your top skills so the highest-priority ones appear in the first three (shown prominently on your profile).</p>
  <table class="skills-table">
    <thead><tr><th>#</th><th>Skill</th><th>Status</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  {keep_block}
</div>"""


def _featured_section(items: list[dict]) -> str:
    if not items:
        return ""
    rows = ""
    for item in sorted(items, key=lambda x: x.get("rank", 99)):
        kind = html.escape(item.get("type", ""))
        desc = html.escape(item.get("description", ""))
        rows += f"<tr><td>{item.get('rank', '')}</td><td><span class='type-badge'>{kind}</span></td><td>{desc}</td></tr>"
    return f"""
<div class="card">
  <h2>Featured Section Recommendations</h2>
  <p>Pin these items to your Featured section to demonstrate depth beyond the resume.</p>
  <table class="featured-table">
    <thead><tr><th>#</th><th>Type</th><th>Description</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""


def _keywords_section(result: dict) -> str:
    recruiter_kw = result.get("recruiter_keywords", [])
    gaps = result.get("keyword_gaps", [])

    kw_html = " ".join(
        f'<span class="keyword-chip">{html.escape(k)}</span>'
        for k in recruiter_kw
    )
    gap_html = " ".join(
        f'<span class="gap-chip">{html.escape(g)}</span>'
        for g in gaps
    ) if gaps else "<em>None — all key terms already present.</em>"

    return f"""
<div class="card">
  <h2>Recruiter Keywords</h2>
  <p>These are the terms Director/Sr Director recruiters at your Tier 1 targets search for. Your profile should contain all of them.</p>
  <div class="chip-row">{kw_html}</div>
  <h3 style="margin-top:24px">Keyword Gaps (add to profile)</h3>
  <div class="chip-row">{gap_html}</div>
</div>"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _char_bar(count: int, limit: int, target: int) -> str:
    pct = min(count / limit * 100, 100)
    target_pct = target / limit * 100
    color = "#22c55e" if count <= target else ("#f59e0b" if count <= limit else "#ef4444")
    return f"""
<div class="char-bar-wrap">
  <div class="char-bar">
    <div class="char-fill" style="width:{pct:.1f}%;background:{color}"></div>
    <div class="char-target" style="left:{target_pct:.1f}%"></div>
  </div>
  <div class="char-label">{count:,} / {limit:,} chars</div>
</div>"""


# ── Page wrapper ──────────────────────────────────────────────────────────────

def _wrap_page(body: str, today: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LinkedIn Optimization — {html.escape(_get_name())} — {today}</title>
<style>
  :root {{
    --bg: #0f172a;
    --card: #1e293b;
    --border: #334155;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #3b82f6;
    --green: #22c55e;
    --yellow: #f59e0b;
    --red: #ef4444;
    --current-bg: #1a2744;
    --rec-bg: #162032;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: var(--bg); color: var(--text); padding: 24px; font-size: 14px; line-height: 1.6; }}
  .header {{ text-align: center; padding: 32px 0 24px; border-bottom: 1px solid var(--border); margin-bottom: 24px; }}
  .header-title {{ font-size: 26px; font-weight: 700; color: #fff; }}
  .header-sub {{ color: var(--muted); margin-top: 4px; }}
  .strategy-note {{ margin-top: 16px; max-width: 720px; margin-left: auto; margin-right: auto;
                    color: var(--text); font-style: italic; border-left: 3px solid var(--accent);
                    padding-left: 16px; text-align: left; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px;
           padding: 24px; margin-bottom: 20px; }}
  .card h2 {{ font-size: 17px; font-weight: 600; margin-bottom: 16px; color: #fff;
              border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
  .card h3 {{ font-size: 14px; font-weight: 600; color: var(--accent); margin: 20px 0 10px; }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  .col-current {{ background: var(--current-bg); border-radius: 8px; padding: 14px; }}
  .col-recommended {{ background: var(--rec-bg); border-radius: 8px; padding: 14px; }}
  .label {{ font-size: 11px; font-weight: 600; color: var(--muted); text-transform: uppercase;
            letter-spacing: .05em; margin-bottom: 8px; }}
  .current-text {{ color: var(--muted); font-size: 13px; }}
  .copy-block {{ background: #0f172a; border: 1px solid var(--border); border-radius: 6px;
                 padding: 12px; font-size: 13px; color: var(--text); cursor: text;
                 user-select: all; margin-top: 8px; }}
  .preserve-lines {{ white-space: pre-wrap; }}
  .positioning-note {{ font-size: 12px; color: var(--yellow); margin-bottom: 12px;
                       font-style: italic; }}
  .char-bar-wrap {{ margin: 6px 0 10px; }}
  .char-bar {{ position: relative; height: 6px; background: var(--border); border-radius: 3px; }}
  .char-fill {{ height: 100%; border-radius: 3px; transition: width .3s; }}
  .char-target {{ position: absolute; top: -3px; width: 2px; height: 12px; background: var(--yellow); }}
  .char-label {{ font-size: 11px; color: var(--muted); margin-top: 3px; }}
  .checklist-table, .skills-table, .featured-table {{ width: 100%; border-collapse: collapse; }}
  .checklist-table th, .checklist-table td,
  .skills-table th, .skills-table td,
  .featured-table th, .featured-table td {{
    text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border);
    font-size: 13px;
  }}
  .checklist-table th, .skills-table th, .featured-table th {{
    color: var(--muted); font-size: 11px; text-transform: uppercase; font-weight: 600;
  }}
  .priority-cell {{ font-weight: 700; color: var(--accent); text-align: center; width: 32px; }}
  .badge-present {{ background: #14532d; color: var(--green); padding: 2px 8px;
                    border-radius: 10px; font-size: 11px; font-weight: 600; }}
  .badge-missing {{ background: #450a0a; color: var(--red); padding: 2px 8px;
                    border-radius: 10px; font-size: 11px; font-weight: 600; }}
  .badge-top3 {{ background: var(--accent); color: #fff; padding: 1px 6px;
                 border-radius: 8px; font-size: 10px; font-weight: 700; margin-left: 6px; }}
  .type-badge {{ background: #1e3a5f; color: #93c5fd; padding: 2px 8px;
                 border-radius: 10px; font-size: 11px; font-weight: 600; }}
  .chip-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
  .keyword-chip {{ background: #1e3a5f; color: #93c5fd; padding: 4px 12px;
                   border-radius: 12px; font-size: 12px; font-weight: 500; }}
  .gap-chip {{ background: #3b1f00; color: var(--yellow); padding: 4px 12px;
               border-radius: 12px; font-size: 12px; font-weight: 500; }}
  .keep-note {{ color: var(--muted); font-size: 12px; margin-top: 12px; font-style: italic; }}
  @media (max-width: 800px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
{body}
</body>
</html>"""
