"""Generate the comprehensive Executive Summary PDF.

Sections:
  1. Compensation (posted or estimated)
  2. Company Strategy
  3. Role Context
  4. Day in the Life
  5. Interview Prep (talking points + high-signal question)
  6. Red Flags / Tailwinds
  7. Council Analysis (independent section — all panel reviews + synthesis)

Renders the structured brief produced by research.exec_intel (Perplexity in
cloud mode, ddgs + local LLM in OSS mode). The two paths return the same shape,
so this renderer is backend-agnostic.
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

logger = logging.getLogger(__name__)


def _clean(text: str) -> str:
    """Strip markdown and citation artifacts before rendering in ReportLab."""
    if not text:
        return ""
    # Convert **bold** to ReportLab <b> tags
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # Strip citation markers [1], [2][3], etc.
    text = re.sub(r'\[\d+\]', '', text)
    # Replace Unicode block char (■) used by some models as hyphen
    text = text.replace('■', '-').replace('▪', '-')
    # Replace smart dashes and quotes
    text = text.replace('—', '-').replace('–', '-')
    text = text.replace('“', '"').replace('”', '"')
    text = text.replace('’', "'").replace('‘', "'")
    # Collapse excess whitespace
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text

NAVY        = colors.HexColor("#0E3689")
NAVY_LIGHT  = colors.HexColor("#E8EDF5")
GOLD_BG     = colors.HexColor("#FFF8E1")
GOLD_BORDER = colors.HexColor("#E6C84D")
GREEN_BG    = colors.HexColor("#E8F5E9")
GREEN_BDR   = colors.HexColor("#66BB6A")
RED_BG      = colors.HexColor("#FFEBEE")
RED_BDR     = colors.HexColor("#EF9A9A")
GRAY_BG     = colors.HexColor("#F5F5F5")
CARD_BORDER = colors.HexColor("#D0D0D0")
COUNCIL_BG  = colors.HexColor("#F0F4FF")
COUNCIL_BDR = colors.HexColor("#7B9FE0")
WHITE       = colors.white
BLACK       = colors.HexColor("#1A1A1A")
GRAY        = colors.HexColor("#666666")
PAGE_W      = letter[0] - 1.5 * inch


def generate(
    jd: ParsedJD,
    company: str,
    role: str,
    salary_intel: dict,
    intel_result: dict,
    output_dir: Path,
    slug: str,
) -> Path:
    """Build and save the Executive Summary PDF. Returns the saved path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"exec_summary_{slug}.pdf"

    fonts = _setup_fonts()
    styles = _build_styles(fonts)
    today_str = datetime.now().strftime("%Y-%m-%d")

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.7 * inch,
    )

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(fonts["regular"], 8)
        canvas.setFillColor(GRAY)
        canvas.drawString(0.75 * inch, 0.4 * inch, f"Executive Summary — {company} — CONFIDENTIAL")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.4 * inch, f"Generated {today_str}")
        canvas.restoreState()

    story = _build_story(jd, company, role, salary_intel, intel_result, styles, fonts, today_str)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    logger.info("Executive summary saved: %s", out_path)
    return out_path


# Story builder

