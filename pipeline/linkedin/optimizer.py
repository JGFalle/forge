"""LinkedIn profile optimizer: generates optimized content for all key sections.

Generates headline, About, experience descriptions, skills, and recruiter keyword
recommendations using Claude Sonnet. Outputs a structured JSON artifact that
the report generator turns into an HTML report.

Run once to establish the optimized baseline. Re-run when career milestones change
(new role, major win, target pivot).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from utils.config import get
from utils.logging import get_logger

load_dotenv(Path(".env"))
logger = get_logger(__name__)

def _build_optimizer_prompt(current_profile_block: str) -> str:
    """Build the LinkedIn optimizer prompt from config."""
    name = get("person.name", "")
    education = get("person.education", "")

    history = get("career_history", [])
    history_lines = "\n".join(
        f"- {r.get('period','')}: {r.get('company','')}, {r.get('title','')}"
        for r in history
    )

    achievements = get("key_achievements", [])
    metrics_lines = "\n".join(f"- {a}" for a in achievements) if achievements else "(add to config.yaml key_achievements)"

    defensibility = get("defensibility_notes", [])
    defensibility_lines = "\n".join(f"- {n}" for n in defensibility) if defensibility else ""

    identity = get("identity", {})
    primary = identity.get("primary", "")
    secondary = identity.get("secondary", "")
    avoid = identity.get("avoid_leading_with", "")
    target_levels = ", ".join(identity.get("target_levels", ["Director", "Senior Director"]))

    tier1 = ", ".join(get("target_companies.tier1", []))
    tier2 = ", ".join(get("target_companies.tier2", []))

    # Build experience schema from career_history
    exp_keys = []
    for role in history[:3]:  # top 3 roles
        role_id = role.get("id", "")
        company = role.get("company", "")
        title = role.get("title", "")
        exp_keys.append(
            f'    "{role_id}": {{'
            f'"recommended": "optimized {company} {title} description, 400-1000 chars", '
            f'"positioning_note": "1 sentence on why framed this way"}}'
        )
    exp_block = "{\n" + ",\n".join(exp_keys) + "\n  }"

    # Checklist from career_history
    checklist_items = [
        '{{"priority": 1, "section": "Headline", "effort": "5 min", "action": "specific change to make"}}',
        '{{"priority": 2, "section": "About", "effort": "15 min", "action": "specific change to make"}}',
    ]
    for i, role in enumerate(history[:3], 3):
        company = role.get("company", "")
        checklist_items.append(
            f'{{"priority": {i}, "section": "Experience - {company}", "effort": "20 min", "action": "specific change to make"}}'
        )
    checklist_items.append('{{"priority": 10, "section": "Skills", "effort": "10 min", "action": "specific change to make"}}')
    checklist_items.append('{{"priority": 11, "section": "Featured", "effort": "30 min", "action": "specific change to make"}}')
    checklist_block = "[\n    " + ",\n    ".join(checklist_items) + "\n  ]"

    return f"""
You are optimizing {name}'s LinkedIn profile for a {target_levels} job search.
Read every section carefully before generating anything.

---
CAREER HISTORY (canonical)
---
{history_lines}
Education: {education}

KEY METRICS (only state what is listed here — never inflate):
{metrics_lines}

---
IDENTITY — LEAD IN THIS ORDER
---
1. PRIMARY: {primary}. Lead with this in the headline, first line of About, and all experience summaries.
2. SECONDARY: {secondary}.
3. BACKGROUND ONLY (never lead with): {avoid}. Credentials exist but this is not the positioning.

TARGET ROLES: {target_levels} owning operations strategy, network optimization, or supply chain transformation.
TIER 1 TARGETS: {tier1 or "(configure in config.yaml target_companies.tier1)"}
TIER 2 TARGETS: {tier2 or "(configure in config.yaml target_companies.tier2)"}

---
COMMUNICATION RULES
---
1. No em dashes (—). Use periods, commas, colons, or parentheses.
2. No AI-tell phrases: "delve," "landscape," "synergy," "in today's rapidly evolving," "it's important to note."
3. No paragraphs longer than 3 sentences.
4. Dollar outcomes go early — first sentence of About should name a number.
5. LinkedIn About: 2,000-2,200 chars (2,600 max). First 3 lines visible before "see more" — make them count.
6. Headline: 120 chars target (220 max). Pack identity signal and 2-3 searchable keywords.

{f"---{chr(10)}DEFENSIBILITY RULES{chr(10)}---{chr(10)}{defensibility_lines}{chr(10)}" if defensibility_lines else ""}
---
CURRENT PROFILE
---
{current_profile_block}

