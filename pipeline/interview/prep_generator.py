"""Interview prep generator — produces a structured pre-interview briefing via Claude API.

Takes the tailoring JSON + people intel markdown (if available) and generates
a complete interview brief: company intelligence, role alignment, STAR story bank,
positioning reminders, questions to ask, and tough question prep.
Output is rendered as a self-contained HTML file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(Path(".env"))

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def _build_prep_background() -> str:
    from utils.config import get
    name = get("person.name", "")
    email = get("person.email", "")
    location = get("person.location", "")
    linkedin = get("person.linkedin", "")
    education = get("person.education", "")

    history = get("career_history", [])
    history_lines = "\n".join(
        f"- {r.get('period','')}: {r.get('company','')}, {r.get('title','')}"
        for r in history
    )

    achievements = get("key_achievements", [])
    achiev_lines = "\n".join(f"- {a}" for a in achievements) if achievements else "(not configured)"

    identity = get("identity", {})
    primary = identity.get("primary", "")
    secondary = identity.get("secondary", "")
    avoid = identity.get("avoid_leading_with", "")

    return f"""{name} | {location} | {email} | {linkedin}
{education}

CAREER HISTORY:
{history_lines}

KEY ACHIEVEMENTS (use these for STAR stories — do not overstate):
{achiev_lines}

IDENTITY (lead in this order):
1. {primary}
2. {secondary}
3. {avoid} — background only, never the lead"""


_PREP_PROMPT_TEMPLATE = """
You are building a pre-interview briefing for a job candidate.
Return a single JSON object — no explanation text, only the JSON.

---
CANDIDATE BACKGROUND
---
{candidate_background}

COMMUNICATION RULES:
- No em dashes. Use commas, colons, or periods.
- No AI-tell phrases: delve, landscape, synergy, "in today's rapidly evolving."
- Direct, confident, peer-to-peer tone.

---
APPLICATION CONTEXT
---
{application_context}

---
TAILORING JSON (how this role was positioned)
---
{tailoring_json}

---
PEOPLE INTEL / COMPANY RESEARCH
---
{people_intel}

---
JOB DESCRIPTION
---
Company: {company}
Role: {role}
{jd_text}

---
INSTRUCTIONS
---
Generate a pre-interview briefing with these sections. Be specific — not generic platitudes.
Reference the actual company, role, and the candidate's real achievements. Think like a trusted advisor
who has studied this company deeply and knows the candidate's background cold.

Return this exact JSON structure:

{{
  "company_brief": {{
    "strategic_context": "2-3 sentences: what initiative or pressure is this role sitting inside? What does the company need right now that this role addresses?",
    "leadership_language": ["exact phrase from earnings/investor materials", "another phrase", "a third"],
    "success_year_one": "What does winning look like in the first 12 months? Be concrete — deliverables, relationships, metrics.",
    "watch_out": "One honest observation about a cultural or political dynamic to be aware of going in."
  }},
  "role_alignment": {{
    "what_theyre_really_hiring_for": "Beyond the JD — what problem are they solving by making this hire? What failure mode are they avoiding?",
    "top_3_eval_criteria": [
      "The most important thing they will evaluate — named specifically",
      "Second most important",
      "Third"
    ],
    "identity_bridge": "How the candidate's primary identity connects to this role's requirements. Be honest about gaps."
  }},
  "story_bank": [
    {{
      "title": "Short name for this story",
      "situation": "1 sentence context",
      "action": "1-2 sentences: what was done specifically",
      "result": "Quantified outcome",
      "use_when": "The type of question or competency this answers",
      "jd_match": "The specific JD requirement or competency this addresses"
    }}
  ],
  "positioning_reminders": [
    "Specific reminder about how to frame identity for THIS role",
    "How to handle the biggest gap or stretch in this application",
    "One thing to never say in this interview"
  ],
  "questions_to_ask": [
    {{
      "question": "The exact question to ask",
      "why": "Why this question signals deep research — what it shows you understand"
    }}
  ],
  "tough_questions": [
    {{
      "question": "The hard question they will likely ask",
      "suggested_framing": "How to answer — specific, not generic. Name the actual framing."
    }}
  ]
}}

