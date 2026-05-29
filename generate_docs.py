"""Generate a PDF documenting the changes made to create FORGE from PACE."""

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

NAVY = colors.HexColor("#0E3689")
NAVY_LIGHT = colors.HexColor("#EEF2FA")
GOLD = colors.HexColor("#C9A227")
GRAY = colors.HexColor("#555555")
LIGHT_GRAY = colors.HexColor("#F5F5F5")
DARK = colors.HexColor("#1A1A1A")
WHITE = colors.white
GREEN = colors.HexColor("#1A7340")

PAGE_W = letter[0] - 1.5 * inch


def styles():
    return {
        "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=28, textColor=WHITE, alignment=TA_CENTER, leading=34),
        "subtitle": ParagraphStyle("subtitle", fontName="Helvetica", fontSize=13, textColor=colors.HexColor("#D0D8F0"), alignment=TA_CENTER, leading=18),
        "meta": ParagraphStyle("meta", fontName="Helvetica", fontSize=10, textColor=colors.HexColor("#B0BBDD"), alignment=TA_CENTER),
        "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=16, textColor=NAVY, spaceBefore=18, spaceAfter=6, leading=20),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, textColor=NAVY, spaceBefore=14, spaceAfter=4, leading=16),
        "h3": ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=10, textColor=DARK, spaceBefore=10, spaceAfter=3),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10, textColor=DARK, leading=15, spaceAfter=5),
        "code": ParagraphStyle("code", fontName="Courier", fontSize=9, textColor=colors.HexColor("#1A3A6B"), leading=13, leftIndent=12, spaceAfter=4),
        "bullet": ParagraphStyle("bullet", fontName="Helvetica", fontSize=10, textColor=DARK, leading=14, leftIndent=14, spaceAfter=3),
        "caption": ParagraphStyle("caption", fontName="Helvetica-Oblique", fontSize=9, textColor=GRAY, leading=13),
        "label": ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=9, textColor=GRAY),
        "tag": ParagraphStyle("tag", fontName="Helvetica-Bold", fontSize=8, textColor=WHITE, alignment=TA_CENTER),
        "tbl_h": ParagraphStyle("tbl_h", fontName="Helvetica-Bold", fontSize=9, textColor=WHITE),
        "tbl_b": ParagraphStyle("tbl_b", fontName="Helvetica", fontSize=9, textColor=DARK, leading=13),
        "tbl_code": ParagraphStyle("tbl_code", fontName="Courier", fontSize=8, textColor=DARK, leading=12),
        "footer": ParagraphStyle("footer", fontName="Helvetica", fontSize=8, textColor=GRAY),
    }


S = styles()


def cover_page():
    items = []
    # Navy background hero
    hero = Table(
        [[Paragraph("FORGE", S["title"])],
         [Spacer(1, 6)],
         [Paragraph("Technical Change Documentation", S["subtitle"])],
         [Spacer(1, 4)],
         [Paragraph(f"PACE → FORGE Conversion &nbsp;·&nbsp; {date.today().strftime('%B %d, %Y')}", S["meta"])]],
        colWidths=[PAGE_W + 0.75 * inch],
    )
    hero.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 30),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 30),
        ("LEFTPADDING", (0, 0), (-1, -1), 20),
        ("RIGHTPADDING", (0, 0), (-1, -1), 20),
    ]))
    items.append(hero)
    items.append(Spacer(1, 24))

    # 3-column stat strip
    stat_data = [
        [
            Paragraph("78", _stat_num()),
            Paragraph("14", _stat_num()),
            Paragraph("0", _stat_num()),
        ],
        [
            Paragraph("Files committed", S["label"]),
            Paragraph("Source files modified", S["label"]),
            Paragraph("Personal references remaining", S["label"]),
        ],
    ]
    stat_tbl = Table(stat_data, colWidths=[PAGE_W / 3] * 3)
    stat_tbl.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, -1), NAVY_LIGHT),
        ("BOX", (0, 0), (-1, -1), 1, NAVY),
        ("LINEBEFORE", (1, 0), (1, -1), 1, NAVY),
        ("LINEBEFORE", (2, 0), (2, -1), 1, NAVY),
    ]))
    items.append(stat_tbl)
    items.append(Spacer(1, 20))

    # Context box
    context = Table([[Paragraph(
        "<b>What this document is:</b> A record of every change made when converting the PACE "
        "job application pipeline into FORGE — a genericized, open-source version that anyone "
        "can configure for their own job search. Covers architecture decisions, file-by-file "
        "changes, and the config schema designed to replace all hardcoded personal data.",
        S["body"]
    )]], colWidths=[PAGE_W])
    context.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GRAY),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CCCCCC")),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    items.append(context)
    items.append(PageBreak())
    return items


