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
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from utils.config import get as _cfg
from utils.logging import get_logger

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
    group.add_argument("--regen-exec-summary", metavar="APP_FOLDER", help="Regenerate the executive summary (deep intel + council) for an existing application folder")
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
        "--bulk",
        action="store_true",
        help="Batch-process every company/JD in the Drive Que (use --dry-run for a read-only plan)",
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
    p.add_argument("--bulk-limit", type=int, default=None, metavar="N", help="Bulk: process at most N companies")
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
        help="Pre-supply application context (gut-check answers or override reason); skips interactive prompt",
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
                print(f"  {u['company']} / {u['role']}: {u['days_silent']}d silent (was: {u['status']})")
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
                  f"({g['count']} copies, remove {g['removed']}, keep status '{g['kept_status']}')")
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
        from pipeline.tracker.followup import display as display_followup
        from pipeline.tracker.followup import draft
        from pipeline.tracker.followup import save as save_followup
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

        from pipeline.tracker.digest import display as disp_digest
        from pipeline.tracker.digest import generate as gen_digest
        from pipeline.tracker.digest import save_html as save_digest
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
            print("  (email skipped; check GMAIL_APP_PASSWORD in .env)")
        return

    # Bulk: batch-process the Drive Que
    if args.bulk:
        from pipeline.bulk.orchestrator import run_bulk
        from pipeline.bulk.report import format_report
        try:
            report = run_bulk(dry_run=args.dry_run, limit=args.bulk_limit)
        except FileNotFoundError as exc:
            logger.error("%s", exc)
            sys.exit(1)
        print(format_report(report))
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

    # Regen: executive summary (deep intel + council)
    if args.regen_exec_summary:
        _regen_exec_summary(Path(args.regen_exec_summary))
        return

    # Re-run council on existing application folder
    if args.council:
        _run_council_regen(Path(args.council))
        return

    # Interview prep
    if args.prep:
        import subprocess

        from pipeline.interview.prep_generator import run as run_prep
        from pipeline.tracker.tracker import _find_entry, load
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

        from pipeline.cover_letter.generate import run as run_cover
        from pipeline.cover_letter.validator import display_result as display_cover_result
        from pipeline.cover_letter.validator import save_result as save_cover_result
        from pipeline.cover_letter.validator import validate as validate_cover
        from pipeline.tailoring.ats_checker import check as ats_check
        from pipeline.tailoring.ats_checker import display_report as ats_display
        from pipeline.tailoring.ats_checker import save_report as ats_save
        from pipeline.tailoring.tailor import run as run_tailor

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
    _run_full_pipeline(jd_pdf, args)


# Interactive prompt callbacks for the single-JD pipeline (pipeline.core routes
# each mid-flow decision through one of these when interactive=True).

def _ghost_proceed_prompt(signals_text: str):
    """Proceed despite HIGH ghost risk? Returns bool, or None if non-interactive."""
    try:
        print("\n" + "\u2500" * 60)
        answer = _tty_input("  Ghost job risk is HIGH. Proceed anyway? (y/n): ").lower()
        print("\u2500" * 60)
        return answer == "y"
    except (EOFError, OSError):
        return None


def _hard_pass_override_prompt():
    """Apply despite HARD_PASS? Returns (override, reason), or (None, "") if non-interactive."""
    try:
        print("\n" + "\u2500" * 60)
        override = _tty_input("  HARD PASS \u2014 Apply anyway? (y/n): ").lower()
        print("\u2500" * 60)
        if override != "y":
            return False, ""
        print("\nGot it. Tell me why you're applying despite the hard pass.")
        print("(Your answer shapes the cover letter tone and positioning.)\n")
        reason = _tty_input("> ")
        return True, reason
    except (EOFError, OSError):
        return None, ""


def _stretch_context_prompt(assessment) -> str:
    """STRETCH: run the gut-check questionnaire and return the raw answers."""
    from pipeline.assessment.fit_assessor import prompt_gut_check
    print("Stretch fit detected. Answer the gut-check questions before we generate materials.\n")
    try:
        return prompt_gut_check(assessment)
    except EOFError:
        print("\nNon-interactive terminal \u2014 use --context to supply your answers.")
        sys.exit(1)