def _build_story(
    jd: ParsedJD,
    company: str,
    role: str,
    salary_intel: dict,
    intel_result: dict,
    styles: dict,
    fonts: dict,
    today_str: str,
) -> list:
    story: list = []
    agg = intel_result.get("aggregation", {})

    story.append(HRFlowable(width="100%", thickness=3, color=NAVY, spaceAfter=8))
    story.append(Paragraph("EXECUTIVE SUMMARY", styles["eyebrow"]))
    story.append(Paragraph(html.escape(company), styles["h1"]))
    story.append(Paragraph(html.escape(role), styles["role"]))
    story.append(Spacer(1, 10))

    # Compensation
    story.append(_section_bar("COMPENSATION", fonts))
    story.append(Spacer(1, 6))
    story += _comp_block(jd, salary_intel, styles)
    story.append(Spacer(1, 12))

    if not agg:
        story.append(Paragraph(
            "Executive intelligence not available — no research backend "
            "(Perplexity or OSS) produced results.",
            styles["body"],
        ))
        return story

    # Company Strategy
    if agg.get("company_strategy"):
        story.append(_section_bar("COMPANY STRATEGY", fonts))
        story.append(Spacer(1, 6))
        story += _body_paragraphs(_clean(agg["company_strategy"]), styles)
        story.append(Spacer(1, 12))

    # Role Context
    if agg.get("role_context"):
        story.append(_section_bar("ROLE CONTEXT", fonts))
        story.append(Spacer(1, 6))
        story += _body_paragraphs(_clean(agg["role_context"]), styles)
        story.append(Spacer(1, 12))

    # Day in the Life
    if agg.get("day_in_the_life"):
        story.append(_section_bar("DAY IN THE LIFE", fonts))
        story.append(Spacer(1, 6))
        story += _body_paragraphs(_clean(agg["day_in_the_life"]), styles)
        story.append(Spacer(1, 12))

    # Interview Prep
    talking_points = agg.get("interview_talking_points", [])
    high_signal_q = agg.get("high_signal_question", "")
    if talking_points or high_signal_q:
        story.append(_section_bar("INTERVIEW PREP", fonts))
        story.append(Spacer(1, 6))
        if talking_points:
            story.append(Paragraph("Talking Points", styles["change_label"]))
            for pt in talking_points:
                story.append(Paragraph(f"&bull;&nbsp;&nbsp;{_clean(pt)}", styles["bullet"]))
            story.append(Spacer(1, 6))
        if high_signal_q:
            story.append(Paragraph("High-Signal Question to Ask", styles["change_label"]))
            story.append(Paragraph(f"&quot;{_clean(high_signal_q)}&quot;", styles["body_italic"]))
        story.append(Spacer(1, 12))

    # Red Flags / Tailwinds
    red_flags = agg.get("red_flags", "")
    tailwinds = agg.get("tailwinds", "")
    show_flags = red_flags and red_flags.lower() not in ("none identified", "none", "")
    show_tail  = tailwinds and tailwinds.lower() not in ("none identified", "none", "")
    if show_flags or show_tail:
        story.append(_section_bar("RED FLAGS / TAILWINDS", fonts))
        story.append(Spacer(1, 6))
        if show_flags:
            story += _signal_card("Red Flags", _clean(red_flags), RED_BG, RED_BDR, styles)
            story.append(Spacer(1, 6))
        if show_tail:
            story += _signal_card("Tailwinds", _clean(tailwinds), GREEN_BG, GREEN_BDR, styles)
        story.append(Spacer(1, 12))

    # Unique Insights
    unique = agg.get("unique_insights", [])
    if unique:
        story.append(_section_bar("UNIQUE INSIGHTS", fonts))
        story.append(Spacer(1, 6))
        for insight in unique:
            story.append(Paragraph(f"&bull;&nbsp;&nbsp;{_clean(insight)}", styles["bullet"]))
        story.append(Spacer(1, 12))

    # Council Analysis (independent section)
    story.append(HRFlowable(width="100%", thickness=2, color=COUNCIL_BDR, spaceAfter=6))
    story.append(Paragraph("COUNCIL ANALYSIS", styles["council_header"]))
    story.append(Paragraph(
        "Independent model reviews of the deep research findings, followed by synthesis.",
        styles["caption"],
    ))
    story.append(Spacer(1, 8))

    panel = intel_result.get("panel", {})
    for model, text in panel.items():
        if not text or text.startswith("[ERROR:"):
            story.append(Paragraph(
                f"<b>{html.escape(model.upper())}</b>: model returned no output or errored.",
                styles["caption"],
            ))
            continue
        story.append(Paragraph(f"<b>{html.escape(model.upper())}</b>", styles["change_label"]))
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            cleaned = _clean(line)
            if not cleaned:
                continue
            # Bullet lines (- text or * text)
            if re.match(r'^[-*]\s+', cleaned):
                cleaned = re.sub(r'^[-*]\s+', '', cleaned)
                story.append(Paragraph(f"&bull;&nbsp;&nbsp;{cleaned}", styles["council_body_bullet"]))
            # Numbered section headers (1. Title or **1. Title**)
            elif re.match(r'^\d+[\.\)]\s', cleaned) or re.match(r'^<b>\d+', cleaned):
                story.append(Paragraph(cleaned, styles["council_sub"]))
            else:
                story.append(Paragraph(cleaned, styles["council_body"]))
        story.append(Spacer(1, 8))

    # Consensus / Divergence
    consensus = agg.get("consensus", "")
    divergence = agg.get("divergence", "")
    if consensus:
        story.append(Paragraph("Consensus", styles["council_sub"]))
        story.append(Paragraph(_clean(consensus), styles["council_body"]))
        story.append(Spacer(1, 4))
    if divergence and divergence.lower() not in ("no significant divergence", ""):
        story.append(Paragraph("Divergence", styles["council_sub"]))
        story.append(Paragraph(_clean(divergence), styles["council_body"]))
        story.append(Spacer(1, 4))

    if agg.get("raw"):
        story.append(Paragraph(
            "Aggregator parse failed — raw output truncated below. "
            "This usually means the model response was cut off (token limit). "
            "Structured sections above may be incomplete.",
            styles["caption"],
        ))

    return story


# Section: Compensation