def _stat_num():
    return ParagraphStyle("stat_num", fontName="Helvetica-Bold", fontSize=28, textColor=NAVY, alignment=TA_CENTER)


def section_bar(text):
    tbl = Table([[Paragraph(text, ParagraphStyle("bar", fontName="Helvetica-Bold", fontSize=11, textColor=WHITE, leading=14))]], colWidths=[PAGE_W])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def bullet(text):
    return Paragraph(f"&bull;&nbsp;&nbsp;{text}", S["bullet"])


def code(text):
    return Paragraph(text, S["code"])


def overview():
    items = []
    items.append(section_bar("1.  OVERVIEW"))
    items.append(Spacer(1, 10))

    items.append(Paragraph("What PACE Is", S["h2"]))
    items.append(Paragraph(
        "PACE (Personal Applicant Competitive Engine) is a single-entry job application pipeline "
        "built for one person: Jack Falle. Drop a JD PDF and it produces a tailored resume, "
        "cover letter, people intelligence document, executive summary PDF, and keyword gap report. "
        "It also runs job discovery scrapers, tracks applications, and drafts follow-ups.",
        S["body"]
    ))

    items.append(Paragraph("Why FORGE Was Created", S["h2"]))
    items.append(Paragraph(
        "PACE was deeply personalized — career history, identity positioning, compensation floors, "
        "target companies, and defensibility rules were all hardcoded in Python source files and "
        "Claude prompt templates. To share the pipeline with others, every piece of personal data "
        "had to be externalized into configuration that any user could fill in for themselves.",
        S["body"]
    ))

    items.append(Paragraph("Design Constraint", S["h2"]))
    items.append(Paragraph(
        "<b>The original workspace was never touched.</b> All changes were made in a fresh copy "
        "at <font name='Courier' size='9'>/Users/jackfalle/Code/forge</font>. The FORGE repo "
        "was initialized with a clean git history — no PACE commits, no personal data in history.",
        S["body"]
    ))

    items.append(Spacer(1, 12))
    items.append(section_bar("2.  THE CORE ARCHITECTURE CHANGE"))
    items.append(Spacer(1, 10))

    items.append(Paragraph(
        "PACE had personal data in two places: source code (hardcoded in Python prompt templates) "
        "and <font name='Courier' size='9'>config/config.yaml</font> (already had some config but "
        "was still Jack-specific). FORGE moves everything to config.",
        S["body"]
    ))

    items.append(Paragraph("Before: data lived in Python", S["h3"]))
    for line in [
        "prompt_builder.py — full career history, key achievements, identity, defensibility rules hardcoded in the prompt template string",
        "fit_assessor.py — full vision profile, company tiers, comp floors hardcoded in the assessment prompt",
        "docx_modifier.py — ROLE_ANCHORS dict hardcoded with Jack's job titles as anchor text",
        "json_validator.py — role_identifier enum hardcoded with Jack's role slugs",
        "followup.py, negotiation.py — Jack's name, contact info, background hardcoded in prompts",
        "interview/prep_generator.py — full STAR story bank and career history hardcoded",
        "linkedin/optimizer.py — entire career history and achievements hardcoded",
    ]:
        items.append(bullet(line))

    items.append(Spacer(1, 8))
    items.append(Paragraph("After: data lives in config.yaml", S["h3"]))
    for line in [
        "All prompts build dynamically from config at call time",
        "career_history list drives role identifiers, DOCX anchor text, bullet counts, and JSON schema validation",
        "identity, key_achievements, and defensibility_notes drive all resume and cover letter framing",
        "target_companies drives fit scoring and discovery scoring",
        "user_skills drives Workday recommendations in exec summary",
    ]:
        items.append(bullet(line))

    items.append(PageBreak())
    return items


