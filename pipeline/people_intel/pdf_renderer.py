"""Render a people intelligence markdown file to a styled PDF.

Flexible markdown renderer — handles any markdown structure Claude generates.
H1 (#) becomes the document title/hero. H2 (##) becomes navy section bars.
H3 (###), bullets, bold, paragraphs all render cleanly.
"""

from __future__ import annotations

import html
import logging
import re
import sys
from io import BytesIO
from pathlib import Path
from urllib.request import urlopen

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
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

logger = logging.getLogger(__name__)

NAVY       = colors.HexColor("#0E3689")
NAVY_LIGHT = colors.HexColor("#E8EDF5")
GOLD_BG    = colors.HexColor("#FFF8E1")
GOLD_BORDER= colors.HexColor("#E6C84D")
MSG_BG     = colors.HexColor("#F5F5F5")
CARD_BORDER= colors.HexColor("#D0D0D0")
WHITE      = colors.white
BLACK      = colors.HexColor("#1A1A1A")
GRAY       = colors.HexColor("#666666")


def run(input_md: Path, output_pdf: Path | None = None) -> Path:
    if not input_md.exists():
        raise FileNotFoundError(f"Markdown not found: {input_md}")
    output_pdf = output_pdf or input_md.with_suffix(".pdf")
    md_text = input_md.read_text(encoding="utf-8")
    _render(md_text, output_pdf)
    logger.info("People intel PDF saved: %s", output_pdf)
    return output_pdf


def _render(md_text: str, output_pdf: Path) -> None:
    fonts = _setup_fonts()
    styles = _build_styles(fonts)

    doc = SimpleDocTemplate(
        str(output_pdf),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.7 * inch,
    )

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(fonts["regular"], 8)
        canvas.setFillColor(GRAY)
        from utils.config import get as _cfg
        name = _cfg("person.name", "")
        canvas.drawString(0.75 * inch, 0.4 * inch, f"People Intelligence — {name} — CONFIDENTIAL")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.4 * inch, f"Page {doc.page}")
        canvas.restoreState()

    story = _build_story(md_text, styles, fonts)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def _build_story(md_text: str, styles: dict, fonts: dict) -> list:
    story = []
    # Opening rule
    story.append(HRFlowable(width="100%", thickness=3, color=NAVY, spaceAfter=10))

    lines = md_text.split("\n")
    i = 0
    in_code_block = False
    code_lines: list[str] = []

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        # Code fences
        if stripped.startswith("```"):
            if in_code_block:
                in_code_block = False
                if code_lines:
                    block_text = "<br/>".join(html.escape(l) for l in code_lines)
                    t = Table(
                        [[Paragraph(block_text, styles["code"])]],
                        colWidths=[6.8 * inch],
                    )
                    t.setStyle(TableStyle([
                        ("BACKGROUND", (0, 0), (-1, -1), MSG_BG),
                        ("BOX", (0, 0), (-1, -1), 0.5, CARD_BORDER),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]))
                    story.append(t)
                    story.append(Spacer(1, 4))
                    code_lines = []
            else:
                in_code_block = True
            i += 1
            continue

        if in_code_block:
            code_lines.append(raw)
            i += 1
            continue

        # Blank line
        if not stripped:
            story.append(Spacer(1, 4))
            i += 1
            continue

        # H1 — document title
        if stripped.startswith("# ") and not stripped.startswith("##"):
            text = _inline(stripped[2:])
            story.append(Paragraph(text, styles["h1"]))
            story.append(Spacer(1, 4))
            i += 1
            continue

        # H2 — navy section bar
        if stripped.startswith("## ") and not stripped.startswith("###"):
            text = _inline(stripped[3:])
            story.append(Spacer(1, 8))
            story.append(_section_bar(text, fonts))
            story.append(Spacer(1, 6))
            i += 1
            continue

        # H3 — bold navy subheader
        if stripped.startswith("### "):
            text = _inline(stripped[4:])
            story.append(Paragraph(text, styles["h3"]))
            i += 1
            continue

        # H4+
        if stripped.startswith("#### "):
            text = _inline(stripped[5:])
            story.append(Paragraph(text, styles["h4"]))
            i += 1
            continue

        # HR
        if re.match(r"^-{3,}$|^\*{3,}$|^_{3,}$", stripped):
            story.append(HRFlowable(width="100%", thickness=0.5, color=GRAY, spaceBefore=4, spaceAfter=4))
            i += 1
            continue

        # Bullet (- or * or numbered)
        bullet_match = re.match(r"^(\s*)[-*] (.+)", raw)
        num_match = re.match(r"^(\s*)\d+\. (.+)", raw)
        if bullet_match or num_match:
            m = bullet_match or num_match
            indent = len(m.group(1))
            text = _inline(m.group(2))
            style = styles["bullet_2"] if indent >= 2 else styles["bullet"]
            marker = "&bull;" if (bullet_match or indent == 0) else "&bull;"
            story.append(Paragraph(f"{marker}&nbsp;&nbsp;{text}", style))
            i += 1
            continue

        # Regular paragraph
        text = _inline(stripped)
        if text:
            story.append(Paragraph(text, styles["body"]))
        i += 1

    return story