---
LINKEDIN CONSTRAINTS
---
- Headline: 220 chars absolute max. Target 120 for mobile.
- About: 2,600 chars absolute max. First 210 chars show before "see more."
- Experience description: 2,000 chars max per role.
- Skills: LinkedIn shows top 3 prominently; users can endorse up to 50.
- Featured: 3-5 items (articles, posts, links, media).

---
INSTRUCTIONS
---
Generate a complete LinkedIn optimization package. Return ONLY valid JSON matching this schema:

{{
  "headline": "recommended headline, 120 chars or under",
  "about": "recommended About section, 2000-2200 chars",
  "experience": {exp_block},
  "featured_recommendations": [
    {{"rank": 1, "type": "post|article|link|media", "description": "what to create or pin and why"}},
    {{"rank": 2, "type": "post|article|link|media", "description": "..."}}
  ],
  "skills_recommended": [
    "Skill 1 (most important first)",
    "Skill 2",
    "... (15 skills total)"
  ],
  "recruiter_keywords": [
    "Target Title 1",
    "Target Title 2",
    "... (15 terms total — what recruiters at Tier 1 companies actually search)"
  ],
  "keyword_gaps": ["terms in recommended content not present in current profile"],
  "implementation_checklist": {checklist_block},
  "strategic_notes": "2-3 sentences on the overall positioning strategy and what this profile is optimized to do"
}}

Return ONLY the JSON. No explanation text.
""".strip()


def run(export_path: Path | None = None, output_dir: Path | None = None) -> Path:
    """
    Generate LinkedIn profile optimization report.

    Args:
        export_path: Path to LinkedIn data export (.zip or directory). If None,
                     generates purely from canonical career history.
        output_dir:  Where to save the JSON artifact and HTML report.

    Returns:
        Path to the HTML report.
    """
    from pipeline.linkedin.report_generator import generate as generate_report

    if output_dir is None:
        output_dir = Path("outputs/linkedin")
    output_dir.mkdir(parents=True, exist_ok=True)

    profile: dict[str, Any] = {}
    if export_path:
        from pipeline.linkedin.profile_parser import parse_export, summarize
        logger.info("Parsing LinkedIn export: %s", export_path)
        profile = parse_export(export_path)
        logger.info("Profile parsed:\n%s", summarize(profile))

    prompt = _build_prompt(profile)
    logger.info("Calling Claude Sonnet for LinkedIn optimization")
    result = _call_claude(prompt)

    # Save raw JSON artifact
    today = datetime.now().strftime("%Y-%m-%d")
    json_path = output_dir / f"linkedin_optimization_{today}.json"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    logger.info("Optimization JSON saved: %s", json_path)

    # Generate HTML report
    html_path = generate_report(result, profile, output_dir, today)
    logger.info("HTML report saved: %s", html_path)
    return html_path


def _build_prompt(profile: dict[str, Any]) -> str:
    if profile:
        lines = []
        if profile.get("headline"):
            lines.append(f"Current Headline ({len(profile['headline'])} chars):\n{profile['headline']}")
        if profile.get("about"):
            lines.append(f"Current About ({len(profile['about'])} chars):\n{profile['about']}")
        if profile.get("positions"):
            lines.append("Current Experience:")
            for pos in profile["positions"][:6]:
                period = pos["started_on"]
                if pos["finished_on"]:
                    period += f" - {pos['finished_on']}"
                lines.append(f"  {pos['title']} at {pos['company']} ({period})")
                if pos["description"]:
                    desc_preview = pos["description"][:300]
                    lines.append(f"    Description ({len(pos['description'])} chars): {desc_preview}{'...' if len(pos['description']) > 300 else ''}")
        if profile.get("skills"):
            lines.append(f"Current Skills ({len(profile['skills'])} total): {', '.join(profile['skills'][:20])}")
        current_block = "\n\n".join(lines)
    else:
        name = get("person.name", "")
        current_block = (
            f"No current profile provided. Generate optimized content purely based on "
            f"{name}'s career history above. No current vs. recommended comparison needed "
            f"— just produce the best possible profile."
        )

    return _build_optimizer_prompt(current_block)


def _call_claude(prompt: str) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()

    data = json.loads(raw)
    return _strip_em_dashes(data)


def _strip_em_dashes(obj):
    """Recursively replace em dashes in all string values."""
    if isinstance(obj, str):
        return obj.replace("—", ",").replace(" – ", ", ").replace("–", "-")
    if isinstance(obj, dict):
        return {k: _strip_em_dashes(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_em_dashes(i) for i in obj]
    return obj
