"""Build Claude prompts for tailoring JSON and people intel generation.

Prompts are constructed dynamically from config.yaml so they reflect each
user's career history, identity positioning, and defensibility rules.
"""

from pathlib import Path

from pipeline.ingest.jd_parser import ParsedJD
from utils.config import get


def _build_career_history_block() -> str:
    history = get("career_history", [])
    if not history:
        return "CAREER HISTORY: (not configured — fill in config.yaml career_history)"
    lines = ["CAREER HISTORY (canonical, most-recent first):"]
    for role in history:
        company = role.get("company", "")
        title = role.get("title", "")
        period = role.get("period", "")
        lines.append(f"- {period}: {company}, {title}")
    return "\n".join(lines)


def _build_identity_block() -> str:
    identity = get("identity", {})
    primary = identity.get("primary", "")
    secondary = identity.get("secondary", "")
    avoid = identity.get("avoid_leading_with", "")
    if not primary:
        return "IDENTITY: (not configured — fill in config.yaml identity)"
    lines = [
        "IDENTITY — POSITIONING ORDER (apply to every bullet and summary):",
        f"1. PRIMARY: {primary}. Lead with this in every headline, summary, and bullet.",
        f"2. SECONDARY: {secondary}. Supporting frame — never the opening.",
    ]
    if avoid:
        lines.append(
            f"3. BACKGROUND ONLY (never lead with): {avoid}. "
            "If required by JD, bury it — table stakes, not the differentiator."
        )
    return "\n".join(lines)


def _build_achievements_block() -> str:
    achievements = get("key_achievements", [])
    if not achievements:
        return "KEY ACHIEVEMENTS: (not configured — fill in config.yaml key_achievements)"
    lines = ["KEY ACHIEVEMENTS (anchors for summary and bullets):"]
    for a in achievements:
        lines.append(f"- {a}")
    lines.append(
        "\nEach dollar figure must appear AT MOST ONCE across the entire summary field. "
        "Never repeat the same figure."
    )
    return "\n".join(lines)


def _build_defensibility_block() -> str:
    notes = get("defensibility_notes", [])
    if not notes:
        return ""
    lines = ["DEFENSIBILITY RULES — NEVER OVERSTATE:"]
    for note in notes:
        lines.append(f"- {note}")
    return "\n".join(lines)


def _build_experience_schema_block() -> str:
    history = get("career_history", [])
    modifiable = [r for r in history if r.get("modifiable", False)]
    if not modifiable:
        return '  "experience_modifications": []'
    blocks = []
    for role in modifiable:
        role_id = role.get("id", "role")
        company = role.get("company", "")
        max_b = role.get("max_bullets", 3)
        bullets = ", ".join([f'"Bullet {i + 1}"' for i in range(max_b)])
        blocks.append(
            f"""    {{
      "role_identifier": "{role_id}",
      "company": "{company}",
      "replacement_lead_in": "1-2 sentence lead-in.",
      "replacement_bullets": [{bullets}]
    }}"""
        )
    inner = ",\n".join(blocks)
    return f'  "experience_modifications": [\n{inner}\n  ]'


def _build_hard_constraints_block() -> str:
    history = get("career_history", [])
    modifiable = [r for r in history if r.get("modifiable", False)]
    non_modifiable = [r for r in history if not r.get("modifiable", False)]
    lines = ["HARD CONSTRAINTS:"]
    for role in modifiable:
        lines.append(
            f"- {role.get('title', role['id'])} bullets: {role.get('max_bullets', 3)} max"
        )
    if non_modifiable:
        titles = ", ".join(r.get("title", r["id"]) for r in non_modifiable)
        lines.append(f"- Older roles ({titles}) are NOT modified — keep as-is")
    lines.append(f"- Filename is always: {get('person.resume_filename', 'YourName_Resume.docx')}")
    return "\n".join(lines)


def _build_background_summary() -> str:
    achievements = get("key_achievements", [])
    identity = get("identity", {})
    primary = identity.get("primary", "operations leader")
    if achievements:
        return f"{primary} with key programs including: {'; '.join(achievements[:2])}"
    return primary