def config_changes():
    items = []
    items.append(section_bar("3.  CONFIG.YAML — NEW SECTIONS"))
    items.append(Spacer(1, 10))

    items.append(Paragraph(
        "The config was completely rewritten. Existing sections were genericized (personal values "
        "replaced with placeholders). New sections were added to capture what was previously "
        "hardcoded in Python.",
        S["body"]
    ))

    items.append(Spacer(1, 8))
    new_sections = [
        ("career_history", "List of roles with id, company, title, period, anchor_text, modifiable, and max_bullets. This single list drives: role_identifier enum in JSON schema, DOCX anchor text lookup, bullet count enforcement in prompts, and role labels in exec summary."),
        ("identity", "primary, secondary, avoid_leading_with, and target_levels. Used in tailoring prompts, people intel prompts, fit assessment, interview prep, and LinkedIn optimizer."),
        ("key_achievements", "2-4 specific proof points with dollar amounts and scale. Appear in resume summary anchors, fit scoring, interview prep STAR bank, and LinkedIn About."),
        ("defensibility_notes", "Rules Claude must follow to avoid overclaiming. Fed directly into tailoring and people intel prompts."),
        ("target_companies", "Tiered company lists (tier1/tier2/tier3). Used by fit_assessor.py for company_tier scoring and discovery/scorer.py for match scoring."),
        ("user_skills", "Renamed from jack_skills. Full skill set for Workday skills recommendations in exec summary."),
    ]

    tbl_data = [[
        Paragraph("Section", S["tbl_h"]),
        Paragraph("Purpose", S["tbl_h"]),
    ]]
    for section, desc in new_sections:
        tbl_data.append([
            Paragraph(f"<font name='Courier'>{section}</font>", S["tbl_code"]),
            Paragraph(desc, S["tbl_b"]),
        ])

    tbl = Table(tbl_data, colWidths=[1.6 * inch, PAGE_W - 1.6 * inch])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, NAVY_LIGHT]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    items.append(tbl)

    items.append(Spacer(1, 14))
    items.append(Paragraph("Sections removed or genericized", S["h3"]))
    for line in [
        "person.* — all values replaced with placeholder strings",
        "google_docs — personal Google Doc IDs removed, only base_resume key remains (empty by default)",
        "gdrive — account field removed, mount_base/applications_folder are now placeholders",
        "tailoring — jmrs_bullets_max and ci_manager_bullets_max removed; bullet limits now live per-role in career_history[].max_bullets",
        "comp_floors — director/manager key names replaced with target_floor/hard_filter_floor (more generic)",
        "discovery — all Jack-specific values (GDrive CSV path, email, cities, titles, Workday tenants) replaced with placeholders",
        "pipeline.gdrive_sync — set to false by default (opt-in)",
    ]:
        items.append(bullet(line))

    items.append(PageBreak())
    return items


