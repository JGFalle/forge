#!/usr/bin/env python3
"""
Job Application Pipeline, single entry point.

Usage:
    python run.py <path-to-jd.pdf> [--company "Acme Corp"] [--role "Director of Ops"]
    python run.py --tailor <path-to-tailoring.json> [--output-dir <path>]
    python run.py --intel <path-to-people-intel.md>
    python run.py --tracker
    python run.py --prep "Company" "Role"

Full pipeline (triggered by dropping a JD PDF): fully automated:
  1. ingest      - extract text from PDF, parse JD fields
  2. assess      - fit scoring via Claude API (STRONG_FIT / STRETCH / HARD_PASS)
  3. folder     , create application folder, write prompt files as reference
  4. tailor     , generate tailoring JSON via Claude API
  5. intel      , generate people intel via Claude API (+ Perplexity if key set)
  6. resume     : apply tailoring JSON to base resume DOCX
  7. cover       - generate cover letter DOCX
  8. pdf         - render people intel markdown to PDF
  9. gap        , keyword gap report vs. JD
  10. gdrive    , sync application folder to Google Drive
  11. track     , add entry to application_tracker.json
"""

from __future__ import annotations

import argparse
import json as _json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from utils.logging import add_file_handler, get_logger
from utils.config import get as _cfg

logger = get_logger(__name__)


def _named_resume(slug: str) -> str:
    """Return the slug-suffixed resume filename derived from config."""
    fn = _cfg("person.resume_filename", "YourName_Resume.docx")
    base = fn.replace(".docx", "")
    return f"{base}_{slug}.docx"


