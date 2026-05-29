"""Salary negotiation brief: triggered when an application reaches offer status.

Uses Perplexity to pull live comp data, then Claude to build a negotiation
playbook: upside from the offer, non-base items to push on, BATNA arguments,
and exact talking points. Output is a self-contained HTML file.

Trigger:  python run.py --negotiate "Company" "Role" [--offer-amount "$185K"]
Auto-tip: displayed in terminal when status is updated to "offer"
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from pipeline.tracker.tracker import load, _find_entry
from utils.logging import get_logger

load_dotenv()
logger = get_logger(__name__)

def _build_negotiation_prompt() -> str:
    from utils.config import get
    name = get("person.name", "")

    history = get("career_history", [])
    current = history[0] if history else {}
    current_str = ""
    if current:
        current_str = f"Current: {current.get('title', '')}, {current.get('company', '')}"

    achievements = get("key_achievements", [])
    identity = get("identity", {})
    background = identity.get("primary", "operations leader")
    if achievements:
        background += "; " + "; ".join(achievements[:2])

    comp = get("comp_floors", {})
    target_floor = comp.get("target_floor", 175000)
    sr_target = comp.get("sr_target", 220000)
    dr_a = comp.get("day_rate_analysis", 1500)
    dr_i = comp.get("day_rate_implementation", 1800)
    levels_str = ", ".join(get("identity.target_levels", ["Director", "Senior Director"]))

    return f"""You are building a salary negotiation brief for {name}.
Return only JSON — no explanation text.

---
CANDIDATE PROFILE
---
{name} | {levels_str} level target
{current_str}
Background: {background}

Comp floors (never go below):
- Target level: ${target_floor:,} base floor
- Sr level target: ${sr_target:,}+
- Day rate (analysis): ${dr_a:,} | Day rate (implementation): ${dr_i:,}

---
APPLICATION"""


_PROMPT = _build_negotiation_prompt() + """
---
Company: {company}
Role: {role}
Offer amount (if known): {offer_amount}
Market data: {market_data}
Application notes: {notes}
Active competing applications: {competing}

---
INSTRUCTIONS
---
Build a negotiation brief with this exact JSON structure:

{{
  "offer_assessment": {{
    "offer_vs_floor": "Is this offer above, at, or below the candidate's floor? By how much?",
    "offer_vs_market": "How does it compare to the market data? What percentile?",
    "headline_verdict": "One sentence: accept, negotiate, or walk — and why."
  }},
  "upside_items": [
    {{
      "item": "Name of negotiable item (base, sign-on, bonus target, title, first review)",
      "current": "What was offered or implied",
      "ask": "What to request",
      "rationale": "One sentence: why this ask is justified"
    }}
  ],
  "batna_arguments": [
    "Specific argument to make — name the competing activity or market data",
    "Second argument",
    "Third argument (if applicable)"
  ],
  "talking_points": [
    {{
      "moment": "When to say this (e.g., 'when they ask for your number', 'if they push back on base')",
      "script": "Exact language to use — natural, not robotic"
    }}
  ],
  "watch_outs": [
    "One thing that could go wrong in this negotiation",
    "Second risk"
  ],
  "walk_away_number": "The minimum total package (base + target bonus) to decline below"
}}