def file_changes():
    items = []
    items.append(section_bar("4.  FILE-BY-FILE CHANGES"))
    items.append(Spacer(1, 10))

    changes = [
        (
            "pipeline/research/prompt_builder.py",
            "MAJOR REWRITE",
            [
                "Entire _TAILORING_PROMPT_TEMPLATE and _PEOPLE_INTEL_PROMPT_TEMPLATE replaced with dynamic builders",
                "5 new helper functions: _build_career_history_block(), _build_identity_block(), _build_achievements_block(), _build_defensibility_block(), _build_experience_schema_block(), _build_hard_constraints_block()",
                "Each helper reads from config at call time — no hardcoded career data anywhere",
                "Experience modifications schema in the prompt now generated from career_history where modifiable=true",
                "_build_background_summary() builds a 1-sentence background for people intel personalization",
                "People intel prompt now uses config person.name, person.linkedin, person.education instead of hardcoded Jack data",
                "GSU/soccer captain background removed from people intel prompt",
            ]
        ),
        (
            "pipeline/assessment/fit_assessor.py",
            "MAJOR REWRITE",
            [
                "_build_vision_profile() function replaces hardcoded JACK'S VISION PROFILE block",
                "Identity stack pulled from config identity.primary/secondary/avoid_leading_with",
                "Key achievements pulled from config key_achievements",
                "Hard filter floor pulled from config comp_floors.hard_filter_floor",
                "Company tiers (Tier 1/2/3 lists with scores) built from config target_companies.tier1/2/3",
                "Target levels pulled from config identity.target_levels",
                "_ASSESSMENT_PROMPT now uses {vision_profile} placeholder filled at call time",
            ]
        ),
        (
            "pipeline/tailoring/docx_modifier.py",
            "TARGETED CHANGE",
            [
                "ROLE_ANCHORS class attribute (hardcoded dict with Jack's job titles) replaced with _load_role_anchors() function",
                "Function reads career_history from config and builds id -> anchor_text mapping",
                "ROLE_ANCHORS converted to @property that caches the result on first access",
                "Supports any career history — users configure anchor_text in config to match their DOCX",
            ]
        ),
        (
            "pipeline/tailoring/json_validator.py",
            "TARGETED CHANGE",
            [
                "Hardcoded role_identifier enum [jmrs, ci_manager, sr_analyst, lean_analyst, insyncho] removed",
                "_build_schema() function reads career_history[].id from config to build the enum dynamically",
                "Schema is now generated at validation time — falls back to [current_role, prev_role] if config is empty",
                "Makes schema validation automatically consistent with whatever career history the user configures",
            ]
        ),
        (
            "pipeline/output/exec_summary.py",
            "TARGETED CHANGES",
            [
                "_jack_skills() renamed to _user_skills() — reads user_skills from config (was jack_skills)",
                "_role_label() hardcoded mapping (jmrs, ci_manager, etc.) replaced with config career_history lookup",
                "Comment updated to remove 'Jack's known skill set' language",
            ]
        ),
        (
            "pipeline/tracker/html_renderer.py",
            "TARGETED CHANGES",
            [
                "Page title 'PACE Pipeline — Jack Falle' -> 'FORGE Pipeline — {person.name from config}'",
                "Header meta line 'Jack Falle' -> dynamic name from config",
            ]
        ),
        (
            "pipeline/tracker/followup.py",
            "TARGETED CHANGES",
            [
                "Hardcoded JACK'S IDENTITY block replaced with _build_followup_prompt() function",
                "Function builds candidate block from config: name, email, phone, location, linkedin, current role, primary identity, first achievement",
                "_PROMPT now assembled by calling the function + concatenating the rest of the template",
                "LinkedIn message ending changed from 'end with Jack Falle' to 'end with the candidate's name'",
            ]
        ),
        (
            "pipeline/tracker/negotiation.py",
            "TARGETED CHANGES",
            [
                "Hardcoded JACK'S PROFILE block replaced with _build_negotiation_prompt() function",
                "Pulls name, current role, background, and comp floors from config at call time",
                "All 'Jack should' / 'Jack's floor' language genericized",
                "HTML footer 'PACE' -> 'FORGE'",
            ]
        ),
        (
            "pipeline/cover_letter/generate.py",
            "TARGETED CHANGES",
            [
                "Hardcoded defaults 'Jack Falle', 'jackgfalle@gmail.com', 'linkedin.com/in/jack-falle' removed",
                "All values now read from config with empty-string fallback",
                "_cl_filename() helper derives cover letter filename from person.resume_filename in config",
                "'Jack_Falle_Cover_Letter.txt' / '.docx' -> dynamic filename from config",
            ]
        ),
        (
            "pipeline/discovery/scorer.py",
            "TARGETED CHANGES",
            [
                "Hardcoded _TIER1, _TIER2, _TIER3 company lists removed",
                "_get_tier1/2/3() functions load from config target_companies at call time",
                "_load_location_signals() and _load_search_cities() read from config discovery.search_locations",
                "_company_score() and _location_score() now call dynamic loaders instead of static lists",
                "Module docstring updated to remove 'Jack' and 'CLAUDE.md' references",
            ]
        ),
        (
            "pipeline/discovery/scrapers/email_scraper.py",
            "TARGETED CHANGES",
            [
                "Hardcoded _GMAIL_USER = 'jackgfalle@gmail.com' removed",
                "Gmail user now resolved from config at runtime: discovery.digest_from or person.email",
                "Default Gmail label changed from PACE/Alerts to FORGE/Alerts",
                "Docstring updated accordingly",
            ]
        ),
        (
            "pipeline/people_intel/outreach_extractor.py",
            "TARGETED CHANGES",
            [
                "Hardcoded 'Jack Falle' ending check replaced with _sender_name() reading from config",
                "Both strategy 1 (paragraph ending) and strategy 2 (quoted block) now use dynamic name",
                "_trim_to_limit() takes suffix as argument instead of hardcoding 'Jack Falle'",
                "Module docstring updated",
            ]
        ),
        (
            "pipeline/people_intel/pdf_renderer.py",
            "TARGETED CHANGE",
            [
                "Footer 'People Intelligence — Jack Falle — CONFIDENTIAL' now reads name from config at render time",
            ]
        ),
        (
            "pipeline/interview/prep_generator.py",
            "MAJOR REWRITE",
            [
                "Hardcoded _PREP_PROMPT with Jack's full career history, STAR stories, and identity replaced",
                "_build_prep_background() builds candidate block from config at call time",
                "_PREP_PROMPT_TEMPLATE uses {candidate_background} placeholder",
                "All 'Jack should' / 'Jack's' language in JSON schema structure genericized",
                "generate() function updated to call new template with _build_prep_background()",
            ]
        ),
        (
            "pipeline/linkedin/optimizer.py",
            "MAJOR REWRITE",
            [
                "Hardcoded _OPTIMIZER_PROMPT with Jack's full career history, key metrics, and identity removed",
                "_build_optimizer_prompt(current_profile_block) builds full prompt from config at call time",
                "Experience schema section in JSON output now generated from career_history[:3]",
                "Implementation checklist now generated from career_history",
                "_build_prompt() updated to call _build_optimizer_prompt() instead of static string",
            ]
        ),
        (
            "pipeline/linkedin/report_generator.py",
            "TARGETED CHANGES",
            [
                "_get_name() helper added to read person.name from config",
                "Header subtitle 'Jack Falle — Director/Sr Director Search' -> dynamic from config",
                "Page title 'LinkedIn Optimization — Jack Falle — {today}' -> dynamic from config",
                "role_map for experience section now built from config career_history[:3] instead of hardcoded tuple",
            ]
        ),
        (
            "pipeline/discovery/digest.py",
            "TARGETED CHANGE",
            [
                "Hardcoded fallback email 'jackgfalle@gmail.com' replaced with config person.email fallback",
            ]
        ),
        (
            "pipeline/discovery/fit_filter.py",
            "MINOR",
            [
                "Module docstring 'Jack's Director-level target profile' -> 'user's target profile from config'",
            ]
        ),
        (
            "run.py",
            "TARGETED CHANGES",
            [
                "_named_resume(slug) and _named_cover(slug) helper functions added — derive filenames from person.resume_filename in config",
                "All hardcoded 'Jack_Falle_Resume_{slug}.docx' and 'Jack_Falle_Cover_Letter_{slug}.docx' references replaced",
                "Discovery email print statement now reads config digest_to instead of hardcoded email",
                "Stage comment 'V9 base resume' -> 'base resume'",
                "utils.config imported at module level to support the new helpers",
            ]
        ),
    ]

    for filename, tag, points in changes:
        tag_color = {
            "MAJOR REWRITE": colors.HexColor("#8B0000"),
            "TARGETED CHANGES": NAVY,
            "TARGETED CHANGE": NAVY,
            "MINOR": GRAY,
        }.get(tag, NAVY)

        # File header row
        header = Table([[
            Paragraph(f"<font name='Courier' size='9'>{filename}</font>", S["h3"]),
            Paragraph(tag, ParagraphStyle("tag2", fontName="Helvetica-Bold", fontSize=8, textColor=WHITE, alignment=TA_CENTER)),
        ]], colWidths=[PAGE_W - 1.1 * inch, 1.1 * inch])
        header.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), NAVY_LIGHT),
            ("BACKGROUND", (1, 0), (1, 0), tag_color),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (0, 0), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ]))
        items.append(header)

        for pt in points:
            items.append(Paragraph(f"&bull;&nbsp;&nbsp;{pt}", S["bullet"]))
        items.append(Spacer(1, 8))

    items.append(PageBreak())
    return items