_TAILORING_PROMPT_TEMPLATE = """
You are building a tailoring JSON for a resume tailoring pipeline.
Read every section carefully before generating anything.

---
CANDIDATE PROFILE
---
Name: {name}
Email: {email}
Phone: {phone}
Location: {location}
LinkedIn: {linkedin}

{career_history_block}

{achievements_block}

{identity_block}

POSITIONING RULES:
- This candidate is targeting {target_levels}. Frame all bullets as someone who leads
  teams and owns programs — not an individual contributor.
- Dollar outcomes go early. Lead the summary with the strongest program-level anchor.
- Each dollar figure must appear at most once across the entire summary field.
- YEARS OF EXPERIENCE: Career started {career_start_year}. That is approximately
  {years_exp} years. Write "{years_exp} years" or "{years_exp}+ years" only.
  Never inflate to match JD language — fabrication is a defensibility violation.

COMMUNICATION RULES — APPLY TO ALL GENERATED CONTENT:
1. No em dashes (—). Use periods, commas, colons, or parentheses instead.
2. No AI-tell phrases: "delve," "landscape," "synergy," "in today's rapidly evolving,"
   "it's important to note," "navigate the complexities of."
3. No paragraphs longer than 3 sentences.
4. Cover letter: {cover_letter_max} words max. Direct. Mirrors JD language.
5. Summary: {summary_max} chars max.
6. Punctuation hygiene: never place two punctuation marks back-to-back.

{defensibility_block}

{hard_constraints_block}

---
JOB DESCRIPTION
---
Company: {company}
Role: {role}
Location: {location_jd}
Req #: {req_number}
Salary: {salary_range}

Full JD text:
{raw_text}

---
APPLICATION CONTEXT
---
{application_context}

---
INSTRUCTIONS
---
Generate a tailoring JSON that follows this exact schema:

{{
  "company": "{company}",
  "role": "{role}",
  "filename": "{resume_filename}",
  "date_generated": "{today}",
  "headline": "TAILORED HEADLINE IN ALL CAPS",
  "summary": "3-4 sentence summary. {summary_max} chars max. Lead with strongest dollar outcome. Each figure once only. Mirror JD language.",
  "competencies": [
    "Row 1 (4 comma-separated competencies)",
    "Row 2 (4 comma-separated competencies)",
    "Row 3 (4 comma-separated competencies)",
    "Row 4 (4 comma-separated competencies)"
  ],
{experience_schema_block},
  "technical_skills": ["Row 1", "Row 2", "Row 3"],
  "cover_letter": "{cover_letter_max} words max. 4 sentences. No em dashes.",
  "notes": "Brief positioning rationale."
}}

Return ONLY the JSON. No explanation text around it.
Save the output file as: tailoring_{slug}.json
""".strip()


_PEOPLE_INTEL_PROMPT_TEMPLATE = """
You are building a people intelligence document for a job application.

---
TARGET ROLE
---
Company: {company}
Role: {role}
Location: {location}
{req_context}

---
CANDIDATE BACKGROUND (for outreach personalization)
---
Name: {name}
LinkedIn: {linkedin}
Education: {education}
Background: {background_summary}

---
INSTRUCTIONS
---
Research {company} and produce a people intelligence markdown with these sections:

## Business Unit & Strategic Intelligence
Brief the candidate so they speak like an operator who has studied the company,
not a candidate who only read the JD.

- **How leadership talks about this function publicly.** Search earnings calls,
  investor day presentations, annual reports (last 12-18 months). Does leadership
  reference this function by name? Is it framed as a strategic priority, turnaround,
  cost center, or growth engine?
- **Is the JD terminology used in the wild?** For each key JD phrase, find one
  real-world usage from an external source. Flag phrases that only live in the JD.
- **Strategic context.** What initiative or pressure is this role sitting inside?
  What does success look like in year one?
- **Analyst and market view.** How do sell-side analysts or trade press describe
  this part of the business?
- **One sharp interview question.** Write one question that signals analyst-level
  fluency — specific enough that only someone who did real research would ask it.

## Target Role Context
- Why the role may be open (new headcount vs. backfill, signals from LinkedIn/news)
- Recent leadership changes, M&A, or restructuring at {company} relevant to this function

## Key Contacts to Pursue (Priority 1: Direct Outreach)
For each contact use this EXACT format (section header = person's full name):

### Full Name
- **Title:** exact title
- **LinkedIn:** linkedin.com/in/handle (best guess if unknown)
- **Why they matter:** 1 sentence

[LinkedIn message as standalone paragraph, 290-300 characters, ending with "{name}"]

## Priority 2: Warm-Path Contacts
Same format. Worth connecting with even if not on the hiring team.

## Outreach Plan
Recommended send times (Wed 8-10am ET is peak). Order of operations.

---
OUTREACH TONE RULES:
- 290-300 characters total (hard limit)
- Casual, peer-to-peer — not sycophantic
- Hook: mutual connection, shared employer, or educational tie
- End every message with: {name}

Save the output file as: {slug}_people_intel.md
""".strip()