Rules:
- upside_items: 3-5 items, ordered by impact
- batna_arguments: 2-3 items — must be specific to the candidate's situation, not generic
- talking_points: 3-4 scripts — write them in first person
- watch_outs: exactly 2
- Be direct and specific. Numbers over platitudes.
""".strip()


def run(company: str, role: str, offer_amount: str = "") -> Path:
    """Generate negotiation brief and save as HTML. Returns path."""
    entries = load()
    entry = _find_entry(entries, company, role)
    notes = entry.get("notes", "") if entry else ""
    salary_range = entry.get("salary_range", "") if entry else ""

    # If offer_amount not passed, use tracker salary_range
    if not offer_amount and salary_range:
        offer_amount = salary_range

    # Active competing applications (for BATNA)
    from pipeline.tracker.tracker import CLOSED_STATUSES
    all_entries = load()
    competing = [
        f"{e['company']} ({e['status']})"
        for e in all_entries
        if e.get("status") not in CLOSED_STATUSES
        and not (e["company"].lower() == company.lower() and e["role"].lower() == role.lower())
    ]
    competing_str = ", ".join(competing[:5]) if competing else "None active"

    # Perplexity market data
    market_data = _fetch_market_data(company, role)

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    prompt = _PROMPT.format(
        company=company,
        role=role,
        offer_amount=offer_amount or "Not yet disclosed",
        market_data=market_data or "No live market data available — use general Director-level benchmarks.",
        notes=notes[:400] if notes else "None",
        competing=competing_str,
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    brief = json.loads(raw.strip())
    logger.info("Negotiation brief generated for %s / %s", company, role)

    out_dir = Path("outputs/negotiation")
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", f"{company}_{role}".lower()).strip("_")[:50]
    out_path = out_dir / f"negotiation_{slug}.html"
    _render_html(brief, company, role, offer_amount, out_path)
    return out_path


def _fetch_market_data(company: str, role: str) -> str:
    """Pull comp data from Perplexity - returns empty string gracefully."""
    api_key = os.getenv("PERPLEXITY_API_KEY", "")
    if not api_key:
        return ""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")
        query = (
            f"What is the total compensation range (base + target bonus) for a {role} at {company}? "
            f"Include data from Levels.fyi, LinkedIn Salary, Glassdoor, and any state pay transparency "
            f"disclosures. What is the typical negotiation upside from initial offer at this company? "
            f"Keep the response under 200 words."
        )
        response = client.chat.completions.create(
            model="sonar-pro",
            messages=[{"role": "user", "content": query}],
            max_tokens=400,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        logger.warning("Perplexity market data fetch failed: %s", exc)
        return ""


def _render_html(brief: dict, company: str, role: str, offer_amount: str, out_path: Path) -> None:
    today = date.today().strftime("%B %d, %Y")

    def esc(s) -> str:
        if not s:
            return ""
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    oa = brief.get("offer_assessment", {})
    upside = brief.get("upside_items", [])
    batna = brief.get("batna_arguments", [])
    talking = brief.get("talking_points", [])
    watch = brief.get("watch_outs", [])
    walk_away = brief.get("walk_away_number", "")

    upside_rows = "".join(
        f"<tr><td><strong>{esc(u.get('item',''))}</strong></td>"
        f"<td>{esc(u.get('current',''))}</td>"
        f"<td style='color:#16a34a;font-weight:600'>{esc(u.get('ask',''))}</td>"
        f"<td>{esc(u.get('rationale',''))}</td></tr>"
        for u in upside
    )

    batna_items = "".join(f"<li>{esc(b)}</li>" for b in batna)
    watch_items = "".join(f"<li>{esc(w)}</li>" for w in watch)

    talking_cards = "".join(
        f"<div class='talk-card'>"
        f"<div class='talk-moment'>{esc(t.get('moment',''))}</div>"
        f"<div class='talk-script'>\"{esc(t.get('script',''))}\"</div>"
        f"</div>"
        for t in talking
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Negotiation Brief — {esc(company)}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f1f5f9; color: #1e293b; font-size: 14px; line-height: 1.6; }}
  header {{ background: #1B3A6B; color: white; padding: 28px 40px; }}
  header h1 {{ font-size: 20px; font-weight: 700; }}
  header .sub {{ font-size: 13px; opacity: 0.75; margin-top: 4px; }}
  main {{ max-width: 900px; margin: 0 auto; padding: 28px 20px; display: flex; flex-direction: column; gap: 20px; }}
  .card {{ background: white; border-radius: 10px; border: 1px solid #e2e8f0; overflow: hidden; }}
  .card-header {{ background: #1B3A6B; color: white; padding: 10px 20px;
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; }}
  .card-body {{ padding: 20px; }}
  .verdict {{ font-size: 16px; font-weight: 700; color: #1B3A6B; margin-bottom: 12px; }}
  .meta-row {{ font-size: 13px; margin-bottom: 6px; }}
  .meta-row strong {{ color: #475569; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #f8fafc; padding: 8px 12px; text-align: left; font-size: 11px;
    text-transform: uppercase; letter-spacing: .05em; color: #64748b;
    border-bottom: 1px solid #e2e8f0; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
  ul {{ padding-left: 20px; }}
  ul li {{ margin-bottom: 8px; font-size: 13px; }}
  .talk-card {{ background: #f8fafc; border-radius: 8px; padding: 14px 16px; margin-bottom: 10px; }}
  .talk-moment {{ font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .06em; color: #64748b; margin-bottom: 6px; }}
  .talk-script {{ font-size: 14px; color: #1B3A6B; font-style: italic; }}
  .walk-away {{ background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px;
    padding: 16px 20px; font-size: 15px; font-weight: 700; color: #991b1b; }}
  footer {{ text-align: center; padding: 20px; font-size: 11px; color: #94a3b8; }}
</style>
</head>
<body>
<header>
  <h1>Negotiation Brief: {esc(company)}</h1>
  <div class="sub">{esc(role)} &nbsp;·&nbsp; Offer: {esc(offer_amount) or 'TBD'} &nbsp;·&nbsp; {today}</div>
</header>
<main>

  <div class="card">
    <div class="card-header">Offer Assessment</div>
    <div class="card-body">
      <div class="verdict">{esc(oa.get('headline_verdict',''))}</div>
      <div class="meta-row"><strong>vs. floor:</strong> {esc(oa.get('offer_vs_floor',''))}</div>
      <div class="meta-row"><strong>vs. market:</strong> {esc(oa.get('offer_vs_market',''))}</div>
    </div>
  </div>

  <div class="card">
    <div class="card-header">Negotiation Upside — What to Ask For</div>
    <div class="card-body" style="padding:0">
      <table>
        <tr><th>Item</th><th>Offered</th><th>Ask</th><th>Rationale</th></tr>
        {upside_rows}
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-header">BATNA Arguments</div>
    <div class="card-body"><ul>{batna_items}</ul></div>
  </div>

  <div class="card">
    <div class="card-header">Talking Points — Exact Language</div>
    <div class="card-body">{talking_cards}</div>
  </div>

  <div class="card">
    <div class="card-header">Watch Outs</div>
    <div class="card-body"><ul>{watch_items}</ul></div>
  </div>

  <div class="walk-away">
    Walk-away number: {esc(walk_away)}
  </div>

</main>
<footer>FORGE · Negotiation Brief · {esc(company)} · {today}</footer>
</body>
</html>"""

    out_path.write_text(html, encoding="utf-8")
    logger.info("Negotiation brief saved: %s", out_path)
