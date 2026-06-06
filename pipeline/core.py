"""
Core JD-to-deliverables pipeline, extracted from run.py.

`process_jd` is the single callable that turns one job-description PDF into a
full application folder (assessment, tailoring JSON, council, people intel,
resume, cover letter, gap report, tracker entry, optional Google Drive copy).

Both callers share this function:
  - run.py's single-JD CLI wrapper passes ``interactive=True`` and supplies
    callbacks (``ProcessOptions.prompts``) that run the existing terminal
    prompts.
  - the bulk orchestrator passes ``interactive=False`` with fixed
    ``auto_decisions`` and never reaches the prompt callbacks.

The function never calls ``sys.exit``: every former mid-flow abort returns a
``ProcessResult`` carrying the outcome and reason. Callers decide what to print
and what exit code to use.

Cloud vs OSS routing is handled inside the called modules (e.g.
viability_checker delegates to viability_oss when PERPLEXITY_API_KEY is absent),
so this body is backend-agnostic.
"""

from __future__ import annotations

import json as _json
import shutil as _shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from utils.logging import add_file_handler, get_logger

logger = get_logger(__name__)


# ── Outcome constants ────────────────────────────────────────────────────────
SUCCESS = "success"
SKIP = "skip"
FAILURE = "failure"


@dataclass
class AutoDecisions:
    """How to resolve the mid-flow interactive decisions when not interactive.

    Used only when ``ProcessOptions.interactive`` is False (bulk path).
    """

    # Ghost-risk HIGH: when False, skip the JD rather than auto-proceed.
    proceed_on_ghost_high: bool = False
    # HARD_PASS verdict: when False, skip the JD rather than auto-override.
    override_hard_pass: bool = False
    # Hard-pass override reason text, used when override_hard_pass is True.
    hard_pass_reason: str = ""
    # Generate the deep Executive Summary (extra research + council cost).
    exec_summary: bool = False
    # Copy deliverables to Google Drive.
    gdrive_copy: bool = True


@dataclass
class ProcessPrompts:
    """Interactive decision callbacks owned by the caller (run.py wrapper).

    Each callback runs the corresponding terminal prompt and returns the
    answer. They are only invoked when ``ProcessOptions.interactive`` is True.
    """

    # (signals_text) -> bool: proceed despite HIGH ghost risk?
    ghost_proceed: Callable[[str], bool] | None = None
    # () -> (override: bool, reason: str): apply despite HARD_PASS?
    hard_pass_override: Callable[[], tuple[bool, str]] | None = None
    # (assessment) -> str: optional STRETCH context note / gut-check answers.
    stretch_context: Callable[[object], str] | None = None
    # (tailoring_path) -> None: review/edit gate before applying JSON.
    review_gate: Callable[[Path], None] | None = None
    # () -> bool: generate full Executive Summary?
    exec_summary: Callable[[], bool] | None = None
    # () -> bool: copy deliverables to Google Drive?
    gdrive_copy: Callable[[], bool] | None = None
    # (company, role) -> None: "did you submit?" prompt + status update.
    submit: Callable[[str, str], None] | None = None


@dataclass
class ProcessOptions:
    """Inputs and decision-routing for one ``process_jd`` call.

    ``tracker_status`` is the status a NEW tracker entry should be created with
    for this run. Default ``"prompted"`` preserves single-JD behavior. Bulk sets
    ``"in_que"`` so the SINGLE Stage 10 ``add_entry`` writes the right status
    directly (the downgrade guard still protects an existing advanced entry).
    """

    company: str = ""
    role: str = ""
    output_dir: str | None = None
    context: str = ""
    gdrive_target: str = ""
    dry_run: bool = False
    interactive: bool = True
    tracker_status: str = "prompted"
    auto_decisions: AutoDecisions = field(default_factory=AutoDecisions)
    prompts: ProcessPrompts = field(default_factory=ProcessPrompts)