def _section_bar(text: str, fonts: dict) -> Table:
    style = ParagraphStyle(
        "BarText",
        fontName=fonts["bold"],
        fontSize=11,
        leading=14,
        textColor=WHITE,
    )
    t = Table([[Paragraph(text, style)]], colWidths=[6.8 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _inline(text: str) -> str:
    """Convert inline markdown to ReportLab XML. Escapes HTML first."""
    text = html.escape(text)
    # Bold+italic: ***text***
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", text)
    # Bold: **text** or __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    # Italic: *text* or _text_
    text = re.sub(r"\*([^*]+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"_([^_]+?)_", r"<i>\1</i>", text)
    # Inline code
    text = re.sub(r"`([^`]+?)`", r'<font name="Courier">\1</font>', text)
    # Links: [text](url) → text (drop URL)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    return text


def _build_styles(fonts: dict) -> dict:
    def s(name, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, **kw)
    f = fonts
    return {
        "h1":       s("H1", fontName=f["bold"], fontSize=20, leading=24, textColor=BLACK, spaceAfter=4, spaceBefore=8),
        "h3":       s("H3", fontName=f["bold"], fontSize=12, leading=16, textColor=NAVY, spaceAfter=4, spaceBefore=8),
        "h4":       s("H4", fontName=f["bold"], fontSize=11, leading=15, textColor=GRAY, spaceAfter=3, spaceBefore=6),
        "body":     s("Body", fontName=f["regular"], fontSize=10, leading=15, textColor=BLACK, spaceAfter=4),
        "bullet":   s("Bullet", fontName=f["regular"], fontSize=10, leading=14, textColor=BLACK, spaceAfter=2, leftIndent=12),
        "bullet_2": s("Bullet2", fontName=f["regular"], fontSize=10, leading=14, textColor=BLACK, spaceAfter=2, leftIndent=28),
        "code":     s("Code", fontName="Courier", fontSize=9, leading=13, textColor=BLACK),
    }


def _setup_fonts() -> dict:
    font_urls = {
        "Inter":          "https://cdn.jsdelivr.net/fontsource/fonts/inter@latest/latin-400-normal.ttf",
        "Inter-Bold":     "https://cdn.jsdelivr.net/fontsource/fonts/inter@latest/latin-700-normal.ttf",
        "Inter-Italic":   "https://cdn.jsdelivr.net/fontsource/fonts/inter@latest/latin-400-italic.ttf",
    }
    try:
        for name, url in font_urls.items():
            pdfmetrics.registerFont(TTFont(name, BytesIO(urlopen(url, timeout=10).read())))
        return {"regular": "Inter", "bold": "Inter-Bold", "italic": "Inter-Italic"}
    except Exception:
        return {"regular": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique"}