def new_files():
    items = []
    items.append(section_bar("5.  NEW FILES ADDED"))
    items.append(Spacer(1, 10))

    files = [
        ("config/config.yaml", "Complete rewrite", "Previously contained Jack's personal data. Now a user-facing template with placeholder values, extensive comments, and new sections (career_history, identity, key_achievements, defensibility_notes, target_companies). This is the primary thing a new user fills in."),
        (".env.template", "New file", "Documents all environment variables with descriptions of what each API key unlocks. Users copy this to .env and fill in their keys. Previous .env.example was a stub with almost no content."),
        ("assets/README.md", "New file", "Step-by-step guide for preparing the base resume DOCX. Explains anchor text, what sections FORGE modifies, formatting recommendations for ATS compatibility, and the Google Drive alternative."),
        ("README.md", "Complete rewrite", "Replaced the PACE README entirely. Covers what FORGE does, 5-step setup, all commands, discovery setup, Google Drive setup, output folder structure, and troubleshooting. Written for a technical user who has never seen the codebase."),
    ]

    tbl_data = [[
        Paragraph("File", S["tbl_h"]),
        Paragraph("Status", S["tbl_h"]),
        Paragraph("Description", S["tbl_h"]),
    ]]
    for fname, status, desc in files:
        tbl_data.append([
            Paragraph(f"<font name='Courier'>{fname}</font>", S["tbl_code"]),
            Paragraph(status, S["tbl_b"]),
            Paragraph(desc, S["tbl_b"]),
        ])

    tbl = Table(tbl_data, colWidths=[1.6 * inch, 0.9 * inch, PAGE_W - 2.5 * inch])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, NAVY_LIGHT]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    items.append(tbl)

    items.append(Spacer(1, 14))
    items.append(Paragraph("Files removed from PACE that do not appear in FORGE", S["h3"]))
    for line in [
        "CLAUDE.md — Jack-specific AI collaborator context. Not relevant for public users.",
        "CHANGELOG.md — PACE-specific running change log. Starts fresh in FORGE.",
        "com.pace.email-check.plist — Jack's macOS launchd plist with hardcoded paths.",
        ".env / .env.save — personal credentials, gitignored and not copied.",
        "config/credentials.json / config/token.json — Google OAuth tokens, gitignored.",
        "assets/base_resume_v9.docx — Jack's personal resume DOCX.",
        "outputs/ inputs/ logs/ — personal application data, gitignored.",
    ]:
        items.append(bullet(line))

    items.append(PageBreak())
    return items


