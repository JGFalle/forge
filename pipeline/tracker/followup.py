"""Follow-up draft generator, context-aware outreach when the tracker says act.

Reads the tracker entry for a company/role and generates a ready-to-send
follow-up message (email or LinkedIn) using Claude. Surfaces automatically
when compute_next_action returns urgency=overdue|urgent, and is also
triggerable on demand via: python run.py --draft-followup "Company" "Role"
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from pipeline.tracker.tracker import load, _find_entry, compute_next_action
from utils.logging import get_logger

load_dotenv()
logger = get_logger(__name__)

def _build_followup_prompt() -> str:
    from utils.config import get
    name = get("person.name", "")
    email = get("person.email", "")
    phone = get("person.phone", "")
    location = get("person.location", "")
    linkedin = get("person.linkedin", "")

    history = get("career_history", [])
    current = next((r for r in history if r.get("is_current") or r == history[0]), {}) if history else {}
    current_str = ""
    if current:
        current_str = f"Currently: {current.get('title', '')} at {current.get('company', '')}"

    achievements = get("key_achievements", [])
    identity = get("identity", {})
    background_str = identity.get("primary", "operations leader")
    if achievements:
        background_str += f" — {achievements[0]}"

    return f"""You are drafting a follow-up message for a job application.
Return only the draft — no explanation, no preamble, no subject line unless email.

---
CANDIDATE
---
{name} | {email} | {phone} | {location}
LinkedIn: {linkedin}
Background: {background_str}
{current_str}

---
APPLICATION CONTEXT"""


_PROMPT = _build_followup_prompt() + """
---
Company: {company}
Role: {role}
Status: {status}
Days since last touchpoint: {days_silent}
Last event: {last_event}
Known contacts: {contacts}
Recruiter-initiated: {recruiter}
Application notes: {notes}

---
COMMUNICATION RULES
---
- No em dashes (—). Use commas, colons, or periods.
- No AI-tell phrases (delve, synergy, landscape, etc.)
- Peer-to-peer tone. Direct. Not sycophantic.
- If channel is LinkedIn: 290-300 characters max, end with the candidate's name
- If channel is email: 3-4 sentences max. Subject line on first line, then blank line, then body.
- Reference something specific: the role title, the company name, the days since last contact.
- If there is a named contact in the tracker, address them by first name.

---
CHANNEL
---
{channel}

---
TASK
---
Write a follow-up message appropriate for the current application status ({status}) and
the fact that {days_silent} days have passed since the last touchpoint.
If there is a warm contact or recruiter in the tracker, address that person specifically.
If cold, write a general follow-up to the hiring team.
""".strip()


def draft(company: str, role: str) -> dict:
    """
    Generate a follow-up message draft for an application.

    Returns:
      channel     : "linkedin" | "email"
      contact_name - name of the primary contact if known, else ""
      message      : the draft text
      char_count  : len(message) (relevant for LinkedIn)
      action      : what triggered this (from compute_next_action)
      urgency     : "overdue" | "urgent" | "upcoming" | "ok"
    """
    entries = load()
    entry = _find_entry(entries, company, role)
    if not entry:
        return {"error": f"No tracker entry found for {company} / {role}"}

    action = compute_next_action(entry)
    contacts = entry.get("contacts", [])
    today = date.today()

    # Determine channel and primary contact
    channel = "email"
    contact_name = ""
    contact_title = ""

    # Prefer recruiter → hiring manager → warm referral → any contact
    priority = ["recruiter", "hiring_manager", "warm_referral", "intel_contact"]
    primary_contact = None
    for ctype in priority:
        primary_contact = next((c for c in contacts if c.get("type") == ctype), None)
        if primary_contact:
            break

    if primary_contact:
        contact_name = primary_contact.get("name", "")
        contact_title = primary_contact.get("title", "")
        channel = primary_contact.get("channel", "linkedin")
        if channel not in ("linkedin", "email"):
            channel = "email"

    # Build contacts summary for prompt
    contacts_summary = "None logged"
    if contacts:
        parts = []
        for c in contacts[:3]:
            parts.append(f"{c['name']} ({c.get('type','')}, {c.get('channel','')})")
        contacts_summary = "; ".join(parts)

    # Days silent
    last_tp = entry.get("last_touchpoint") or entry.get("date_added", str(today))
    try:
        days_silent = (today - date.fromisoformat(last_tp)).days
    except ValueError:
        days_silent = 0

    # Last event
    history = entry.get("history", [])
    last_event = history[-1].get("event", "unknown") if history else "unknown"

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY not set"}

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = _PROMPT.format(
            company=company,
            role=role,
            status=entry.get("status", ""),
            days_silent=days_silent,
            last_event=last_event,
            contacts=contacts_summary,
            recruiter="Yes" if entry.get("recruiter_initiated") else "No",
            notes=entry.get("notes", "None")[:300],
            channel=f"{channel} message",
        )
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        message = response.content[0].text.strip()
        logger.info(
            "Follow-up draft generated for %s / %s (%s, %d chars)",
            company, role, channel, len(message),
        )
        return {
            "channel": channel,
            "contact_name": contact_name,
            "contact_title": contact_title,
            "message": message,
            "char_count": len(message),
            "action": action.get("text", ""),
            "urgency": action.get("urgency", "ok"),
            "error": "",
        }
    except Exception as exc:
        logger.warning("Follow-up draft failed for %s / %s: %s", company, role, exc)
        return {"error": str(exc)}


def display(result: dict) -> None:
    """Print follow-up draft to terminal."""
    if result.get("error"):
        print(f"\n  Follow-up draft error: {result['error']}\n")
        return

    channel = result["channel"].upper()
    width = 60
    print(f"\n{'='*width}")
    print(f"  FOLLOW-UP DRAFT  [{channel}]")
    if result.get("contact_name"):
        title_str = f" — {result['contact_title']}" if result.get("contact_title") else ""
        print(f"  To: {result['contact_name']}{title_str}")
    if result.get("action"):
        print(f"  Trigger: {result['action']}")
    if result["channel"] == "linkedin":
        valid = "READY" if 290 <= result["char_count"] <= 300 else "CHECK LENGTH"
        print(f"  Characters: {result['char_count']}  [{valid}]")
    print(f"{'='*width}")
    print()
    print(result["message"])
    print()


def save(result: dict, output_dir: Path, slug: str) -> Path | None:
    """Save follow-up draft to the application folder."""
    if result.get("error") or not result.get("message"):
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"followup_draft_{slug}.txt"
    lines = [
        f"FOLLOW-UP DRAFT — {slug}",
        f"Channel: {result['channel']}",
        f"To: {result.get('contact_name', 'Hiring Team')}",
        f"Characters: {result['char_count']}",
        f"Trigger: {result.get('action', '')}",
        "",
        "--- DRAFT ---",
        result["message"],
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
