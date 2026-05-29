"""Generate a one-page executive summary PDF for a job application.

Sections:
  - Compensation (posted or Perplexity-estimated)
  - Job overview (location, remote, ATS, deadline, intro paragraph)
  - Key skills the JD calls for
  - Resume modification summary (what the tailoring JSON changed)
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.request import urlopen

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from pipeline.ingest.jd_parser import ParsedJD
from utils.config import get as _cfg_get

logger = logging.getLogger(__name__)

NAVY        = colors.HexColor("#0E3689")
NAVY_LIGHT  = colors.HexColor("#E8EDF5")
GOLD_BG     = colors.HexColor("#FFF8E1")
GOLD_BORDER = colors.HexColor("#E6C84D")
GREEN_BG    = colors.HexColor("#E8F5E9")
GREEN_BDR   = colors.HexColor("#66BB6A")
GRAY_BG     = colors.HexColor("#F5F5F5")
CARD_BORDER = colors.HexColor("#D0D0D0")
WHITE       = colors.white
BLACK       = colors.HexColor("#1A1A1A")
GRAY        = colors.HexColor("#666666")
PAGE_W      = letter[0] - 1.5 * inch  # usable width


def generate(
    jd: ParsedJD,
    company: str,
    role: str,
    tailoring_data: dict,
    salary_intel: dict,
    output_dir: Path,
    slug: str,
) -> Path:
    """Build and save the executive summary PDF. Returns the saved path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"exec_summary_{slug}.pdf"

    fonts = _setup_fonts()
    styles = _build_styles(fonts)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.7 * inch,
    )

    today_str = datetime.now().strftime("%Y-%m-%d")

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(fonts["regular"], 8)
        canvas.setFillColor(GRAY)
        canvas.drawString(0.75 * inch, 0.4 * inch, f"Executive Summary — {company} — CONFIDENTIAL")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.4 * inch, f"Generated {today_str}")
        canvas.restoreState()

    story = _build_story(jd, company, role, tailoring_data, salary_intel, styles, fonts, today_str)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    logger.info("Executive summary saved: %s", out_path)
    return out_path


# Story builder

def _build_story(
    jd: ParsedJD,
    company: str,
    role: str,
    td: dict,
    salary_intel: dict,
    styles: dict,
    fonts: dict,
    today_str: str,
) -> list:
    story: list = []

    # Top rule
    story.append(HRFlowable(width="100%", thickness=3, color=NAVY, spaceAfter=8))

    # Title
    story.append(Paragraph("EXECUTIVE SUMMARY", styles["eyebrow"]))
    story.append(Paragraph(html.escape(company), styles["h1"]))
    story.append(Paragraph(html.escape(role), styles["role"]))
    story.append(Spacer(1, 10))

    # Compensation
    story.append(_section_bar("COMPENSATION", fonts))
    story.append(Spacer(1, 6))
    story += _comp_block(jd, salary_intel, styles)
    story.append(Spacer(1, 10))

    # Job Overview
    story.append(_section_bar("JOB OVERVIEW", fonts))
    story.append(Spacer(1, 6))
    story += _overview_block(jd, styles)
    story.append(Spacer(1, 10))

    # Key Skills
    story.append(_section_bar("KEY SKILLS CALLED FOR", fonts))
    story.append(Spacer(1, 6))
    story += _skills_block(jd, styles)
    story.append(Spacer(1, 10))

    # Workday Skills
    workday_skills = _extract_workday_skills(jd, td)
    if workday_skills:
        story.append(_section_bar("WORKDAY SKILLS — TYPE THESE IN", fonts))
        story.append(Spacer(1, 6))
        story += _workday_skills_block(workday_skills, styles)
        story.append(Spacer(1, 10))

    # Resume Modifications
    story.append(_section_bar("RESUME MODIFICATIONS", fonts))
    story.append(Spacer(1, 6))
    story += _resume_changes_block(td, styles)

    return story


# Section: Compensation