def _review_gate_prompt(tailoring_path: Path) -> None:
    """Review/edit the tailoring JSON before it is applied to the resume DOCX."""
    try:
        answer = _tty_input("  Review/edit tailoring JSON before applying to resume? (y/n): ").strip().lower()
        if answer == "y":
            import subprocess as _sp
            _sp.run(["open", str(tailoring_path)], check=False)
            _tty_input("  Edit the file, save it, then press Enter to continue...")
            print("  Continuing with updated JSON.")
    except (EOFError, OSError):
        pass


def _exec_summary_prompt() -> bool:
    """Generate the full Executive Summary (deep intel + council)?"""
    try:
        want = _tty_input(
            "\nGenerate full Executive Summary \u2014 deep intel + interview prep? (y/N): "
        ).lower()
    except (EOFError, OSError):
        want = "n"
    return want == "y"


def _gdrive_copy_prompt() -> bool:
    """Copy deliverables to Google Drive?"""
    try:
        answer = _tty_input("\nCopy deliverables to Google Drive? (y/n): ").lower()
    except (EOFError, OSError):
        answer = "n"
    return answer == "y"


def _submit_prompt(company: str, role: str) -> None:
    """'Did you submit?' prompt; updates tracker status to applied."""
    try:
        submitted = _tty_input("\nDid you submit this application? Mark as applied? (y/n): ").lower()
        if submitted == "y":
            from pipeline.tracker.tracker import update_status
            update_status(company, role, "applied", "Submitted \u2014 marked via pipeline")
            print("  Tracker updated: applied")
    except (EOFError, OSError):
        pass


def _run_full_pipeline(jd_pdf: Path, args) -> None:
    """Single-JD CLI wrapper around pipeline.core.process_jd.

    Gathers interactive answers via callbacks, hands them to process_jd, then
    maps the ProcessResult back to the old print/exit behavior.
    """
    from pipeline.core import ProcessOptions, ProcessPrompts, process_jd

    options = ProcessOptions(
        company=args.company or "",
        role=args.role or "",
        output_dir=args.output_dir,
        context=args.context,
        gdrive_target=args.gdrive_target or "",
        dry_run=args.dry_run,
        interactive=True,
        prompts=ProcessPrompts(
            ghost_proceed=_ghost_proceed_prompt,
            hard_pass_override=_hard_pass_override_prompt,
            stretch_context=_stretch_context_prompt,
            review_gate=_review_gate_prompt,
            exec_summary=_exec_summary_prompt,
            gdrive_copy=_gdrive_copy_prompt,
            submit=_submit_prompt,
        ),
    )

    result = process_jd(jd_pdf, options=options)

    if result.skipped:
        if result.skip_reason in ("ghost_declined", "hard_pass_declined"):
            print("Stopped. No files written.")
            sys.exit(0)
        if result.skip_reason == "dry_run":
            sys.exit(0)
        return
    if result.failed:
        if result.error == "ghost_non_interactive":
            print("\nNon-interactive terminal \u2014 use --context to override ghost job block.")
            sys.exit(1)
        if result.error == "hard_pass_non_interactive":
            print("\nNon-interactive terminal \u2014 use --context to supply override reason.")
            sys.exit(1)
        sys.exit(1)

    # Success \u2014 keep the CSV + HTML dashboard in step with the JSON just written.
    try:
        from pipeline.tracker.csv_sync import sync as sync_tracker
        sync_result = sync_tracker()
        print(f"  Tracker synced: {sync_result['csv'].name}, {sync_result['html'].name}")
    except Exception as exc:
        logger.warning("Tracker sync skipped: %s", exc)
    print()
def _make_slug(company: str, role: str) -> str:
    combined = f"{company}_{role}".lower()
    return re.sub(r"[^a-z0-9]+", "_", combined).strip("_")[:60]
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
    from pipeline.ingest.jd_parser import ParsedJD, parse
    from pipeline.ingest.pdf_reader import extract_text
    jd_pdfs = sorted((app_folder / "jd").glob("*.pdf"))
    if jd_pdfs:
        raw = extract_text(jd_pdfs[0])
        return parse(raw)
    return ParsedJD(raw_text="", company="", role="")