def build_tailoring_prompt(
    jd: ParsedJD,
    company: str,
    role: str,
    slug: str,
    today: str,
    application_context: str = "",
) -> str:
    import datetime

    career_start = get("person.career_start_year", 2015)
    current_year = datetime.date.today().year
    years_exp = current_year - career_start

    raw_context = application_context.strip()
    if not raw_context:
        context_block = (
            "No special context. Standard tailoring — lead with primary identity, match JD language."
        )
    elif "OVERRIDE" in raw_context:
        context_block = (
            raw_context
            + "\n\nTAILORING DIRECTIVE — INSIDER OVERRIDE: The override reason above describes "
            "what this role actually entails and why the candidate is applying despite a hard pass. "
            "This insider context is more accurate than the JD text alone. Let it dominate the "
            "framing: headline, summary, and bullets should reflect the broader scope the insider "
            "described, using JD language as a secondary anchor only. This is the single most "
            "important instruction in this prompt."
        )
    else:
        context_block = raw_context

    identity = get("identity", {})
    target_levels = ", ".join(identity.get("target_levels", ["Director", "Senior Director"]))

    return _TAILORING_PROMPT_TEMPLATE.format(
        name=get("person.name", "Your Name"),
        email=get("person.email", "your@email.com"),
        phone=get("person.phone", ""),
        location=get("person.location", ""),
        linkedin=get("person.linkedin", ""),
        career_history_block=_build_career_history_block(),
        achievements_block=_build_achievements_block(),
        identity_block=_build_identity_block(),
        defensibility_block=_build_defensibility_block(),
        hard_constraints_block=_build_hard_constraints_block(),
        experience_schema_block=_build_experience_schema_block(),
        target_levels=target_levels,
        career_start_year=career_start,
        years_exp=years_exp,
        company=company,
        role=role,
        location_jd=jd.location or "Not specified",
        req_number=jd.req_number or "Not found",
        salary_range=jd.salary_range or "Not listed",
        raw_text=jd.raw_text[:8000],
        resume_filename=get("person.resume_filename", "YourName_Resume.docx"),
        summary_max=get("tailoring.summary_max_chars", 500),
        cover_letter_max=get("tailoring.cover_letter_words_max", 150),
        slug=slug,
        today=today,
        application_context=context_block,
    )


def build_people_intel_prompt(jd: ParsedJD, company: str, role: str, slug: str) -> str:
    req_context = f"Req #: {jd.req_number}" if jd.req_number else ""
    return _PEOPLE_INTEL_PROMPT_TEMPLATE.format(
        company=company,
        role=role,
        location=jd.location or "Not specified",
        req_context=req_context,
        name=get("person.name", "Your Name"),
        linkedin=get("person.linkedin", ""),
        education=get("person.education", ""),
        background_summary=_build_background_summary(),
        slug=slug,
    )


def write_prompts(
    output_dir: Path, tailoring_prompt: str, people_intel_prompt: str, slug: str
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "tailoring": output_dir / f"prompt_tailoring_{slug}.txt",
        "people_intel": output_dir / f"prompt_people_intel_{slug}.txt",
    }
    files["tailoring"].write_text(tailoring_prompt, encoding="utf-8")
    files["people_intel"].write_text(people_intel_prompt, encoding="utf-8")
    return files