Rules:
- story_bank: exactly 4 stories, each mapped to a distinct JD requirement
- questions_to_ask: exactly 4 questions — at least one must reference specific public company language or data
- tough_questions: exactly 3 — the 3 most likely curveballs for THIS specific role and the candidate's background
- positioning_reminders: exactly 3
- leadership_language: exactly 3 phrases with source context if possible (e.g., "James Quincey, Q1 2026 earnings")
""".strip()


def generate(
    company: str,
    role: str,
    tailoring_json_path: Path | None = None,
    people_intel_path: Path | None = None,
    jd_text: str = "",
    application_context: str = "",
) -> dict:
    """Call Claude and return the structured prep JSON."""
    tailoring_json = ""
    if tailoring_json_path and tailoring_json_path.exists():
        tailoring_json = tailoring_json_path.read_text(encoding="utf-8")

    people_intel = ""
    if people_intel_path and people_intel_path.exists():
        people_intel = people_intel_path.read_text(encoding="utf-8")[:4000]
    else:
        people_intel = "No people intel document available yet. Use your knowledge of this company."

    prompt = _PREP_PROMPT_TEMPLATE.format(
        candidate_background=_build_prep_background(),
        company=company,
        role=role,
        application_context=application_context or "Standard application — no special context.",
        tailoring_json=tailoring_json[:3000] if tailoring_json else "Not yet generated.",
        people_intel=people_intel,
        jd_text=jd_text[:2000] if jd_text else "",
    )

    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def render_html(prep: dict, company: str, role: str, output_path: Path) -> Path:
    """Render the prep JSON to a self-contained HTML file."""
    from datetime import date
    today = date.today().strftime("%B %d, %Y")

    def esc(s: str | None) -> str:
        if not s:
            return ""
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    cb = prep.get("company_brief", {})
    ra = prep.get("role_alignment", {})
    stories = prep.get("story_bank", [])
    reminders = prep.get("positioning_reminders", [])
    questions = prep.get("questions_to_ask", [])
    tough = prep.get("tough_questions", [])
    lang = cb.get("leadership_language", [])

    # Story cards
    story_html = ""
    story_colors = ["#1B3A6B", "#16a34a", "#d97706", "#7e22ce"]
    for i, s in enumerate(stories):
        color = story_colors[i % len(story_colors)]
        story_html += f"""
        <div class="story-card" style="border-left: 4px solid {color}">
          <div class="story-title">{esc(s.get('title',''))}</div>
          <div class="story-tag">Use when: {esc(s.get('use_when',''))}</div>
          <div class="story-tag jd-match">JD match: {esc(s.get('jd_match',''))}</div>
          <div class="story-body">
            <p><strong>Situation:</strong> {esc(s.get('situation',''))}</p>
            <p><strong>Action:</strong> {esc(s.get('action',''))}</p>
            <p><strong>Result:</strong> {esc(s.get('result',''))}</p>
          </div>
        </div>"""

    # Questions
    q_html = ""
    for q in questions:
        q_html += f"""
        <div class="q-card">
          <div class="q-text">"{esc(q.get('question',''))}"</div>
          <div class="q-why">{esc(q.get('why',''))}</div>
        </div>"""

    # Tough questions
    tq_html = ""
    for tq in tough:
        tq_html += f"""
        <div class="tq-card">
          <div class="tq-question">Q: {esc(tq.get('question',''))}</div>
          <div class="tq-answer">{esc(tq.get('suggested_framing',''))}</div>
        </div>"""

    # Language badges
    lang_html = "".join(f'<span class="lang-badge">{esc(l)}</span>' for l in lang)

    # Reminders
    rem_html = "".join(f'<li>{esc(r)}</li>' for r in reminders)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Interview Prep — {esc(company)}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --navy: #1B3A6B; --green: #16a34a; --amber: #d97706;
    --bg: #f1f5f9; --card: #fff; --border: #e2e8f0;
    --text: #1e293b; --muted: #64748b;
  }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.6; }}
  header {{ background: var(--navy); color: white; padding: 28px 40px; }}
  header h1 {{ font-size: 20px; font-weight: 700; }}
  header .sub {{ font-size: 13px; opacity: 0.75; margin-top: 4px; }}
  main {{ max-width: 960px; margin: 0 auto; padding: 28px 20px; display: flex; flex-direction: column; gap: 24px; }}
  .section {{ background: var(--card); border-radius: 10px; border: 1px solid var(--border);
    overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
  .section-header {{ background: var(--navy); color: white; padding: 12px 20px;
    font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px; }}
  .section-body {{ padding: 20px; }}
  .section-body p {{ margin-bottom: 10px; }}
  .section-body p:last-child {{ margin-bottom: 0; }}
  .label {{ font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.6px; color: var(--muted); margin-bottom: 6px; }}
  .block {{ margin-bottom: 16px; }}
  .block:last-child {{ margin-bottom: 0; }}
  .lang-badge {{ display: inline-block; background: #e0f2fe; color: #0369a1;
    border-radius: 20px; padding: 3px 10px; font-size: 12px; font-weight: 600;
    margin: 2px 4px 2px 0; }}
  ul.criteria {{ padding-left: 20px; }}
  ul.criteria li {{ margin-bottom: 6px; font-size: 13px; }}
  .story-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  @media(max-width: 640px) {{ .story-grid {{ grid-template-columns: 1fr; }} }}
  .story-card {{ background: var(--bg); border-radius: 8px; padding: 14px 16px;
    border-left: 4px solid var(--navy); }}
  .story-title {{ font-weight: 700; font-size: 14px; margin-bottom: 4px; }}
  .story-tag {{ font-size: 11px; color: var(--muted); margin-bottom: 2px; }}
  .story-tag.jd-match {{ color: var(--green); font-weight: 600; margin-bottom: 8px; }}
  .story-body p {{ font-size: 13px; margin-bottom: 4px; }}
  .reminder-list {{ list-style: none; display: flex; flex-direction: column; gap: 10px; }}
  .reminder-list li {{ padding: 10px 14px; background: #fffbeb; border-left: 3px solid var(--amber);
    border-radius: 0 6px 6px 0; font-size: 13px; }}
  .q-card {{ background: var(--bg); border-radius: 8px; padding: 14px 16px; margin-bottom: 10px; }}
  .q-card:last-child {{ margin-bottom: 0; }}
  .q-text {{ font-size: 14px; font-weight: 600; color: var(--navy); margin-bottom: 6px; }}
  .q-why {{ font-size: 12px; color: var(--muted); }}
  .tq-card {{ border: 1px solid var(--border); border-radius: 8px; margin-bottom: 12px; overflow: hidden; }}
  .tq-card:last-child {{ margin-bottom: 0; }}
  .tq-question {{ background: #fef2f2; padding: 10px 16px; font-weight: 600;
    font-size: 13px; color: #991b1b; border-bottom: 1px solid var(--border); }}
  .tq-answer {{ padding: 12px 16px; font-size: 13px; }}
  footer {{ text-align: center; padding: 20px; font-size: 11px; color: var(--muted); }}
</style>
</head>
<body>
<header>
  <h1>Interview Prep: {esc(company)}</h1>
  <div class="sub">{esc(role)} &nbsp;·&nbsp; Generated {today}</div>
</header>
<main>

  <div class="section">
    <div class="section-header">Company Intelligence Brief</div>
    <div class="section-body">
      <div class="block">
        <div class="label">Strategic Context</div>
        <p>{esc(cb.get('strategic_context',''))}</p>
      </div>
      <div class="block">
        <div class="label">Leadership Language — Use These Phrases</div>
        <div>{lang_html}</div>
      </div>
      <div class="block">
        <div class="label">What Winning Looks Like in Year One</div>
        <p>{esc(cb.get('success_year_one',''))}</p>
      </div>
      <div class="block">
        <div class="label">Watch Out</div>
        <p style="color:#92400e; background:#fffbeb; padding:10px 14px; border-radius:6px; border-left:3px solid #d97706">
          {esc(cb.get('watch_out',''))}
        </p>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-header">Role Alignment</div>
    <div class="section-body">
      <div class="block">
        <div class="label">What They're Really Hiring For</div>
        <p>{esc(ra.get('what_theyre_really_hiring_for',''))}</p>
      </div>
      <div class="block">
        <div class="label">Top 3 Eval Criteria</div>
        <ul class="criteria">
          {"".join(f"<li>{esc(c)}</li>" for c in ra.get('top_3_eval_criteria', []))}
        </ul>
      </div>
      <div class="block">
        <div class="label">Identity Bridge</div>
        <p>{esc(ra.get('identity_bridge',''))}</p>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-header">Story Bank (STAR)</div>
    <div class="section-body">
      <div class="story-grid">
        {story_html}
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-header">Positioning Reminders</div>
    <div class="section-body">
      <ul class="reminder-list">
        {rem_html}
      </ul>
    </div>
  </div>

  <div class="section">
    <div class="section-header">Questions to Ask</div>
    <div class="section-body">
      {q_html}
    </div>
  </div>

  <div class="section">
    <div class="section-header">Tough Questions — Be Ready</div>
    <div class="section-body">
      {tq_html}
    </div>
  </div>

</main>
<footer>PACE · Interview Prep · {esc(company)} · {today}</footer>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    return output_path


def run(
    company: str,
    role: str,
    app_folder: Path | None = None,
    output_dir: Path | None = None,
    jd_text: str = "",
    application_context: str = "",
) -> Path:
    """Generate and render the interview prep brief. Returns path to HTML file."""
    from utils.text import clean_text
    import re

    # Try to find tailoring JSON and people intel in the app folder
    tailoring_json_path = None
    people_intel_path = None

    if app_folder and app_folder.exists():
        slug_part = re.sub(r"[^a-z0-9]+", "_", f"{company}_{role}".lower()).strip("_")[:40]
        # Search for tailoring JSON
        for f in app_folder.rglob("tailoring_*.json"):
            tailoring_json_path = f
            break
        # Search for people intel markdown
        for f in app_folder.rglob("*people_intel*.md"):
            people_intel_path = f
            break

    prep_data = generate(
        company=company,
        role=role,
        tailoring_json_path=tailoring_json_path,
        people_intel_path=people_intel_path,
        jd_text=jd_text,
        application_context=application_context,
    )

    out_dir = output_dir or (app_folder / "interview_prep" if app_folder else Path("outputs/interview_prep"))
    out_dir.mkdir(parents=True, exist_ok=True)

    slug = re.sub(r"[^a-z0-9]+", "_", f"{company}".lower()).strip("_")[:30]
    out_path = out_dir / f"interview_prep_{slug}.html"

    return render_html(prep_data, company, role, out_path)