def _comp_block(jd: ParsedJD, salary_intel: dict, styles: dict) -> list:
    items: list = []
    posted = jd.salary_range or ""
    is_posted = bool(posted) and posted not in ("Not listed", "Not specified")

    if is_posted:
        label, value, bg, border = "Posted Compensation", html.escape(posted), GREEN_BG, GREEN_BDR
    elif salary_intel.get("estimated_range"):
        conf = salary_intel.get("confidence", "unknown")
        conf_label = {"high": "high confidence", "medium": "est.", "low": "rough est."}.get(conf, "est.")
        label = f"Market Estimate ({conf_label})"
        value = html.escape(salary_intel["estimated_range"]) + " base"
        bg, border = GOLD_BG, GOLD_BORDER
    else:
        label, value, bg, border = "Compensation", "Not posted — market data unavailable", GRAY_BG, CARD_BORDER

    cell_style = ParagraphStyle("CompCell", fontName=styles["body"].fontName,
                                fontSize=11, leading=16, textColor=BLACK)
    label_style = ParagraphStyle("CompLabel", fontName=styles["label"].fontName,
                                 fontSize=9, leading=12, textColor=GRAY)
    t = Table([[Paragraph(label, label_style)], [Paragraph(f"<b>{value}</b>", cell_style)]],
              colWidths=[PAGE_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 1, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    items.append(t)
    if not is_posted and salary_intel.get("source_note"):
        items.append(Spacer(1, 3))
        items.append(Paragraph(
            f"Source: {html.escape(salary_intel['source_note'][:120])}",
            styles["caption"],
        ))
    return items


def _body_paragraphs(text: str, styles: dict) -> list:
    """Split text on double-newlines and render each block as a paragraph."""
    items = []
    for block in re.split(r'\n{2,}', text):
        block = block.strip()
        if not block:
            continue
        for line in block.split('\n'):
            line = line.strip()
            if not line:
                continue
            if re.match(r'^[-*]\s+', line):
                line = re.sub(r'^[-*]\s+', '', line)
                items.append(Paragraph(f"&bull;&nbsp;&nbsp;{line}", styles["bullet"]))
            else:
                items.append(Paragraph(line, styles["body"]))
        items.append(Spacer(1, 4))
    return items


def _signal_card(label: str, text: str, bg: object, border: object, styles: dict) -> list:
    label_style = ParagraphStyle("SigLabel", fontName=styles["label"].fontName,
                                 fontSize=9, leading=12, textColor=GRAY)
    body_style = ParagraphStyle("SigBody", fontName=styles["body"].fontName,
                                fontSize=10, leading=15, textColor=BLACK)
    t = Table(
        [[Paragraph(label, label_style)], [Paragraph(html.escape(text), body_style)]],
        colWidths=[PAGE_W],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 1, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return [t]


# PDF Primitives

def _section_bar(text: str, fonts: dict) -> Table:
    bar_style = ParagraphStyle("BarText", fontName=fonts["bold"], fontSize=11,
                               leading=14, textColor=WHITE)
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
    def s(name, **kw): return ParagraphStyle(name, **kw)
    return {
        "eyebrow":       s("Eyebrow",     fontName=f["bold"],    fontSize=9,  leading=12, textColor=NAVY,  spaceAfter=2, spaceBefore=4),
        "h1":            s("H1",          fontName=f["bold"],    fontSize=22, leading=26, textColor=BLACK, spaceAfter=2),
        "role":          s("Role",        fontName=f["regular"], fontSize=14, leading=18, textColor=GRAY,  spaceAfter=4),
        "body":          s("Body",        fontName=f["regular"], fontSize=10, leading=15, textColor=BLACK, spaceAfter=4),
        "body_italic":   s("BodyItalic",  fontName=f["italic"],  fontSize=10, leading=15, textColor=BLACK, spaceAfter=4, leftIndent=12),
        "bullet":        s("Bullet",      fontName=f["regular"], fontSize=10, leading=14, textColor=BLACK, spaceAfter=2, leftIndent=12),
        "label":         s("Label",       fontName=f["bold"],    fontSize=9,  leading=12, textColor=GRAY),
        "caption":       s("Caption",     fontName=f["regular"], fontSize=8,  leading=12, textColor=GRAY,  spaceAfter=2),
        "change_label":  s("ChgLabel",    fontName=f["bold"],    fontSize=10, leading=14, textColor=NAVY,  spaceBefore=4, spaceAfter=2),
        "council_header":      s("CncHdr",    fontName=f["bold"],    fontSize=13, leading=16, textColor=COUNCIL_BDR, spaceBefore=6, spaceAfter=2),
        "council_body":        s("CncBody",   fontName=f["regular"], fontSize=9,  leading=14, textColor=BLACK, spaceAfter=2, leftIndent=8),
        "council_body_bullet": s("CncBull",   fontName=f["regular"], fontSize=9,  leading=13, textColor=BLACK, spaceAfter=1, leftIndent=20),
        "council_sub":         s("CncSub",    fontName=f["bold"],    fontSize=9,  leading=13, textColor=NAVY,  spaceAfter=1, leftIndent=8),
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
