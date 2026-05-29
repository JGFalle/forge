"""Fit assessment engine, runs before any materials are generated.

Calls Claude to score a JD against the user's vision profile across four dimensions,
returns a structured verdict (STRONG_FIT / STRETCH / HARD_PASS), and surfaces
gut-check questions when the fit is ambiguous. Answers to those questions flow
into every downstream artifact automatically.
"""

import json
import os
from dataclasses import dataclass, field

import anthropic
from dotenv import load_dotenv

from pipeline.ingest.jd_parser import ParsedJD
from utils.config import get

load_dotenv()

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


@dataclass
class DimensionScore:
    score: int          # 1-10
    rationale: str
    flags: list[str] = field(default_factory=list)


@dataclass
class FitAssessment:
    verdict: str                        # STRONG_FIT | STRETCH | HARD_PASS
    overall_score: int                  # 1-10
    identity_alignment: DimensionScore
    scope_level: DimensionScore
    comp_alignment: DimensionScore
    company_tier: DimensionScore
    hard_filter_triggered: bool
    hard_filter_reason: str | None
    gut_check_questions: list[str]      # populated if STRETCH
    summary: str                        # 2-3 sentence plain-English verdict
    gut_check_answers: str = ""         # filled in after user responds


def _build_vision_profile() -> str:
    identity = get("identity", {})
    primary = identity.get("primary", "operations leader")
    secondary = identity.get("secondary", "")
    avoid = identity.get("avoid_leading_with", "")
    target_levels = identity.get("target_levels", ["Director", "Senior Director"])

    achievements = get("key_achievements", [])
    achiev_block = ""
    if achievements:
        achiev_block = "\nKey proof points:\n" + "\n".join(f"- {a}" for a in achievements)

    comp = get("comp_floors", {})
    target_floor = comp.get("target_floor", 175000)
    hard_filter_floor = comp.get("hard_filter_floor", 140000)

    tier1 = get("target_companies.tier1", [])
    tier2 = get("target_companies.tier2", [])
    tier3 = get("target_companies.tier3", [])

    tier1_str = ", ".join(tier1) if tier1 else "Tier 1 operators (not configured)"
    tier2_str = ", ".join(tier2) if tier2 else "Tier 2 tech-adjacent (not configured)"
    tier3_str = ", ".join(tier3) if tier3 else "Tier 3 consulting bridge (not configured)"

    levels_str = ", ".join(target_levels)

    avoid_block = ""
    if avoid:
        avoid_block = (
            f"\n3. BACKGROUND ONLY (never the lead identity): {avoid}. "
            "Credentials exist but this is not the differentiator."
        )

    return f"""---
CANDIDATE VISION PROFILE
---
Identity order (lead with 1, never lead with 3):
1. PRIMARY: {primary}{achiev_block}
2. SECONDARY: {secondary}{"" if secondary else "(not configured)"}{avoid_block}

HOW TO READ THE VISION PROFILE:
The vision represents the aspirational top bracket. It is NOT a binary pass/fail.
A role below the ideal is still potentially excellent by any market standard.
HARD_PASS is reserved for categorically disqualifying roles — not for roles that
simply fall short of the ideal. When in doubt, default to STRETCH over HARD_PASS.

True hard filters — auto HARD_PASS only if:
- Role is purely {avoid or "misaligned with primary identity"} with zero technical component
- Individual contributor seat with no team ownership whatsoever
- Base comp clearly below ${hard_filter_floor:,} where explicitly stated
- Completely wrong domain or function (use the extreme outlier test)

Lower-priority signals (score down, do NOT hard pass):
- PE-backed mid-market consulting: legitimate bridge, score company_tier 4-5/10
- No AI/ML or primary identity component: lower identity_alignment, but assess on merits
- Comp between ${hard_filter_floor:,}-${target_floor:,}: flag it, assess total comp potential

Target levels: {levels_str}. Must have meaningful team ownership.

Comp: ${target_floor:,} base is the aspirational floor.
Hard filter triggers only below ${hard_filter_floor:,} explicitly stated.
Between ${hard_filter_floor:,}-${target_floor:,}: flag it, do not hard-pass.

Company tier scoring:
- Tier 1 target operators (score 9-10): {tier1_str}
- Tier 2 tech/automation adjacent (score 7-8): {tier2_str}
- Tier 3 consulting bridge (score 5-6): {tier3_str}
- PE-backed mid-market (score 4-5): legitimate but not ideal path
- Other large enterprise (score 5-7): assess on company size, brand, function scope
- Truly wrong fit (score 1-3): small company, irrelevant industry"""