def _regen_intel(app_folder: Path) -> None:
    from pipeline.people_intel.outreach_extractor import display as display_outreach
    from pipeline.people_intel.outreach_extractor import extract as extract_outreach
    from pipeline.people_intel.outreach_extractor import save as save_outreach
    from pipeline.people_intel.pdf_renderer import run as render_intel
    from pipeline.research.intel_generator import generate as generate_intel
    from pipeline.research.perplexity_client import fetch_company_intel

    _, _, company, role, slug = _load_regen_context(app_folder)
    jd = _regen_jd(app_folder)

    print(f"\nRegenerating people intel: {company} / {role}")
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
    import shutil as _shutil

    from pipeline.cover_letter.generate import run as run_cover
    from pipeline.cover_letter.generate import run_txt as run_cover_txt
    from pipeline.cover_letter.validator import display_result as display_cover_result
    from pipeline.cover_letter.validator import save_result as save_cover_result
    from pipeline.cover_letter.validator import validate as validate_cover

    td, tailoring_path, company, role, slug = _load_regen_context(app_folder)
    jd = _regen_jd(app_folder)

    print(f"\nRegenerating cover letter: {company} / {role}")
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

    from pipeline.tailoring.ats_checker import check as ats_check
    from pipeline.tailoring.ats_checker import display_report as ats_display
    from pipeline.tailoring.ats_checker import save_report as ats_save
    from pipeline.tailoring.tailor import run as run_tailor

    _, tailoring_path, company, role, slug = _load_regen_context(app_folder)

    print(f"\nRegenerating resume: {company} / {role}")
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
def _regen_exec_summary(app_folder: Path) -> None:
    """Regenerate the executive summary (deep intel + council) for an existing folder.

    Fetches deep company intel (Perplexity, or ddgs + local LLM in OSS mode),
    runs the intel council, writes the exec summary PDF, then re-renders the
    resume modifications PDF with the intel reference sections populated.
    """
    from pipeline.output.exec_summary import generate as gen_exec_summary
    from pipeline.output.resume_mods import generate as gen_resume_mods
    from pipeline.research.exec_intel import fetch_deep_intel, run_intel_council
    from pipeline.research.salary_intel import fetch_salary_intel

    td, _tailoring_path, company, role, slug = _load_regen_context(app_folder)
    jd = _regen_jd(app_folder)

    print(f"\nRegenerating executive summary: {company} / {role}")
    salary_intel = {}
    if not jd.salary_range or jd.salary_range in ("Not listed", "Not specified"):
        salary_intel = fetch_salary_intel(company, role, jd.location or "")

    intel_raw = fetch_deep_intel(company, role, jd.raw_text)
    good = sum(1 for v in intel_raw.values() if v and not v.startswith("[ERROR:"))
    print(f"  {good}/5 intel angles returned content")
    intel_result = run_intel_council(intel_raw, jd.raw_text, company, role)

    research_dir = app_folder / "research"
    exec_path = gen_exec_summary(
        jd=jd, company=company, role=role,
        salary_intel=salary_intel, intel_result=intel_result,
        output_dir=research_dir, slug=slug,
    )
    mods_path = gen_resume_mods(
        jd=jd, company=company, role=role,
        tailoring_data=td, salary_intel=salary_intel,
        output_dir=research_dir, slug=slug,
        exec_intel_result=intel_result,
    )
    print(f"\nExec summary saved: {exec_path.name}")
    print(f"Resume mods updated: {mods_path.name}")


def _run_council_regen(app_folder: Path) -> None:
    """Re-run council on an existing application folder and re-apply to resume + cover letter."""
    from pipeline.research.keyword_gap import compute_gap
    from pipeline.tailoring.summary_council import display_result as display_council
    from pipeline.tailoring.summary_council import run_council
    from pipeline.tailoring.summary_council import save_report as save_council
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
    from pipeline.cover_letter.generate import run as run_cover
    from pipeline.cover_letter.generate import run_txt as run_cover_txt
    from pipeline.tailoring.tailor import run as run_tailor
    run_tailor(tailoring_path, app_folder / "resume")
    run_cover(tailoring_path, app_folder / "cover_letter")
    run_cover_txt(tailoring_path, app_folder / "cover_letter")
    print("  Resume and cover letter updated.")


if __name__ == "__main__":
    main()