@dataclass
class ProcessResult:
    """Outcome of one ``process_jd`` call.

    ``outcome`` is one of SUCCESS / SKIP / FAILURE. ``skip_reason`` is set for
    skips (ghost risk, hard pass, dry run); ``error`` is set for failures.
    """

    outcome: str
    company: str = ""
    role: str = ""
    app_folder: Path | None = None
    resume_path: Path | None = None
    named_resume: Path | None = None
    cover_path: Path | None = None
    named_cover: Path | None = None
    cover_txt_path: Path | None = None
    intel_pdf_path: Path | None = None
    resume_mods_path: Path | None = None
    exec_summary_path: Path | None = None
    council_path: Path | None = None
    gap: dict = field(default_factory=dict)
    ats_result: dict = field(default_factory=dict)
    fit_verdict: str = ""
    fit_score: float = 0.0
    apply_by_date: str = ""
    skip_reason: str = ""
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.outcome == SUCCESS

    @property
    def skipped(self) -> bool:
        return self.outcome == SKIP

    @property
    def failed(self) -> bool:
        return self.outcome == FAILURE


def _slug(company: str, role: str) -> str:
    from utils.naming import make_slug

    return make_slug(company, role)


def _named_resume(slug: str) -> str:
    """Slug-suffixed resume filename, derived from config (no personal data)."""
    from utils.config import get
    fn = get("person.resume_filename", "YourName_Resume.docx")
    base = fn.replace(".docx", "")
    return f"{base}_{slug}.docx"


def _named_cover(slug: str) -> str:
    """Slug-suffixed cover letter filename, derived from config."""
    from utils.config import get
    fn = get("person.resume_filename", "YourName_Resume.docx")
    base = fn.replace("_Resume.docx", "")
    return f"{base}_Cover_Letter_{slug}.docx"