def _comp_block(jd: ParsedJD, salary_intel: dict, styles: dict) -> list:
    items: list = []
    posted = jd.salary_range or ""
    is_posted = bool(posted) and posted not in ("Not listed", "Not specified")

    if is_posted:
        # Green box: confirmed posted comp
        label = "Posted Compensation"
        value = html.escape(posted)
        bg, border = GREEN_BG, GREEN_BDR
    elif salary_intel.get("estimated_range"):
        conf = salary_intel.get("confidence", "unknown")
        conf_label = {"high": "high confidence", "medium": "est.", "low": "rough est."}.get(conf, "est.")
        label = f"Market Estimate ({conf_label})"
        value = html.escape(salary_intel["estimated_range"]) + " base"
        bg, border = GOLD_BG, GOLD_BORDER
    else:
        label = "Compensation"
        value = "Not posted — market data unavailable"
        bg, border = GRAY_BG, CARD_BORDER

    cell_style = ParagraphStyle(
        "CompCell",
        fontName=styles["body"].fontName,
        fontSize=11,
        leading=16,
        textColor=BLACK,
    )
    label_style = ParagraphStyle(
        "CompLabel",
        fontName=styles["label"].fontName,
        fontSize=9,
        leading=12,
        textColor=GRAY,
    )
    t = Table(
        [[Paragraph(label, label_style)], [Paragraph(f"<b>{value}</b>", cell_style)]],
        colWidths=[PAGE_W],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 1, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    items.append(t)

    # Source note for estimated
    if not is_posted and salary_intel.get("source_note"):
        items.append(Spacer(1, 3))
        items.append(Paragraph(
            f"Source: {html.escape(salary_intel['source_note'][:120])}",
            styles["caption"],
        ))

    return items


# Section: Job Overview

def _overview_block(jd: ParsedJD, styles: dict) -> list:
    items: list = []

    # Key-value metadata row
    meta: list[tuple[str, str]] = []
    if jd.location:
        meta.append(("Location", jd.location))
    if jd.remote_ok:
        meta.append(("Work Type", "Remote / Hybrid eligible"))
    if jd.ats_system:
        meta.append(("ATS", jd.ats_system))
    if jd.req_number:
        meta.append(("Req #", jd.req_number))
    if jd.apply_by_date:
        meta.append(("Apply By", jd.apply_by_date))

    if meta:
        rows = [[Paragraph(f"<b>{k}</b>", styles["kv_key"]), Paragraph(html.escape(v), styles["kv_val"])] for k, v in meta]
        t = Table(rows, colWidths=[1.4 * inch, PAGE_W - 1.4 * inch])
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        items.append(t)
        items.append(Spacer(1, 8))

    # Intro paragraph from JD
    intro = _extract_intro(jd.raw_text)
    if intro:
        items.append(Paragraph(html.escape(intro), styles["body"]))

    return items


def _extract_intro(raw_text: str) -> str:
    """Pull the first substantive prose paragraph from the JD (not headers or bullets)."""
    if not raw_text:
        return ""
    paragraphs = re.split(r"\n{2,}", raw_text.strip())
    for para in paragraphs:
        para = para.strip()
        # Skip: empty, very short, all-caps headers, bullet-led lines
        if len(para) < 60:
            continue
        if para == para.upper() and len(para) < 200:
            continue
        if re.match(r"^[\-\*\•]", para):
            continue
        # Strip inline bullet noise from multi-line paragraphs
        clean = re.sub(r"\n\s*[\-\*\•]\s*", " ", para)
        clean = re.sub(r"\s{2,}", " ", clean).strip()
        # Trim to ~500 chars at a sentence boundary
        if len(clean) > 500:
            match = re.search(r"[.!?]\s", clean[:500])
            clean = clean[: match.end()].strip() if match else clean[:500]
        if len(clean) > 40:
            return clean
    return ""


# Section: Key Skills

def _skills_block(jd: ParsedJD, styles: dict) -> list:
    reqs = jd.key_requirements or []

    if not reqs:
        return [Paragraph("No structured requirements parsed from this JD.", styles["body"])]

    items: list = []
    for req in reqs[:14]:
        items.append(Paragraph(f"&bull;&nbsp;&nbsp;{html.escape(req)}", styles["bullet"]))
    return items


# Section: Workday Skills

def _user_skills() -> set[str]:
    skills = _cfg_get("user_skills", [])
    return {s.lower() for s in skills} if skills else set()

# Generic filler words to strip from skill candidates
_SKIP_TOKENS = {
    "and", "or", "the", "with", "for", "of", "in", "to", "a", "an",
    "experience", "background", "knowledge", "understanding", "ability",
    "skills", "skill", "proficiency", "expertise",
}


def _extract_workday_skills(jd: ParsedJD, td: dict) -> list[str]:
    """Build a curated list of Workday-ready skill terms for this specific JD.

    Sources (in priority order):
    1. Individual skills parsed from the tailoring JSON technical_skills rows
    2. Skill-like tokens from the competency rows
    3. JD key_requirements cross-referenced against the user's skill set

    Returns deduplicated, title-cased list sorted for readability.
    """
    candidates: set[str] = set()

    # Source 1: technical_skills rows (comma-separated, already JD-aligned)
    for row in td.get("technical_skills", []):
        for token in row.split(","):
            clean = token.strip().strip("•").strip()
            if len(clean) > 2 and clean.lower() not in _SKIP_TOKENS:
                candidates.add(clean)

    # Source 2: competency rows - extract multi-word skill phrases
    for row in td.get("competencies", []):
        for token in row.split(","):
            clean = token.strip().strip("•").strip()
            if 3 < len(clean) < 50 and clean.lower() not in _SKIP_TOKENS:
                candidates.add(clean)

    jd_text_lower = (jd.raw_text or "").lower()
    for skill in _user_skills():
        if skill in jd_text_lower:
            candidates.add(skill.title())

    # Deduplicate case-insensitively (prefer the casing from sources 1/2)
    seen_lower: set[str] = set()
    final: list[str] = []
    for skill in sorted(candidates, key=lambda s: s.lower()):
        if skill.lower() not in seen_lower and len(skill) > 2:
            seen_lower.add(skill.lower())
            final.append(skill)

    return final[:30]  # cap at 30; Workday fields have limits


def _workday_skills_block(skills: list[str], styles: dict) -> list:
    """Gold callout box listing exact skill terms to type into Workday."""
    items: list = []

    instruction_style = ParagraphStyle(
        "WDInstruct",
        fontName=styles["label"].fontName,
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#7B5E00"),
        spaceAfter=6,
    )
    skill_style = ParagraphStyle(
        "WDSkill",
        fontName=styles["body"].fontName,
        fontSize=10,
        leading=15,
        textColor=BLACK,
    )

    # Instruction line
    items.append(Paragraph(
        "Workday Skills did not autofill. Type each skill below individually into the Skills search field. "
        "These are exact-match terms aligned to this JD — each one is a verified capability.",
        instruction_style,
    ))

    # Skills in a wrapped 3-column table
    row_size = 3
    rows = []
    for i in range(0, len(skills), row_size):
        chunk = skills[i:i + row_size]
        while len(chunk) < row_size:
            chunk.append("")
        rows.append([Paragraph(f"&bull;&nbsp;{html.escape(s)}" if s else "", skill_style) for s in chunk])

    col_w = PAGE_W / row_size
    t = Table(rows, colWidths=[col_w] * row_size)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GOLD_BG),
        ("BOX", (0, 0), (-1, -1), 1, GOLD_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, GOLD_BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    items.append(t)
    return items


# Section: Resume Modifications

def _resume_changes_block(td: dict, styles: dict) -> list:
    items: list = []

    # Headline
    headline = td.get("headline", "")
    if headline:
        items.append(Paragraph("Headline", styles["change_label"]))
        items.append(Paragraph(html.escape(headline), styles["change_value"]))
        items.append(Spacer(1, 6))

    # Summary
    summary = td.get("summary", "")
    if summary:
        items.append(Paragraph("Professional Summary", styles["change_label"]))
        items.append(Paragraph(html.escape(summary), styles["change_value"]))
        items.append(Spacer(1, 6))

    # Competencies
    competencies = td.get("competencies", [])
    if competencies:
        items.append(Paragraph("Competency Rows", styles["change_label"]))
        for i, row in enumerate(competencies, 1):
            items.append(Paragraph(f"Row {i}: {html.escape(row)}", styles["change_value"]))
        items.append(Spacer(1, 6))

    # Experience modifications
    mods = td.get("experience_modifications", [])
    for mod in mods:
        role_id = mod.get("role_identifier", "")
        co = mod.get("company", role_id)
        label = _role_label(role_id, co)

        items.append(Paragraph(label, styles["change_label"]))
        lead_in = mod.get("replacement_lead_in", "")
        if lead_in:
            items.append(Paragraph(html.escape(lead_in), styles["change_value"]))
        for bullet in mod.get("replacement_bullets", []):
            items.append(Paragraph(f"&bull;&nbsp;&nbsp;{html.escape(bullet)}", styles["change_bullet"]))
        items.append(Spacer(1, 6))

    # Technical skills
    tech = td.get("technical_skills", [])
    if tech:
        items.append(Paragraph("Technical Skills Rows", styles["change_label"]))
        for i, row in enumerate(tech, 1):
            items.append(Paragraph(f"Row {i}: {html.escape(row)}", styles["change_value"]))
        items.append(Spacer(1, 6))

    # Positioning notes
    notes = td.get("notes", "")
    if notes:
        items.append(Paragraph("Positioning Notes", styles["change_label"]))
        items.append(Paragraph(html.escape(notes), styles["caption"]))

    return items


def _role_label(role_id: str, company: str) -> str:
    # Build label from config career_history if available; fall back to company+id
    from utils.config import get
    history = get("career_history", [])
    for role in history:
        if role.get("id") == role_id:
            return f"{role.get('company', company)} — {role.get('title', role_id)}"
    return f"{company} ({role_id})"


# PDF Primitives

def _section_bar(text: str, fonts: dict) -> Table:
    bar_style = ParagraphStyle(
        "BarText",
        fontName=fonts["bold"],
        fontSize=11,
        leading=14,
        textColor=WHITE,
    )
    t = Table([[Paragraph(text, bar_style)]], colWidths=[PAGE_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _build_styles(fonts: dict) -> dict:
    f = fonts

    def s(name, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, **kw)

    return {
        "eyebrow":      s("Eyebrow",    fontName=f["bold"], fontSize=9,  leading=12, textColor=NAVY,  spaceAfter=2, spaceBefore=4),
        "h1":           s("H1",         fontName=f["bold"], fontSize=22, leading=26, textColor=BLACK, spaceAfter=2),
        "role":         s("Role",       fontName=f["regular"], fontSize=14, leading=18, textColor=GRAY, spaceAfter=4),
        "body":         s("Body",       fontName=f["regular"], fontSize=10, leading=15, textColor=BLACK, spaceAfter=4),
        "bullet":       s("Bullet",     fontName=f["regular"], fontSize=10, leading=14, textColor=BLACK, spaceAfter=2, leftIndent=12),
        "label":        s("Label",      fontName=f["bold"],    fontSize=9,  leading=12, textColor=GRAY),
        "caption":      s("Caption",    fontName=f["regular"], fontSize=8,  leading=12, textColor=GRAY, spaceAfter=2),
        "kv_key":       s("KVKey",      fontName=f["bold"],    fontSize=10, leading=14, textColor=BLACK),
        "kv_val":       s("KVVal",      fontName=f["regular"], fontSize=10, leading=14, textColor=BLACK),
        "change_label": s("ChgLabel",   fontName=f["bold"],    fontSize=10, leading=14, textColor=NAVY,  spaceBefore=4, spaceAfter=2),
        "change_value": s("ChgValue",   fontName=f["regular"], fontSize=10, leading=14, textColor=BLACK, spaceAfter=2, leftIndent=8),
        "change_bullet": s("ChgBullet", fontName=f["regular"], fontSize=10, leading=14, textColor=BLACK, spaceAfter=1, leftIndent=20),
    }


def _setup_fonts() -> dict:
    font_urls = {
        "Inter":        "https://cdn.jsdelivr.net/fontsource/fonts/inter@latest/latin-400-normal.ttf",
        "Inter-Bold":   "https://cdn.jsdelivr.net/fontsource/fonts/inter@latest/latin-700-normal.ttf",
        "Inter-Italic": "https://cdn.jsdelivr.net/fontsource/fonts/inter@latest/latin-400-italic.ttf",
    }
    try:
        for name, url in font_urls.items():
            pdfmetrics.registerFont(TTFont(name, BytesIO(urlopen(url, timeout=10).read())))
        return {"regular": "Inter", "bold": "Inter-Bold", "italic": "Inter-Italic"}
    except Exception:
        return {"regular": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique"}