_ASSESSMENT_PROMPT = """
You are evaluating a job posting against a defined search vision.
Return a JSON assessment — no explanation text, just the JSON.

{vision_profile}

---
JOB DESCRIPTION
---
Company: {company}
Role: {role}
Location: {location}
Req #: {req_number}
Salary: {salary}

Full JD:
{raw_text}

---
SCORING INSTRUCTIONS
---
Score each dimension 1-10. Be direct and specific — not generic.

identity_alignment: Does this role let the candidate lead with their primary identity?
  Or does it push them toward the identity they want to avoid? Score 7+ if primary
  identity is present. Score 4-6 if adjacent. Score 1-3 only if it's the anti-identity.

scope_level: Is this genuinely at the target level with real team ownership and program
  scope? Or is it a lower-level role with an inflated title?

comp_alignment: Does stated comp (or likely comp for the level/company if not listed)
  meet the target floor?

company_tier: Score using the tier guidance above.

Overall verdict logic:
- STRONG_FIT: All dimensions 7+, no hard filters, identity alignment is clean
- STRETCH: One or more dimensions 4-6, or identity requires reframing, but a legitimate
  reason to apply exists.
- HARD_PASS: Only when a true hard filter applies. NOT for roles simply below the ideal.

gut_check_questions: If STRETCH, write 2-3 specific questions the candidate must answer
  before proceeding. These should name the specific gap and force a real answer.
  If STRONG_FIT or HARD_PASS, return empty array.

Return this exact JSON structure:
{{
  "verdict": "STRONG_FIT|STRETCH|HARD_PASS",
  "overall_score": 0,
  "summary": "2-3 sentence plain-English verdict. Be direct.",
  "hard_filter_triggered": false,
  "hard_filter_reason": null,
  "identity_alignment": {{"score": 0, "rationale": "", "flags": []}},
  "scope_level": {{"score": 0, "rationale": "", "flags": []}},
  "comp_alignment": {{"score": 0, "rationale": "", "flags": []}},
  "company_tier": {{"score": 0, "rationale": "", "flags": []}},
  "gut_check_questions": []
}}
""".strip()


def assess(jd: ParsedJD, company: str, role: str) -> FitAssessment:
    prompt = _ASSESSMENT_PROMPT.format(
        vision_profile=_build_vision_profile(),
        company=company,
        role=role,
        location=jd.location or "Not specified",
        req_number=jd.req_number or "Not listed",
        salary=jd.salary_range or "Not listed",
        raw_text=jd.raw_text[:6000],
    )

    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw.strip())

    return FitAssessment(
        verdict=data["verdict"],
        overall_score=data["overall_score"],
        summary=data["summary"],
        hard_filter_triggered=data["hard_filter_triggered"],
        hard_filter_reason=data.get("hard_filter_reason"),
        identity_alignment=DimensionScore(**data["identity_alignment"]),
        scope_level=DimensionScore(**data["scope_level"]),
        comp_alignment=DimensionScore(**data["comp_alignment"]),
        company_tier=DimensionScore(**data["company_tier"]),
        gut_check_questions=data.get("gut_check_questions", []),
    )


def display(assessment: FitAssessment) -> None:
    verdict_label = {
        "STRONG_FIT": "STRONG FIT",
        "STRETCH": "STRETCH — gut check required",
        "HARD_PASS": "HARD PASS",
    }.get(assessment.verdict, assessment.verdict)

    print(f"\n{'='*60}")
    print(f"  FIT ASSESSMENT: {verdict_label}  ({assessment.overall_score}/10)")
    print(f"{'='*60}")
    print(f"\n{assessment.summary}\n")

    if assessment.hard_filter_triggered:
        print(f"HARD FILTER: {assessment.hard_filter_reason}\n")

    dims = [
        ("Identity Alignment", assessment.identity_alignment),
        ("Scope / Level",      assessment.scope_level),
        ("Comp Alignment",     assessment.comp_alignment),
        ("Company Tier",       assessment.company_tier),
    ]
    for label, dim in dims:
        flags = f"  [{', '.join(dim.flags)}]" if dim.flags else ""
        print(f"  {label:22s} {dim.score:2d}/10  {dim.rationale}{flags}")

    if assessment.gut_check_questions:
        print(f"\n{'─'*60}")
        print("  GUT CHECK — answer these before proceeding:")
        for i, q in enumerate(assessment.gut_check_questions, 1):
            print(f"\n  {i}. {q}")
        print(f"{'─'*60}")

    print()


def prompt_gut_check(assessment: FitAssessment) -> str:
    if not assessment.gut_check_questions:
        return ""

    print("Type your answers below. Press Enter twice after each one.\n")
    answers = []
    for i, q in enumerate(assessment.gut_check_questions, 1):
        print(f"Q{i}: {q}")
        lines = []
        while True:
            line = input()
            if line == "" and lines:
                break
            lines.append(line)
        answers.append(f"Q{i}: {q}\nA{i}: {' '.join(lines)}")
        print()

    return "\n\n".join(answers)