def _named_cover(slug: str) -> str:
    fn = _cfg("person.resume_filename", "YourName_Resume.docx")
    base = fn.replace("_Resume.docx", "")
    return f"{base}_Cover_Letter_{slug}.docx"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Job Application Pipeline")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("jd_pdf", nargs="?", help="Path to the job description PDF")
    group.add_argument("--tailor", metavar="JSON", help="Apply a tailoring JSON directly (skips ingest)")
    group.add_argument("--intel", metavar="MD", help="Render a people intel markdown to PDF")
    group.add_argument("--tracker", action="store_true", help="Show pipeline dashboard")
    group.add_argument("--sync", action="store_true", help="Reconcile the tracker CSV (Excel) and JSON, then regenerate CSV + HTML")
    group.add_argument("--dedupe", action="store_true", help="Find and resolve duplicate tracker entries (preview by default; confirm to apply)")
    group.add_argument("--prep", nargs=2, metavar=("COMPANY", "ROLE"), help="Generate interview prep brief")
    group.add_argument("--digest", action="store_true", help="Show weekly pipeline digest")
    group.add_argument(
        "--draft-followup",
        nargs=2,
        metavar=("COMPANY", "ROLE"),
        help='Draft a follow-up message: --draft-followup "Company" "Role"',
    )
    group.add_argument(
        "--negotiate",
        nargs=2,
        metavar=("COMPANY", "ROLE"),
        help='Generate salary negotiation brief: --negotiate "Company" "Role"',
    )
    group.add_argument("--cleanup", action="store_true", help="Mark stale applications as ghosted (30+ days silent)")
    group.add_argument("--regen-intel", metavar="APP_FOLDER", help="Regenerate people intel for an existing application folder")
    group.add_argument("--regen-cover", metavar="APP_FOLDER", help="Regenerate cover letter for an existing application folder")
    group.add_argument("--regen-resume", metavar="APP_FOLDER", help="Regenerate resume for an existing application folder")
    group.add_argument("--council", metavar="APP_FOLDER", help="Re-run council on an existing application folder and re-apply to resume + cover letter")
    group.add_argument(
        "--linkedin-optimize",
        nargs="?",
        const="generate",
        metavar="EXPORT_PATH",
        help="Generate LinkedIn profile optimization report. Optionally pass path to LinkedIn data export (.zip or folder).",
    )
    group.add_argument(
        "--discover",
        action="store_true",
        help="Run job discovery scrapers, update tracker, send digest email",
    )
    group.add_argument(
        "--email-check",
        action="store_true",
        help="Quick email-only check: read Gmail alerts, append new jobs to tracker (no scraping, no email digest)",
    )

    p.add_argument("--company", help="Company name (required when using JD PDF without embedded detection)")
    p.add_argument("--role", help="Role title (required when using JD PDF without embedded detection)")
    p.add_argument("--output-dir", default=None, help="Override output directory")
    p.add_argument("--dry-run", action="store_true", help="Parse and validate; do not write files")
    p.add_argument("--debug", action="store_true", help="Enable debug logging")
    p.add_argument(
        "--offer-amount",
        default="",
        metavar="AMOUNT",
        help='Offer amount for negotiation brief, e.g. "$185K"',
    )
    p.add_argument(
        "--context",
        default="",
        metavar="TEXT",
        help="Pre-supply application context (gut-check answers or override reason) — skips interactive prompt",
    )
    p.add_argument(
        "--gdrive-target",
        default="",
        metavar="PATH",
        help="Exact GDrive folder to copy outputs into (set automatically by pace when JD is picked from GDrive)",
    )
    p.add_argument(
        "--status",
        nargs="+",
        metavar="ARG",
        help='Update status: --status "Company" "Role Title" new_status [optional note]',
    )
    p.add_argument(
        "--contact",
        nargs="+",
        metavar="ARG",
        help='Log a contact: --contact "Company" "Role" "Name" [type] [channel] [notes]',
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    logger.handlers[0].setLevel(10 if args.debug else 20)

    # Status update
    if args.status:
        from pipeline.tracker.tracker import update_status
        parts = args.status
        if len(parts) < 3:
            print('Usage: --status "Company Name" "Role Title" new_status [optional note]')
            sys.exit(1)
        company_arg, role_arg, status_arg = parts[0], parts[1], parts[2]
        note_arg = " ".join(parts[3:]) if len(parts) > 3 else ""
        if update_status(company_arg, role_arg, status_arg, note_arg):
            print(f"Updated: {company_arg} → {status_arg}")
            if status_arg == "offer":
                offer_hint = f' --offer-amount "{note_arg}"' if note_arg else ""
                print(f'\n  You have an offer. Run: negotiate "{company_arg}" "{role_arg}"{offer_hint}')
        else:
            print(f"No entry found for: {company_arg}")
        return

    # Contact logging
    if args.contact:
        from pipeline.tracker.tracker import add_contact
        parts = args.contact
        if len(parts) < 3:
            print('Usage: --contact "Company" "Role" "Name" [type] [channel] [notes]')
            print('  type:    warm_referral | hiring_manager | recruiter | intel_contact')
            print('  channel: linkedin | email | phone | in_person')
            sys.exit(1)
        add_contact(
            company=parts[0],
            role=parts[1],
            name=parts[2],
            contact_type=parts[3] if len(parts) > 3 else "intel_contact",
            channel=parts[4] if len(parts) > 4 else "linkedin",
            notes=" ".join(parts[5:]) if len(parts) > 5 else "",
        )
        print(f"Contact logged: {parts[2]} for {parts[0]}")
        return

    # Tracker / pipeline view
    # Tracker sync (CSV ⇄ JSON ⇄ HTML)
    if args.sync:
        from pipeline.tracker.csv_sync import sync as sync_tracker
        result = sync_tracker()
        if result["changed"] or result["added"]:
            print(f"Imported from CSV: {result['changed']} updated, {result['added']} added")
        for w in result["warnings"]:
            print(f"  ⚠  {w}")
        print(f"CSV:  {result['csv']}")
        print(f"HTML: {result['html']}")
        return

    if args.tracker:
        import subprocess
        from pipeline.tracker.csv_sync import sync as sync_tracker
        from pipeline.tracker.tracker import display_pipeline
        # Fold in any Excel edits first, then render the dashboard from truth.
        result = sync_tracker()
        if result["changed"] or result["added"]:
            print(f"Imported from CSV: {result['changed']} updated, {result['added']} added")
        for w in result["warnings"]:
            print(f"  ⚠  {w}")
        display_pipeline()
        html_path = result["html"]
        print(f"Opening dashboard: {html_path}")
        subprocess.run(["open", str(html_path)], check=False)
        return

    # Stale cleanup
    if args.cleanup:
        from pipeline.tracker.tracker import cleanup_stale
        updated = cleanup_stale(days_threshold=30)
        if updated:
            print(f"\nMarked {len(updated)} application(s) as ghosted:")
            for u in updated:
                print(f"  {u['company']} / {u['role']} — {u['days_silent']}d silent (was: {u['status']})")
        else:
            print("\nNo stale applications found (threshold: 30 days).")
        return

    # Dedupe tracker entries
    if args.dedupe:
        from pipeline.tracker.tracker import dedupe
        preview = dedupe(apply=False)
        if not preview["groups"]:
            print("\nNo duplicate (company, role) entries found.")
            return
        print(f"\nFound {len(preview['groups'])} duplicate group(s) "
              f"({preview['total_before']} entries -> {preview['total_after']}):")
        for g in preview["groups"]:
            print(f"  [{g['action'].upper()}] {g['company']} / {g['role']} "
                  f"— {g['count']} copies, remove {g['removed']}, keep status '{g['kept_status']}'")
            for c in g["conflicts"]:
                print(f"      ⚠  {c}")
        answer = input("\nApply these changes? A backup is saved first. [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted. Nothing was written.")
            return
        result = dedupe(apply=True)
        from pipeline.tracker.csv_sync import sync as sync_tracker
        sync_tracker()
        print(f"\nDedupe applied: {result['total_before']} -> {result['total_after']} entries "
              f"({result['merged_groups']} merged, {result['deleted_groups']} collapsed).")
        print(f"Backup: {result['backup']}")
        print("CSV + HTML regenerated.")
        return

    # Follow-up draft
    if args.draft_followup:
        from pipeline.tracker.followup import draft, display as display_followup, save as save_followup
        company_arg, role_arg = args.draft_followup
        result = draft(company_arg, role_arg)
        display_followup(result)
        if not result.get("error"):
            slug = _make_slug(company_arg, role_arg)
            saved = save_followup(result, Path("outputs/followups"), slug)
            if saved:
                print(f"Draft saved: {saved}")
        return

    # Negotiation brief
    if args.negotiate:
        import subprocess
        from pipeline.tracker.negotiation import run as run_negotiate
        company_arg, role_arg = args.negotiate
        offer = getattr(args, "offer_amount", "")
        print(f"\nBuilding negotiation brief for {company_arg}...")
        html_path = run_negotiate(company_arg, role_arg, offer_amount=offer)
        print(f"Brief saved: {html_path}")
        subprocess.run(["open", str(html_path)], check=False)
        return

    # Weekly digest
    if args.digest:
        import subprocess
        from pipeline.tracker.digest import generate as gen_digest, display as disp_digest, save_html as save_digest
        data = gen_digest()
        disp_digest(data)
        html_path = save_digest(data, Path("outputs"))
        print(f"Digest saved: {html_path}")
        subprocess.run(["open", str(html_path)], check=False)
        return

    # LinkedIn profile optimizer
    if args.linkedin_optimize:
        import subprocess
        from pipeline.linkedin.optimizer import run as run_linkedin
        from utils.progress import spinner
        export_path = None
        if args.linkedin_optimize != "generate":
            export_path = Path(args.linkedin_optimize)
            if not export_path.exists():
                print(f"Export path not found: {export_path}")
                sys.exit(1)
            print(f"\nParsing LinkedIn export: {export_path.name}")
        else:
            print("\nGenerating LinkedIn optimization (no current profile provided)")
        with spinner("Calling Claude Sonnet for profile optimization"):
            html_path = run_linkedin(export_path=export_path)
        print(f"\nReport saved: {html_path}")
        subprocess.run(["open", str(html_path)], check=False)
        return

    # Discovery: run scrapers + send digest
    if args.discover:
        from pipeline.discovery.runner import run as run_discovery
        quiet = not sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False
        result = run_discovery(send_email=True)
        new_count = result.get("new", 0)
        summary = result.get("summary", {})
        print(f"\n  Discovery complete: {new_count} new job{'s' if new_count != 1 else ''} added to tracker")
        print(f"  Queue: {summary.get('TBD',0)} TBD | {summary.get('Queued',0)} Queued | {summary.get('Applied',0)} Applied")
        if result.get("email_sent"):
            from utils.config import get as _cfg
            print(f"  Digest email sent to {_cfg('discovery.digest_to', '')}")
        elif new_count > 0:
            print("  (email skipped — check GMAIL_APP_PASSWORD in .env)")
        return

    # Email-only check (launchd 15-min interval)
    if args.email_check:
        from pipeline.discovery.runner import run_email_only
        added = run_email_only()
        if added:
            print(f"Email check: {added} new job{'s' if added != 1 else ''} added to tracker")
        return

    # Regen: people intel
    if args.regen_intel:
        _regen_intel(Path(args.regen_intel))
        return

    # Regen: cover letter
    if args.regen_cover:
        _regen_cover(Path(args.regen_cover))
        return

    # Regen: resume
    if args.regen_resume:
        _regen_resume(Path(args.regen_resume))
        return

    # Re-run council on existing application folder
    if args.council:
        _run_council_regen(Path(args.council))
        return

    # Interview prep
    if args.prep:
        import subprocess
        from pipeline.interview.prep_generator import run as run_prep
        from pipeline.tracker.tracker import load, _find_entry
        company_arg, role_arg = args.prep
        entries = load()
        entry = _find_entry(entries, company_arg, role_arg)
        app_folder = Path(entry["app_folder"]) if entry and entry.get("app_folder") else None
        context = entry.get("notes", "") if entry else ""
        print(f"\nGenerating interview prep for {company_arg}...")
        html_path = run_prep(
            company=company_arg,
            role=role_arg,
            app_folder=app_folder,
            application_context=context,
        )
        print(f"Brief saved: {html_path}")
        subprocess.run(["open", str(html_path)], check=False)
        return

    # People intel only
    if args.intel:
        from pipeline.people_intel.pdf_renderer import run as render_intel
        md_path = Path(args.intel)
        out_pdf = render_intel(md_path)
        print(f"PDF saved: {out_pdf}")
        return

    # Tailor only
    if args.tailor:
        import json as _json
        from pipeline.tailoring.tailor import run as run_tailor
        from pipeline.tailoring.ats_checker import check as ats_check, display_report as ats_display, save_report as ats_save
        from pipeline.cover_letter.generate import run as run_cover
        from pipeline.cover_letter.validator import validate as validate_cover, display_result as display_cover_result, save_result as save_cover_result

        json_path = Path(args.tailor)
        output_dir = Path(args.output_dir) if args.output_dir else Path("outputs/standalone")
        _tailor_slug = json_path.stem

        resume_path = run_tailor(json_path, output_dir, dry_run=args.dry_run)
        if resume_path:
            ats_result = ats_check(resume_path)
            ats_display(ats_result)
            ats_save(ats_result, output_dir, _tailor_slug)

            cover_path = run_cover(json_path, output_dir)
            with open(json_path, encoding="utf-8") as _f:
                _cl_text = _json.load(_f).get("cover_letter", "")
            if _cl_text:
                cv_result = validate_cover(_cl_text)
                display_cover_result(cv_result)
                save_cover_result(cv_result, output_dir, _tailor_slug)

            print(f"\nResume:       {resume_path}")
            print(f"Cover letter: {cover_path}")
            print(f"ATS score:    {ats_result['score']}/100  [{ats_result['grade']}]")
        return

    # Full pipeline (JD PDF)
    jd_pdf = Path(args.jd_pdf)
    if not jd_pdf.exists():
        logger.error("JD PDF not found: %s", jd_pdf)
        sys.exit(1)

    # Stage 1: Ingest
    from pipeline.ingest.pdf_reader import extract_text
    from pipeline.ingest.jd_parser import parse
    from utils.progress import spinner, stage as show_stage, done as show_done

    logger.info("Stage 1: Ingesting %s", jd_pdf.name)
    show_stage(1, f"Ingesting JD  —  {jd_pdf.name}")
    raw_text = extract_text(jd_pdf)
    jd = parse(raw_text)

    # Claude extraction, fires only when regex parse left company/role blank
    if not jd.company or not jd.role:
        from pipeline.ingest.jd_extractor import extract_company_role
        logger.info("Company/role not found by regex — calling Claude extractor")
        extracted_company, extracted_role = extract_company_role(raw_text)
        if extracted_company:
            jd.company = extracted_company
        if extracted_role:
            jd.role = extracted_role

    company = args.company or jd.company or _prompt_user("Company name")
    role = args.role or jd.role or _prompt_user("Role title")
    today = datetime.now().strftime("%Y-%m-%d")
    slug = _make_slug(company, role)

    # Duplicate detection, warn if already applied to this role
    _check_for_duplicate(company, role)

    # Stage 1.5: Job Viability Assessment: ghost job + freshness check before any API credits spent
    from pipeline.research.viability_checker import check as viability_check, display as viability_display, should_block as viability_blocks
    from utils.progress import spinner, stage as show_stage, done as show_done
    logger.info("Stage 1.5: Job viability check for %s / %s", company, role)
    show_stage(2, f"Job Viability  —  {company}")
    with spinner("Querying Perplexity for ghost job signals"):
        viability = viability_check(company, role)
    viability_display(viability)

    if viability_blocks(viability):
        signals_text = "; ".join(viability.get("signals", [])) or "see signals above"
        print(f"  HIGH ghost job risk detected: {signals_text}")
        if args.context:
            print(f"  --context flag supplied — proceeding despite high ghost risk.")
        else:
            try:
                print("\n" + "─" * 60)
                answer = _tty_input("  Ghost job risk is HIGH. Proceed anyway? (y/n): ").lower()
                print("─" * 60)
                if answer != "y":
                    print("Stopped. No files written.")
                    sys.exit(0)
            except (EOFError, OSError):
                print("\nNon-interactive terminal — use --context to override ghost job block.")
                sys.exit(1)

    # Salary intel - fetch from Perplexity if JD did not post comp
    salary_intel = {}
    original_salary = jd.salary_range  # preserve before any mutation
    if not jd.salary_range or jd.salary_range in ("Not listed", "Not specified"):
        from pipeline.research.salary_intel import fetch_salary_intel
        logger.info("Salary not posted — querying market data for %s / %s", company, role)
        with spinner("Fetching market salary data"):
            salary_intel = fetch_salary_intel(company, role, jd.location or "")
        if salary_intel.get("estimated_range"):
            jd.salary_range = f"~{salary_intel['estimated_range']} (market est.)"

    # Stage 2: Fit Assessment
    from pipeline.assessment.fit_assessor import assess, display, prompt_gut_check

    logger.info("Stage 2: Running fit assessment for %s / %s", company, role)
    show_stage(3, f"Fit Assessment  —  {company}")
    with spinner("Scoring role against vision profile"):
        assessment = assess(jd, company, role)
    display(assessment)
    if salary_intel:
        from pipeline.research.salary_intel import display_salary_intel
        display_salary_intel(salary_intel, original_salary or "")

    application_context = ""

    if assessment.verdict == "HARD_PASS":
        if args.context:
            application_context = f"OVERRIDE — Hard pass overridden by user.\nReason: {args.context}"
        else:
            try:
                print("\n" + "─" * 60)
                override = _tty_input("  HARD PASS — Apply anyway? (y/n): ").lower()
                print("─" * 60)
                if override != "y":
                    print("Stopped. No files written.")
                    sys.exit(0)
                print("\nGot it. Tell me why you're applying despite the hard pass.")
                print("(Your answer shapes the cover letter tone and positioning.)\n")
                reason = _tty_input("> ")
                application_context = f"OVERRIDE — Hard pass overridden by user.\nReason: {reason}"
            except (EOFError, OSError):
                print("\nNon-interactive terminal — use --context to supply override reason.")
                sys.exit(1)

    elif assessment.verdict == "STRETCH":
        if args.context:
            application_context = f"STRETCH APPLICATION — User gut-check answers:\n{args.context}"
        else:
            print("Stretch fit detected. Answer the gut-check questions before we generate materials.\n")
            try:
                application_context = prompt_gut_check(assessment)
                if application_context:
                    application_context = f"STRETCH APPLICATION — User gut-check answers:\n{application_context}"
            except EOFError:
                print("\nNon-interactive terminal — use --context to supply your answers.")
                sys.exit(1)

    # Stage 3: Create application folder
    import json as _json
    import shutil as _shutil
    from pipeline.output.folder_manager import create_application_folder, copy_file
    from pipeline.research.prompt_builder import build_tailoring_prompt, build_people_intel_prompt, write_prompts

    logger.info("Stage 3: Creating application folder for %s / %s", company, role)
    show_stage(4, "Creating Application Folder")
    app_folder = create_application_folder(company, role)
    copy_file(jd_pdf, app_folder / "jd")

    add_file_handler(logger, app_folder / "run.log")

    # Save assessment and write prompt files as reference artifacts
    _save_assessment(app_folder / "research", assessment, application_context, slug)
    tailoring_prompt = build_tailoring_prompt(jd, company, role, slug, today, application_context)
    people_prompt = build_people_intel_prompt(jd, company, role, slug)
    write_prompts(app_folder / "research", tailoring_prompt, people_prompt, slug)
    if salary_intel and salary_intel.get("raw"):
        from pipeline.research.salary_intel import save_salary_intel
        save_salary_intel(salary_intel, company, role, app_folder / "research")

    if args.dry_run:
        print(f"\nDRY RUN complete — folder created, no API calls made.")
        print(f"  {app_folder}")
        sys.exit(0)

    # Stage 4: Generate tailoring JSON → tailoring_json/
    from pipeline.research.tailor_generator import generate as generate_tailoring
    logger.info("Stage 4: Generating tailoring JSON via API")
    show_stage(5, "Tailoring JSON")
    with spinner("Generating JSON via Claude Sonnet"):
        _, tailoring_path = generate_tailoring(
            jd, company, role, slug, application_context,
            output_dir=app_folder / "tailoring_json",
        )

    # Stage 4a: Keyword gap + grammar pre-scan - both fed to council as context
    from pipeline.research.keyword_gap import compute_gap
    from pipeline.tailoring.json_validator import grammar_check as grammar_check_json
    with open(tailoring_path, encoding="utf-8") as _f:
        _td_for_prescan = _json.load(_f)
    _pre_gap = compute_gap(jd.raw_text, _td_for_prescan)
    _missing_terms = _pre_gap.get("missing", [])[:20]  # top 20 missing JD terms for council
    logger.info("Pre-council keyword gap: %.1f%% coverage, %d missing terms",
                _pre_gap["coverage_pct"], len(_pre_gap["missing"]))
    _grammar_issues = grammar_check_json(_td_for_prescan)
    if _grammar_issues:
        logger.info("Grammar pre-scan: %d field(s) flagged — council will review", len(_grammar_issues))

    # Stage 4b: Council, reviews summary + cover letter, patches tailoring JSON with both
    council_path = None
    from utils.config import get as _cfg_get
    if _cfg_get("tailoring.council_enabled", True) and os.getenv("PERPLEXITY_API_KEY"):
        from pipeline.tailoring.summary_council import run_council, save_report as save_council, display_result as display_council
        logger.info("Stage 4b: Council review (summary + cover letter)")
        show_stage(5, "Council Review")
        with open(tailoring_path, encoding="utf-8") as _f:
            _td_raw = _json.load(_f)
        _draft_summary = _td_raw.get("summary", "")
        _draft_cover = _td_raw.get("cover_letter", "")
        with spinner("Running model council on summary + cover letter"):
            council_result = run_council(
                summary=_draft_summary,
                cover_letter=_draft_cover,
                jd_text=jd.raw_text,
                company=company,
                role=role,
                missing_keywords=_missing_terms,
                application_context=application_context,
            )
        # Patch tailoring JSON with council-revised fields before downstream stages use it
        _agg = council_result.get("aggregation", {})
        _patched = False
        if _agg.get("final_summary"):
            _td_raw["summary"] = _agg["final_summary"]
            _patched = True
        if _agg.get("final_cover_letter"):
            _td_raw["cover_letter"] = _agg["final_cover_letter"]
            _patched = True
        if _patched:
            with open(tailoring_path, "w", encoding="utf-8") as _f:
                _json.dump(_td_raw, _f, indent=2)
            logger.info("Tailoring JSON patched with council revisions")
        council_path = save_council(
            council_result, _draft_summary, _draft_cover, company, role,
            output_dir=app_folder / "research",
            slug=slug,
        )
        display_council(council_result, _draft_summary, _draft_cover)
        if council_path:
            print(f"  Council report: {council_path.name}")
    else:
        logger.debug("Council skipped (disabled or no PERPLEXITY_API_KEY)")

    # Review gate, pause before applying JSON to DOCX so user can inspect/edit
    if _cfg_get("tailoring.review_gate", True):
        print(f"\n  Tailoring JSON: {tailoring_path}")
        try:
            answer = _tty_input("  Review/edit tailoring JSON before applying to resume? (y/n): ").strip().lower()
            if answer == "y":
                import subprocess as _sp
                _sp.run(["open", str(tailoring_path)])
                _tty_input("  Edit the file, save it, then press Enter to continue...")
                print("  Continuing with updated JSON.")
        except (EOFError, OSError):
            pass

    # Stage 5: Generate people intel → people_intel/ (Perplexity enrichment if key available)
    from pipeline.research.perplexity_client import fetch_company_intel
    from pipeline.research.intel_generator import generate as generate_intel
    logger.info("Stage 5: Generating people intel")
    show_stage(6, f"People Intelligence  —  {company}")
    with spinner("Fetching live intel via Perplexity"):
        perplexity_context = fetch_company_intel(company, role)
    if perplexity_context:
        print(f"  {len(perplexity_context):,} chars of live market intel")
    with spinner("Generating people intel via Claude Sonnet"):
        intel_md_path = generate_intel(
            jd, company, role, slug,
            output_dir=app_folder / "people_intel",
            perplexity_context=perplexity_context,
        )
    # Extract outreach messages immediately so they're copy-paste ready
    from pipeline.people_intel.outreach_extractor import extract as extract_outreach, save as save_outreach, display as display_outreach
    _intel_md_text = intel_md_path.read_text(encoding="utf-8")
    _outreach_msgs = extract_outreach(_intel_md_text)
    save_outreach(_outreach_msgs, app_folder / "people_intel", slug)
    display_outreach(_outreach_msgs)

    # Inject personalized salutation into tailoring JSON if a hiring manager is identifiable
    _hm_first_name = _extract_hiring_manager_name(_intel_md_text)
    if _hm_first_name:
        with open(tailoring_path, encoding="utf-8") as _f:
            _td = _json.load(_f)
        if "cover_letter_salutation" not in _td:
            _td["cover_letter_salutation"] = _hm_first_name
            with open(tailoring_path, "w", encoding="utf-8") as _f:
                _json.dump(_td, _f, indent=2)
            logger.info("Cover letter salutation set to: %s", _hm_first_name)

    # Stage 6: Apply tailoring → resume/YourName_Resume.docx + named copy + ATS check
    from pipeline.tailoring.tailor import run as run_tailor
    from pipeline.tailoring.ats_checker import check as ats_check, display_report as ats_display, save_report as ats_save
    logger.info("Stage 6: Tailoring resume")
    show_stage(7, "Resume Tailoring + ATS Check")
    resume_path = run_tailor(tailoring_path, app_folder / "resume")
    named_resume = None
    ats_result = {}
    if resume_path:
        named_resume = resume_path.parent / _named_resume(slug)
        _shutil.copy2(resume_path, named_resume)
        ats_result = ats_check(resume_path)
        ats_display(ats_result)
        ats_save(ats_result, app_folder / "research", slug)

    # Stage 7: Generate cover letter → cover_letter/CoverLetter.docx + named copy + txt + quality gate
    from pipeline.cover_letter.generate import run as run_cover, run_txt as run_cover_txt
    from pipeline.cover_letter.validator import validate as validate_cover, display_result as display_cover_result, save_result as save_cover_result
    logger.info("Stage 7: Generating cover letter")
    show_stage(8, "Cover Letter + Quality Gate")
    cover_path = run_cover(tailoring_path, app_folder / "cover_letter")
    named_cover = None
    cover_txt_path = None
    if cover_path:
        named_cover = cover_path.parent / _named_cover(slug)
        _shutil.copy2(cover_path, named_cover)
        cover_txt_path = run_cover_txt(tailoring_path, app_folder / "cover_letter")
        # Quality gate
        with open(tailoring_path, encoding="utf-8") as _f:
            _cl_text = _json.load(_f).get("cover_letter", "")
        cv_result = validate_cover(_cl_text, jd.raw_text[:3000])
        display_cover_result(cv_result)
        save_cover_result(cv_result, app_folder / "research", slug)

    # Stage 8: Render people intel → people_intel/*.pdf
    from pipeline.people_intel.pdf_renderer import run as render_intel
    logger.info("Stage 8: Rendering people intel PDF")
    show_stage(9, "People Intel PDF")
    intel_pdf_path = render_intel(intel_md_path)

    # Stage 9: Keyword gap report → research/
    from pipeline.research.keyword_gap import compute_gap, display_gap, save_gap_report
    logger.info("Stage 9: Running keyword gap analysis")
    show_stage(10, "Keyword Gap Analysis")
    with open(tailoring_path, encoding="utf-8") as _f:
        tailoring_data = _json.load(_f)
    gap = compute_gap(jd.raw_text, tailoring_data)
    display_gap(gap)
    save_gap_report(gap, company, role, app_folder / "research")

    # Stage 9b: Resume Modifications PDF → research/ (fast, no API calls)
    from pipeline.output.resume_mods import generate as gen_resume_mods
    logger.info("Stage 9b: Generating resume modifications PDF")
    show_stage(11, "Resume Modifications PDF")
    resume_mods_path = gen_resume_mods(
        jd=jd,
        company=company,
        role=role,
        tailoring_data=tailoring_data,
        salary_intel=salary_intel,
        output_dir=app_folder / "research",
        slug=slug,
    )
    exec_summary_path = None  # populated below only if the user opts into deep intel

    # Stage 10: Tracker (upsert, no duplicate if pipeline reruns)
    from pipeline.tracker.tracker import add_entry
    _override_reason = ""
    if "OVERRIDE" in application_context:
        _override_reason = application_context
    add_entry(
        company=company,
        role=role,
        req_number=jd.req_number,
        salary_range=jd.salary_range,
        ats_system=jd.ats_system,
        app_folder=str(app_folder),
        fit_verdict=assessment.verdict,
        fit_score=assessment.overall_score,
        apply_by_date=jd.apply_by_date or "",
        override_reason=_override_reason,
    )

    # Stage 11: Google Drive: prompt user
    show_done("Pipeline Complete")
    print(f"\n{'='*60}")
    print(f"  APPLICATION READY: {company}")
    print(f"{'='*60}")
    print(f"  Folder:       {app_folder}")
    if resume_path:
        print(f"  Resume:       {resume_path.name}")
        if named_resume:
            print(f"                {named_resume.name}")
    if cover_path:
        print(f"  Cover letter: {cover_path.name}")
        if named_cover:
            print(f"                {named_cover.name}")
    print(f"  People intel: {intel_pdf_path.name}")
    print(f"  Resume mods:  {resume_mods_path.name}")
    print(f"  Gap coverage: {gap['coverage_pct']}%")
    if resume_path and ats_result:
        print(f"  ATS score:    {ats_result['score']}/100  [{ats_result['grade']}]")
    if jd.apply_by_date:
        print(f"  Deadline:     {jd.apply_by_date}")
    print(f"{'='*60}")

    # Optional: full Executive Summary (deep company intel + council). Off by
    # default — it adds research calls (Perplexity, or ddgs + local LLM in OSS).
    try:
        want_exec = _tty_input(
            "\nGenerate full Executive Summary — deep intel + interview prep? (y/N): "
        ).lower()
    except (EOFError, OSError):
        want_exec = "n"
    if want_exec == "y":
        from pipeline.research.exec_intel import fetch_deep_intel, run_intel_council
        from pipeline.output.exec_summary import generate as gen_exec_summary
        intel_raw = fetch_deep_intel(company, role, jd.raw_text)
        intel_result = run_intel_council(intel_raw, jd.raw_text, company, role)
        exec_summary_path = gen_exec_summary(
            jd=jd, company=company, role=role,
            salary_intel=salary_intel, intel_result=intel_result,
            output_dir=app_folder / "research", slug=slug,
        )
        # Re-render resume mods with the intel reference sections populated.
        resume_mods_path = gen_resume_mods(
            jd=jd, company=company, role=role,
            tailoring_data=tailoring_data, salary_intel=salary_intel,
            output_dir=app_folder / "research", slug=slug,
            exec_intel_result=intel_result,
        )
        print(f"  Exec summary: {exec_summary_path.name}")

    try:
        answer = _tty_input("\nCopy deliverables to Google Drive? (y/n): ").lower()
    except (EOFError, OSError):
        answer = "n"

    if answer == "y":
        _copy_to_gdrive(company, role, resume_path, named_resume, cover_path, named_cover, intel_pdf_path, cover_txt_path, exec_summary_path, council_path=council_path, resume_mods_path=resume_mods_path, gdrive_target=args.gdrive_target or "")

    # Prompt to mark application as submitted
    try:
        submitted = _tty_input("\nDid you submit this application? Mark as applied? (y/n): ").lower()
        if submitted == "y":
            from pipeline.tracker.tracker import update_status
            update_status(company, role, "applied", "Submitted — marked via pipeline")
            print(f"  Tracker updated: applied")
    except (EOFError, OSError):
        pass
    print()


def _copy_to_gdrive(
    company: str,
    role: str,
    resume_path,
    named_resume,
    cover_path,
    named_cover,
    intel_pdf_path,
    cover_txt_path=None,
    exec_summary_path=None,
    council_path=None,
    resume_mods_path=None,
    gdrive_target: str = "",
) -> None:
    import shutil
    from utils.config import get

    if gdrive_target:
        # Exact path provided by pace (PDF was picked from GDrive) - use it directly
        company_folder = Path(gdrive_target)
    else:
        mount = Path(get("gdrive.mount_base", "")).expanduser()
        if not mount.exists():
            print(f"  Google Drive not mounted: {mount}")
            return
        applications = get("gdrive.applications_folder", "Job Search HQ/Applications (by company)")
        company_folder = mount / applications / company

    final_dir  = company_folder / "01_Final Documents"
    draft_dir  = company_folder / "02_Draft Documents"
    research_dir = company_folder / "03_Research"
    for d in (final_dir, draft_dir, research_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 01_Final Documents - clean filenames only
    final_files = [resume_path, cover_path]
    # 02_Draft Documents, slugged copies + plain-text cover letter
    draft_files = [named_resume, named_cover, cover_txt_path]
    # 03_Research, intel PDF, resume mods, exec summary, council report
    research_files = [intel_pdf_path, resume_mods_path, exec_summary_path, council_path]

    copied: list[str] = []
    for src, dest_dir in (
        [(f, final_dir)    for f in final_files] +
        [(f, draft_dir)    for f in draft_files] +
        [(f, research_dir) for f in research_files]
    ):
        if src and Path(src).exists():
            shutil.copy2(src, dest_dir / Path(src).name)
            copied.append(f"{dest_dir.name}/{Path(src).name}")

    print(f"  Copied {len(copied)} file(s) to Google Drive: {company_folder}")
    for f in copied:
        print(f"    {f}")


def _prompt_user(label: str) -> str:
    val = input(f"{label}: ").strip()
    if not val:
        logger.error("%s is required.", label)
        sys.exit(1)
    return val


def _save_assessment(research_dir: Path, assessment, context: str, slug: str) -> None:
    from pipeline.assessment.fit_assessor import FitAssessment
    lines = [
        f"FIT ASSESSMENT — {slug}",
        f"Verdict: {assessment.verdict}  ({assessment.overall_score}/10)",
        f"",
        assessment.summary,
        "",
    ]
    if assessment.hard_filter_triggered:
        lines += [f"HARD FILTER: {assessment.hard_filter_reason}", ""]
    dims = [
        ("Identity Alignment", assessment.identity_alignment),
        ("Scope / Level",      assessment.scope_level),
        ("Comp Alignment",     assessment.comp_alignment),
        ("Company Tier",       assessment.company_tier),
    ]
    for label, dim in dims:
        lines.append(f"{label}: {dim.score}/10 — {dim.rationale}")
    if context:
        lines += ["", "APPLICATION CONTEXT:", context]
    out = research_dir / f"assessment_{slug}.txt"
    out.write_text("\n".join(lines), encoding="utf-8")


def _make_slug(company: str, role: str) -> str:
    combined = f"{company}_{role}".lower()
    return re.sub(r"[^a-z0-9]+", "_", combined).strip("_")[:60]


def _extract_hiring_manager_name(intel_md: str) -> str:
    """Return first name of hiring manager from intel markdown, or '' if not found."""
    # Pattern: bold name followed by hiring manager signal on same or next line
    patterns = [
        r"\*\*([A-Z][a-z]+(?: [A-Z][a-z]+)+)\*\*[^\n]*[Hh]iring [Mm]anager",
        r"[Hh]iring [Mm]anager[:\s]+\*?\*?([A-Z][a-z]+(?: [A-Z][a-z]+)+)",
        r"[Hh]iring [Mm]anager[^:]*:\s*\*?\*?([A-Z][a-z]+(?: [A-Z][a-z]+)+)",
    ]
    for pat in patterns:
        m = re.search(pat, intel_md)
        if m:
            return m.group(1).split()[0]
    return ""


def _tty_input(prompt: str) -> str:
    """Read from /dev/tty directly to avoid buffered stdin from spinner threads."""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        with open("/dev/tty") as tty:
            return tty.readline().strip()
    except OSError:
        return input("").strip()


def _load_regen_context(app_folder: Path) -> tuple[dict, Path, str, str, str]:
    """Load tailoring JSON and metadata from an existing application folder."""
    import json as _json
    tailor_jsons = sorted((app_folder / "tailoring_json").glob("*.json"))
    if not tailor_jsons:
        print(f"No tailoring JSON found in {app_folder}/tailoring_json/")
        sys.exit(1)
    tailoring_path = tailor_jsons[0]
    with open(tailoring_path, encoding="utf-8") as f:
        td = _json.load(f)
    company = td.get("company", "")
    role = td.get("role", "")
    slug = _make_slug(company, role)
    return td, tailoring_path, company, role, slug


def _regen_jd(app_folder: Path):
    """Return a ParsedJD from the JD PDF in the app folder, or a minimal stub."""
    from pipeline.ingest.pdf_reader import extract_text
    from pipeline.ingest.jd_parser import parse, ParsedJD
    jd_pdfs = sorted((app_folder / "jd").glob("*.pdf"))
    if jd_pdfs:
        raw = extract_text(jd_pdfs[0])
        return parse(raw)
    return ParsedJD(raw_text="", company="", role="")


def _regen_intel(app_folder: Path) -> None:
    import shutil as _shutil
    from pipeline.research.perplexity_client import fetch_company_intel
    from pipeline.research.intel_generator import generate as generate_intel
    from pipeline.people_intel.outreach_extractor import extract as extract_outreach, save as save_outreach, display as display_outreach
    from pipeline.people_intel.pdf_renderer import run as render_intel

    _, _, company, role, slug = _load_regen_context(app_folder)
    jd = _regen_jd(app_folder)

    print(f"\nRegenerating people intel — {company} / {role}")
    perplexity_context = fetch_company_intel(company, role)
    if perplexity_context:
        print(f"  {len(perplexity_context):,} chars of live intel fetched")
    intel_md_path = generate_intel(
        jd, company, role, slug,
        output_dir=app_folder / "people_intel",
        perplexity_context=perplexity_context,
    )
    _intel_md_text = intel_md_path.read_text(encoding="utf-8")
    _outreach_msgs = extract_outreach(_intel_md_text)
    save_outreach(_outreach_msgs, app_folder / "people_intel", slug)
    display_outreach(_outreach_msgs)
    intel_pdf_path = render_intel(intel_md_path)
    print(f"\nPeople intel saved: {intel_md_path.name}")
    print(f"PDF rendered:       {intel_pdf_path.name}")


def _regen_cover(app_folder: Path) -> None:
    import json as _json
    import shutil as _shutil
    from pipeline.cover_letter.generate import run as run_cover, run_txt as run_cover_txt
    from pipeline.cover_letter.validator import validate as validate_cover, display_result as display_cover_result, save_result as save_cover_result

    td, tailoring_path, company, role, slug = _load_regen_context(app_folder)
    jd = _regen_jd(app_folder)

    print(f"\nRegenerating cover letter — {company} / {role}")
    cover_path = run_cover(tailoring_path, app_folder / "cover_letter")
    if cover_path:
        named_cover = cover_path.parent / _named_cover(slug)
        _shutil.copy2(cover_path, named_cover)
        run_cover_txt(tailoring_path, app_folder / "cover_letter")
        cl_text = td.get("cover_letter", "")
        if cl_text:
            cv_result = validate_cover(cl_text, jd.raw_text[:3000])
            display_cover_result(cv_result)
            save_cover_result(cv_result, app_folder / "research", slug)
        print(f"\nCover letter saved: {cover_path.name}")
        print(f"Named copy:         {named_cover.name}")


def _regen_resume(app_folder: Path) -> None:
    import shutil as _shutil
    from pipeline.tailoring.tailor import run as run_tailor
    from pipeline.tailoring.ats_checker import check as ats_check, display_report as ats_display, save_report as ats_save

    _, tailoring_path, company, role, slug = _load_regen_context(app_folder)

    print(f"\nRegenerating resume — {company} / {role}")
    resume_path = run_tailor(tailoring_path, app_folder / "resume")
    if resume_path:
        named_resume = resume_path.parent / _named_resume(slug)
        _shutil.copy2(resume_path, named_resume)
        ats_result = ats_check(resume_path)
        ats_display(ats_result)
        ats_save(ats_result, app_folder / "research", slug)
        print(f"\nResume saved: {resume_path.name}")
        print(f"Named copy:   {named_resume.name}")
        print(f"ATS score:    {ats_result['score']}/100  [{ats_result['grade']}]")


def _check_for_duplicate(company: str, role: str) -> None:
    """Warn if an entry for this company/role has already been applied to."""
    from pipeline.tracker.tracker import load, _find_entry, CLOSED_STATUSES
    entries = load()
    entry = _find_entry(entries, company, role)
    if not entry:
        return
    status = entry.get("status", "")
    if status in ("prompted",):
        return  # Pipeline rerun before applying, expected, no warning needed
    if status in CLOSED_STATUSES:
        print(f"\n  Note: Tracker shows a CLOSED entry for {company} / {role} (status: {status}).")
        print(f"  Continuing will regenerate materials — tracker entry will be updated.\n")
    else:
        print(f"\n  Note: Already tracking {company} / {role} with status '{status}'.")
        print(f"  Continuing will regenerate materials — tracker entry will be updated.\n")


def _run_council_regen(app_folder: Path) -> None:
    """Re-run council on an existing application folder and re-apply to resume + cover letter."""
    import shutil as _shutil
    from pipeline.tailoring.summary_council import run_council, save_report as save_council, display_result as display_council
    from pipeline.research.keyword_gap import compute_gap
    from utils.progress import spinner

    td, tailoring_path, company, role, slug = _load_regen_context(app_folder)
    jd = _regen_jd(app_folder)

    print(f"\n  Re-running council: {company} / {role}")

    pre_gap = compute_gap(jd.raw_text, td)
    missing_terms = pre_gap.get("missing", [])[:20]

    draft_summary = td.get("summary", "")
    draft_cover = td.get("cover_letter", "")

    with spinner("Running council"):
        result = run_council(
            summary=draft_summary,
            cover_letter=draft_cover,
            jd_text=jd.raw_text,
            company=company,
            role=role,
            missing_keywords=missing_terms,
        )

    agg = result.get("aggregation", {})
    patched = False
    if agg.get("final_summary"):
        td["summary"] = agg["final_summary"]
        patched = True
    if agg.get("final_cover_letter"):
        td["cover_letter"] = agg["final_cover_letter"]
        patched = True
    if patched:
        with open(tailoring_path, "w", encoding="utf-8") as _f:
            _json.dump(td, _f, indent=2)
        print("  Tailoring JSON patched with council revisions.")

    save_council(result, draft_summary, draft_cover, company, role,
                 output_dir=app_folder / "research", slug=slug)
    display_council(result, draft_summary, draft_cover)

    # Re-apply to resume and cover letter
    from pipeline.tailoring.tailor import run as run_tailor
    from pipeline.cover_letter.generate import run as run_cover, run_txt as run_cover_txt
    run_tailor(tailoring_path, app_folder / "resume")
    run_cover(tailoring_path, app_folder / "cover_letter")
    run_cover_txt(tailoring_path, app_folder / "cover_letter")
    print(f"  Resume and cover letter updated.")


if __name__ == "__main__":
    main()