def _save_assessment(research_dir: Path, assessment, context: str, slug: str) -> None:
    lines = [
        f"FIT ASSESSMENT — {slug}",
        f"Verdict: {assessment.verdict}  ({assessment.overall_score}/10)",
        "",
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


def _extract_hiring_manager_name(intel_md: str) -> str:
    """Return first name of hiring manager from intel markdown, or '' if not found."""
    import re

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


def process_jd(jd_pdf: Path, *, options: ProcessOptions) -> ProcessResult:
    """Run the full JD-to-deliverables pipeline for one JD PDF.

    Returns a ``ProcessResult`` instead of exiting. Mid-flow decisions are
    routed through ``options.prompts`` (when interactive) or
    ``options.auto_decisions`` (when not).
    """
    args_context = options.context
    interactive = options.interactive
    prompts = options.prompts
    auto = options.auto_decisions

    # Stage 1: Ingest
    from pipeline.ingest.jd_parser import parse
    from pipeline.ingest.pdf_reader import extract_text
    from utils.progress import done as show_done
    from utils.progress import spinner
    from utils.progress import stage as show_stage

    logger.info("Stage 1: Ingesting %s", jd_pdf.name)
    show_stage(1, f"Ingesting JD  —  {jd_pdf.name}")
    raw_text = extract_text(jd_pdf)
    jd = parse(raw_text)

    # Claude extraction — fires only when regex parse left company/role blank
    if not jd.company or not jd.role:
        from pipeline.ingest.jd_extractor import extract_company_role
        logger.info("Company/role not found by regex — calling Claude extractor")
        extracted_company, extracted_role = extract_company_role(raw_text)
        if extracted_company:
            jd.company = extracted_company
        if extracted_role:
            jd.role = extracted_role

    company = options.company or jd.company or _prompt_user("Company name")
    role = options.role or jd.role or _prompt_user("Role title")
    today = datetime.now().strftime("%Y-%m-%d")
    slug = _slug(company, role)

    # Duplicate detection — warn if already applied to this role
    _check_for_duplicate(company, role)

    # Stage 1.5: Job Viability Assessment — ghost job + freshness check before any API credits spent
    from pipeline.research.viability_checker import check as viability_check
    from pipeline.research.viability_checker import display as viability_display
    from pipeline.research.viability_checker import should_block as viability_blocks
    logger.info("Stage 1.5: Job viability check for %s / %s", company, role)
    show_stage(2, f"Job Viability  —  {company}")
    with spinner("Checking for ghost job signals"):
        viability = viability_check(company, role)
    viability_display(viability)

    if viability_blocks(viability):
        signals_text = "; ".join(viability.get("signals", [])) or "see signals above"
        print(f"  HIGH ghost job risk detected: {signals_text}")
        if args_context:
            print("  --context flag supplied — proceeding despite high ghost risk.")
        elif interactive:
            proceed = prompts.ghost_proceed(signals_text) if prompts.ghost_proceed else False
            if proceed is None:
                # Non-interactive terminal signalled by None — failure (exit 1 in wrapper)
                return ProcessResult(
                    FAILURE, company=company, role=role,
                    error="ghost_non_interactive",
                )
            if not proceed:
                return ProcessResult(
                    SKIP, company=company, role=role, skip_reason="ghost_declined",
                )
        elif not auto.proceed_on_ghost_high:
            return ProcessResult(
                SKIP, company=company, role=role, skip_reason="ghost_high",
            )

    # Salary intel — fetch market data if JD did not post comp
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
    from pipeline.assessment.fit_assessor import assess, display

    logger.info("Stage 2: Running fit assessment for %s / %s", company, role)
    show_stage(3, f"Fit Assessment  —  {company}")
    with spinner("Scoring role against vision profile"):
        assessment = assess(jd, company, role)
    display(assessment)
    if salary_intel:
        from pipeline.research.salary_intel import display_salary_intel
        display_salary_intel(salary_intel, original_salary or "")

    application_context = ""
    from utils.config import get as _cfg_get

    if assessment.verdict == "HARD_PASS":
        if args_context:
            application_context = f"OVERRIDE — Hard pass overridden by user.\nReason: {args_context}"
        elif interactive:
            override, context_reason = (
                prompts.hard_pass_override() if prompts.hard_pass_override else (None, "")
            )
            if override is None:
                # Non-interactive terminal signalled by None — failure (exit 1 in wrapper)
                return ProcessResult(
                    FAILURE, company=company, role=role,
                    fit_verdict=assessment.verdict, fit_score=assessment.overall_score,
                    error="hard_pass_non_interactive",
                )
            if not override:
                return ProcessResult(
                    SKIP, company=company, role=role,
                    fit_verdict=assessment.verdict, fit_score=assessment.overall_score,
                    skip_reason="hard_pass_declined",
                )
            application_context = f"OVERRIDE — Hard pass overridden by user.\nReason: {context_reason}"
        elif not auto.override_hard_pass:
            return ProcessResult(
                SKIP, company=company, role=role,
                fit_verdict=assessment.verdict, fit_score=assessment.overall_score,
                skip_reason="hard_pass",
            )
        else:
            reason = auto.hard_pass_reason or "Hard pass overridden — use standard JD-aligned framing."
            application_context = f"OVERRIDE — Hard pass overridden by user.\nReason: {reason}"

    elif assessment.verdict == "STRETCH":
        _default_stretch = _cfg_get("tailoring.default_stretch_context",
                                    "STRETCH — proceed with standard JD-aligned framing.")
        if args_context:
            application_context = f"STRETCH APPLICATION — User context:\n{args_context}"
        elif interactive:
            note = prompts.stretch_context(assessment) if prompts.stretch_context else ""
            application_context = (
                f"STRETCH APPLICATION — User gut-check answers:\n{note}"
                if note else _default_stretch
            )
        else:
            application_context = _default_stretch

    # Stage 3: Create application folder
    from pipeline.output.folder_manager import copy_file, create_application_folder
    from pipeline.research.prompt_builder import (
        build_people_intel_prompt,
        build_tailoring_prompt,
        write_prompts,
    )

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

    if options.dry_run:
        print("\nDRY RUN complete — folder created, no API calls made.")
        print(f"  {app_folder}")
        return ProcessResult(
            SKIP, company=company, role=role, app_folder=app_folder,
            fit_verdict=assessment.verdict, fit_score=assessment.overall_score,
            skip_reason="dry_run",
        )

    # Stage 4: Generate tailoring JSON → tailoring_json/
    from pipeline.research.tailor_generator import generate as generate_tailoring
    logger.info("Stage 4: Generating tailoring JSON via API")
    show_stage(5, "Tailoring JSON")
    with spinner("Generating JSON via Claude Sonnet"):
        _, tailoring_path = generate_tailoring(
            jd, company, role, slug, application_context,
            output_dir=app_folder / "tailoring_json",
        )

    # Stage 4a: Keyword gap + grammar pre-scan — both fed to council as context
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

    # Stage 4b: Council — reviews summary + cover letter, patches tailoring JSON with both
    council_path = None
    if _cfg_get("tailoring.council_enabled", True):
        from pipeline.tailoring.summary_council import display_result as display_council
        from pipeline.tailoring.summary_council import run_council
        from pipeline.tailoring.summary_council import save_report as save_council
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
        # Surface any council errors immediately — don't let them hide until document review
        _council_errors = council_result.get("errors", [])
        if _council_errors:
            print(f"\n  {'!'*60}")
            print(f"  COUNCIL ERRORS — {len(_council_errors)} model(s) failed.")
            print("  Resume and cover letter will use the original Claude draft.")
            print("  Fix: check your council provider keys / models")
            for _ce in _council_errors:
                print(f"    {_ce[:120]}")
            print(f"  {'!'*60}\n")

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
        logger.debug("Council skipped (disabled in config)")

    # Review gate — pause before applying JSON to DOCX so user can inspect/edit
    if _cfg_get("tailoring.review_gate", True) and interactive and prompts.review_gate:
        print(f"\n  Tailoring JSON: {tailoring_path}")
        prompts.review_gate(tailoring_path)

    # Stage 5: Generate people intel → people_intel/ (Perplexity enrichment if key available)
    from pipeline.research.intel_generator import generate as generate_intel
    from pipeline.research.perplexity_client import fetch_company_intel
    logger.info("Stage 5: Generating people intel")
    show_stage(6, f"People Intelligence  —  {company}")
    with spinner("Fetching live company intel"):
        perplexity_context = fetch_company_intel(company, role)
    if perplexity_context:
        print(f"  {len(perplexity_context):,} chars of live market intel")
    with spinner("Generating people intel"):
        intel_md_path = generate_intel(
            jd, company, role, slug,
            output_dir=app_folder / "people_intel",
            perplexity_context=perplexity_context,
        )
    # Extract outreach messages immediately so they're copy-paste ready
    from pipeline.people_intel.outreach_extractor import display as display_outreach
    from pipeline.people_intel.outreach_extractor import extract as extract_outreach
    from pipeline.people_intel.outreach_extractor import save as save_outreach
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

    # Stage 6: Apply tailoring → resume DOCX + named copy + ATS check
    from pipeline.tailoring.ats_checker import check as ats_check
    from pipeline.tailoring.ats_checker import display_report as ats_display
    from pipeline.tailoring.ats_checker import save_report as ats_save
    from pipeline.tailoring.tailor import run as run_tailor
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

    # Stage 7: Generate cover letter → cover_letter DOCX + named copy + txt + quality gate
    from pipeline.cover_letter.generate import run as run_cover
    from pipeline.cover_letter.generate import run_txt as run_cover_txt
    from pipeline.cover_letter.validator import display_result as display_cover_result
    from pipeline.cover_letter.validator import save_result as save_cover_result
    from pipeline.cover_letter.validator import validate as validate_cover
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
    from pipeline.research.keyword_gap import display_gap, save_gap_report
    logger.info("Stage 9: Running keyword gap analysis")
    show_stage(10, "Keyword Gap Analysis")
    with open(tailoring_path, encoding="utf-8") as _f:
        tailoring_data = _json.load(_f)
    gap = compute_gap(jd.raw_text, tailoring_data)
    display_gap(gap)
    save_gap_report(gap, company, role, app_folder / "research")

    # Stage 9b: Resume Modifications PDF (always runs — fast, no extra API calls)
    from pipeline.output.resume_mods import generate as gen_resume_mods
    logger.info("Stage 9b: Resume modifications PDF")
    show_stage(11, "Resume Modifications PDF")
    exec_summary_path = None
    intel_result: dict = {}
    resume_mods_path = gen_resume_mods(
        jd=jd,
        company=company,
        role=role,
        tailoring_data=tailoring_data,
        salary_intel=salary_intel,
        output_dir=app_folder / "research",
        slug=slug,
        exec_intel_result=None,  # populated later if user opts into exec summary
    )

    # Stage 10: Tracker (upsert — no duplicate if pipeline reruns)
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
        status=options.tracker_status,
    )

    # Stage 11: Google Drive — prompt user
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
    if resume_mods_path:
        print(f"  Resume mods:  {resume_mods_path.name}")
    print(f"  Gap coverage: {gap['coverage_pct']}%")
    if resume_path and ats_result:
        print(f"  ATS score:    {ats_result['score']}/100  [{ats_result['grade']}]")
    if jd.apply_by_date:
        print(f"  Deadline:     {jd.apply_by_date}")
    print(f"{'='*60}")

    # Optional: full Executive Summary (deep intel + council — adds research calls)
    if interactive:
        run_exec = prompts.exec_summary() if prompts.exec_summary else False
    else:
        run_exec = auto.exec_summary

    if run_exec:
        from pipeline.output.exec_summary import generate as gen_exec_summary
        from pipeline.research.exec_intel import fetch_deep_intel, run_intel_council
        show_stage(12, "Executive Summary (deep intel + council)")
        with spinner("Fetching deep company intel (5 research angles)"):
            exec_intel_raw = fetch_deep_intel(company, role, jd.raw_text)
        good = sum(1 for v in exec_intel_raw.values() if v and not v.startswith("[ERROR:"))
        print(f"  {good}/5 intel angles returned content")
        with spinner("Running intel council synthesis"):
            intel_result = run_intel_council(exec_intel_raw, jd.raw_text, company, role)
        if intel_result.get("errors"):
            print(f"  ⚠  Council: {len(intel_result['errors'])} model(s) failed")
        exec_summary_path = gen_exec_summary(
            jd=jd, company=company, role=role,
            salary_intel=salary_intel, intel_result=intel_result,
            output_dir=app_folder / "research", slug=slug,
        )
        print(f"  Exec summary: {exec_summary_path.name}")
        # Regenerate resume_mods with intel now available
        resume_mods_path = gen_resume_mods(
            jd=jd, company=company, role=role,
            tailoring_data=tailoring_data, salary_intel=salary_intel,
            output_dir=app_folder / "research", slug=slug,
            exec_intel_result=intel_result,
        )
        print("  Resume mods updated with intel sections.")

    if interactive:
        do_copy = prompts.gdrive_copy() if prompts.gdrive_copy else False
    else:
        do_copy = auto.gdrive_copy

    if do_copy:
        _copy_to_gdrive(
            company, role, resume_path, named_resume, cover_path, named_cover,
            intel_pdf_path, cover_txt_path, exec_summary_path,
            council_path=council_path, resume_mods_path=resume_mods_path,
            gdrive_target=options.gdrive_target or "",
        )

    # Prompt to mark application as submitted (interactive single-JD only)
    if interactive and prompts.submit:
        prompts.submit(company, role)

    return ProcessResult(
        SUCCESS,
        company=company,
        role=role,
        app_folder=app_folder,
        resume_path=resume_path,
        named_resume=named_resume,
        cover_path=cover_path,
        named_cover=named_cover,
        cover_txt_path=cover_txt_path,
        intel_pdf_path=intel_pdf_path,
        resume_mods_path=resume_mods_path,
        exec_summary_path=exec_summary_path,
        council_path=council_path,
        gap=gap,
        ats_result=ats_result,
        fit_verdict=assessment.verdict,
        fit_score=assessment.overall_score,
        apply_by_date=jd.apply_by_date or "",
    )


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
        # Exact path provided by the caller (PDF was picked from GDrive) — use it directly
        company_folder = Path(gdrive_target)
    else:
        mount = Path(get("gdrive.mount_base", "")).expanduser()
        if not mount.exists():
            print(f"  Google Drive not mounted: {mount}")
            return
        applications = get("gdrive.applications_folder", "Job Search/Applications")
        company_folder = mount / applications / company

    final_dir  = company_folder / "01_Final Documents"
    draft_dir  = company_folder / "02_Draft Documents"
    research_dir = company_folder / "03_Research"
    for d in (final_dir, draft_dir, research_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 01_Final Documents — clean filenames only
    final_files = [resume_path, cover_path]
    # 02_Draft Documents — slugged copies + plain-text cover letter
    draft_files = [named_resume, named_cover, cover_txt_path]
    # 03_Research — intel PDF, exec summary, resume modifications, council report
    research_files = [intel_pdf_path, exec_summary_path, resume_mods_path, council_path]

    copied: list[str] = []
    for src, dest_dir in (
        [(f, final_dir)    for f in final_files] +
        [(f, draft_dir)    for f in draft_files] +
        [(f, research_dir) for f in research_files]
    ):
        if src and Path(src).exists():
            shutil.copy2(src, dest_dir / Path(src).name)
            copied.append(f"{dest_dir.name}/{Path(src).name}")

    # Completion sentinel: written ONLY after every deliverable above has been
    # copied. bulk's `deliverables_present` keys on this (not on the resume
    # alone), so a crash mid-copy is never mistaken for a finished job and the
    # JD is correctly re-processed on the next run.
    try:
        from pipeline.bulk.lifecycle import mark_deliverables_complete
        mark_deliverables_complete(company_folder)
    except ImportError:
        # bulk module not present — write the configured sentinel directly.
        sentinel = get("bulk.sentinel", ".forge_complete")
        (company_folder / sentinel).write_text("", encoding="utf-8")

    print(f"  Copied {len(copied)} file(s) to Google Drive: {company_folder}")
    for f in copied:
        print(f"    {f}")


def _prompt_user(label: str) -> str:
    import sys

    val = input(f"{label}: ").strip()
    if not val:
        logger.error("%s is required.", label)
        sys.exit(1)
    return val


def _check_for_duplicate(company: str, role: str) -> None:
    """Warn if an entry for this company/role has already been applied to."""
    from pipeline.tracker.tracker import CLOSED_STATUSES, _find_entry, load
    entries = load()
    entry = _find_entry(entries, company, role)
    if not entry:
        return
    status = entry.get("status", "")
    if status in ("prompted",):
        return  # Pipeline rerun before applying — expected, no warning needed
    if status in CLOSED_STATUSES:
        print(f"\n  Note: Tracker shows a CLOSED entry for {company} / {role} (status: {status}).")
        print("  Continuing will regenerate materials — tracker entry will be updated.\n")
    else:
        print(f"\n  Note: Already tracking {company} / {role} with status '{status}'.")
        print("  Continuing will regenerate materials — tracker entry will be updated.\n")