def user_setup():
    items = []
    items.append(section_bar("6.  WHAT A NEW USER NEEDS TO DO"))
    items.append(Spacer(1, 10))

    steps = [
        ("1", "Clone and install",
         "git clone + pip install -e . — standard Python package install. Requires Python 3.11+."),
        ("2", "Set API keys",
         "cp .env.template .env — then fill in ANTHROPIC_API_KEY (required), PERPLEXITY_API_KEY (recommended for ghost job detection and council review), SERPAPI_API_KEY (optional, for discovery scraping), GMAIL_APP_PASSWORD (optional, for email digest)."),
        ("3", "Fill in config/config.yaml",
         "Work top to bottom through all sections: person (name, email, phone, LinkedIn, resume filename), career_history (roles with ids and anchor text), identity (primary/secondary/avoid), key_achievements (proof points with dollar amounts), target_companies (tiered lists), comp_floors, user_skills, discovery settings."),
        ("4", "Prepare base resume DOCX",
         "Place resume at assets/base_resume.docx. Make sure anchor_text values in config.yaml appear verbatim in the DOCX section headers. Read assets/README.md for the full guide."),
        ("5", "Run the pipeline",
         "python run.py path/to/jd.pdf — that's it. Outputs go to outputs/YYYY-MM-DD_company_role/."),
    ]

    for num, title, desc in steps:
        step_row = Table([[
            Paragraph(num, ParagraphStyle("stepnum", fontName="Helvetica-Bold", fontSize=16, textColor=WHITE, alignment=TA_CENTER)),
            Table([[
                Paragraph(title, S["h3"]),
                Paragraph(desc, S["body"]),
            ]], colWidths=[PAGE_W - 0.7 * inch]),
        ]], colWidths=[0.45 * inch, PAGE_W - 0.45 * inch])
        step_row.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), NAVY),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (0, 0), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ]))
        items.append(step_row)
        items.append(Spacer(1, 6))

    items.append(Spacer(1, 10))
    items.append(section_bar("7.  WHAT DOES NOT NEED TO CHANGE"))
    items.append(Spacer(1, 10))
    items.append(Paragraph(
        "The following modules are fully generic and required zero changes — they operate "
        "on data passed to them at runtime and have no hardcoded personal content:",
        S["body"]
    ))
    unchanged = [
        "pipeline/ingest/ — PDF text extraction and JD parsing. Pure text processing.",
        "pipeline/tailoring/summary_council.py — 3-model council review logic. Generic prompt, no personal data.",
        "pipeline/tailoring/ats_checker.py — ATS compatibility scoring. Operates on any DOCX.",
        "pipeline/tailoring/tailor.py — Applies tailoring JSON to base DOCX. Generic.",
        "pipeline/tailoring/drive_client.py — Google Drive OAuth client. Generic.",
        "pipeline/cover_letter/validator.py — Word count, grammar, em-dash checking. Generic.",
        "pipeline/people_intel/pdf_renderer.py (except one footer line) — Markdown to PDF. Generic.",
        "pipeline/output/folder_manager.py — Application folder structure. Generic.",
        "pipeline/tracker/tracker.py, html_renderer.py (except one name line), digest.py — Tracking logic. Generic.",
        "pipeline/research/keyword_gap.py, viability_checker.py, salary_intel.py, tailor_generator.py, intel_generator.py, perplexity_client.py — Research and generation modules. Generic.",
        "pipeline/discovery/normalizer.py, tracker.py, runner.py, scrapers/ — Discovery infrastructure. Generic.",
        "pipeline/linkedin/profile_parser.py — LinkedIn export parser. Generic.",
        "utils/ — All utilities (config loader, logging, grammar, text, progress). Generic.",
    ]
    for line in unchanged:
        items.append(bullet(line))

    items.append(Spacer(1, 14))

    # Footer note
    note = Table([[Paragraph(
        f"<b>Original workspace:</b> /Users/jackfalle/Code/job-application-pipeline — "
        f"<b>not modified</b>.<br/>"
        f"<b>FORGE workspace:</b> /Users/jackfalle/Code/forge — "
        f"clean git repo, initial commit {date.today().strftime('%Y-%m-%d')}.",
        S["caption"]
    )]], colWidths=[PAGE_W])
    note.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY_LIGHT),
        ("BOX", (0, 0), (-1, -1), 1, NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    items.append(note)

    return items


def build_pdf(out_path: Path):
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.65 * inch,
    )

    today = date.today().strftime("%B %d, %Y")

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(GRAY)
        canvas.drawString(0.75 * inch, 0.35 * inch, "FORGE — Technical Change Documentation")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.35 * inch, f"{today}  |  Page {_doc.page}")
        canvas.restoreState()

    story = []
    story += cover_page()
    story += overview()
    story += config_changes()
    story += file_changes()
    story += new_files()
    story += user_setup()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(f"PDF saved: {out_path}")


if __name__ == "__main__":
    out = Path(__file__).parent / "FORGE_Change_Documentation.pdf"
    build_pdf(out)
