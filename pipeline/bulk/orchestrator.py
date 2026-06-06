"""Bulk batch orchestrator: wire discovery + lifecycle + process_jd.

`run_bulk(dry_run=, limit=)` is the single entry point behind
`python run.py --bulk`. It resolves the real Drive Que, discovers companies
and their JDs, and either:

  - DRY-RUN (read-only, no API): ingest each JD PDF locally to get its role,
    plan its destination folder, check whether it would be skipped (completion
    sentinel already present), and report the plan. Writes nothing.

  - LIVE: call `process_jd` non-interactively for each JD with fixed
    AutoDecisions (ghost-HIGH and HARD_PASS resolve to SKIP, exec summary off,
    Drive copy on). Drives the crash-safe sequence (copy-JD -> verify -> prune),
    adds an `in_que` tracker entry, and finalizes the company Que folder only
    after all its JDs are done. One CSV/HTML sync runs at the end.

Design contracts:
  - Hard-fail if the Drive mount or Que folder is missing.
  - Seed the collision `taken` set with existing on-disk subfolders before
    placing, so a cross-run collision stays safe.
  - Continue-on-error: one JD that raises or returns FAILURE never aborts the
    batch.
  - Ghost-HIGH / HARD_PASS -> SKIP, leave in Que, log loudly.
  - Skip-if-present: a JD whose dest already has the sentinel is skipped.
  - Tracker writes are batched: one `csv_sync.sync()` at the END.

No `print` here (project rule): the orchestrator logs; `run.py` prints.
"""

from __future__ import annotations

from pathlib import Path

from utils.logging import get_logger

from . import discovery, lifecycle, report

logger = get_logger(__name__)


def _ingest_role(jd_pdf: Path) -> str:
    """Parse just the role from a JD PDF locally (no API).

    Company always comes from the folder. Returns "" if the PDF can't be read
    or the role is not found by the local regex parser. Never calls
    Claude/Perplexity, safe for dry-run and cheap pre-ingest in the live path.
    """
    from pipeline.ingest.jd_parser import parse
    from pipeline.ingest.pdf_reader import extract_text

    try:
        raw_text = extract_text(jd_pdf)
        jd = parse(raw_text)
    except Exception as exc:  # noqa: BLE001 (any parse failure -> role unparsed)
        logger.warning("Could not ingest role from %s: %s", jd_pdf.name, exc)
        return ""
    return (jd.role or "").strip()


def _resolve_role(jd_pdf: Path) -> str:
    """Resolve a role for a JD with no API: local parse, else the filename.

    Prefers the local regex parser's role. Falls back to the filename when the
    parser returns empty (the real Que PDFs are "Reader View" exports). The
    resolved role is used for placement, the dry-run plan, and as
    `options.role` so the dest folder, `result.role`, and the tracker entry
    all agree. Never returns empty; filename fallback yields at least "Untitled".
    """
    return _ingest_role(jd_pdf) or discovery.role_from_filename(jd_pdf)


def _existing_subfolders(company_dir: Path) -> set[Path]:
    """On-disk per-job subfolders already under a company folder.

    Seeds the collision `taken` set so a new role that sanitizes to a name an
    earlier run already claimed gets a distinct subfolder. Returns empty set if
    the company folder doesn't exist yet.
    """
    if not company_dir.exists():
        return set()
    return {c for c in company_dir.iterdir() if c.is_dir() and not c.name.startswith(".")}


def _prune_empty_dest(dest_dir: Path, created_dest: bool) -> None:
    """Remove a dest dir we just created if the JD wrote nothing into it.

    Called on ghost/hard-pass SKIPs or failures where `process_jd` produced no
    deliverables. Only acts when `created_dest` is True and the dir is empty.
    Never touches a pre-existing or non-empty folder.
    """
    if not created_dest:
        return
    try:
        if dest_dir.is_dir() and not any(dest_dir.iterdir()):
            dest_dir.rmdir()
            logger.info("Removed empty dest folder (nothing written): %s", dest_dir)
    except OSError as exc:
        logger.debug("Could not remove empty dest %s: %s", dest_dir, exc)


def _backup_tracker_prebulk() -> None:
    """Snapshot the tracker JSON before the first live write of a batch.

    Called once per live `run_bulk`, never in dry-run. A backup failure is
    logged but must not abort the batch.
    """
    from pipeline.tracker.tracker import backup_tracker

    try:
        path = backup_tracker("prebulk")
        logger.info("Tracker backed up before bulk run: %s", path)
    except Exception as exc:  # noqa: BLE001 (backup failure must not lose the batch)
        logger.warning("Pre-bulk tracker backup failed (continuing): %s", exc)


def _resolve_que() -> Path:
    """Resolve the real Drive Que dir; raises if absent.

    Never silently falls back to outputs/: a missing mount or Que folder would
    lose the batch.
    """
    que = discovery.que_dir()
    if not que.exists() or not que.is_dir():
        raise FileNotFoundError(
            f"Que folder not found: {que}\n"
            "The Google Drive mount or the Que folder is missing. Mount Drive "
            "and create the Que folder, or fix gdrive.mount_base / "
            "gdrive.applications_folder / bulk.que_folder in config/config.yaml. "
            "Refusing to run (bulk never silently writes into outputs/)."
        )
    return que


def _bulk_options(company: str, role: str, dest_dir: Path):
    """Build non-interactive ProcessOptions for one live JD.

    Ghost-HIGH and HARD_PASS resolve to SKIP. `tracker_status="in_que"` so
    Stage 10 writes the entry as `in_que` directly; the downgrade guard still
    protects an already-advanced entry on re-run.
    """
    from pipeline.core import AutoDecisions, ProcessOptions

    return ProcessOptions(
        company=company,
        role=role,
        context="",
        gdrive_target=str(dest_dir),
        dry_run=False,
        interactive=False,
        tracker_status="in_que",
        auto_decisions=AutoDecisions(
            proceed_on_ghost_high=False,
            override_hard_pass=False,
            exec_summary=False,
            gdrive_copy=True,
        ),
    )


def _run_dry(que: Path, limit: int | None) -> report.BulkReport:
    """Read-only, no-API plan preview. Writes nothing."""
    disc = discovery.discover(que)
    apps_dir = que.parent  # Applications/ (Que lives directly under it)

    rep = report.BulkReport(
        dry_run=True,
        que_dir=que,
        companies_discovered=len(disc.queues),
        skipped_empty=list(disc.skipped_empty),
    )

    queues = disc.queues if limit is None else disc.queues[:limit]
    rep.companies_processed = len(queues)

    for cq in queues:
        company_dir = lifecycle.target_company_dir(apps_dir, cq.company)
        # Seed taken with existing on-disk subfolders so the planned dests for a
        # cross-run different-role collision match what the live run would pick.
        taken = _existing_subfolders(company_dir)
        for jd in cq.jds:
            role = _resolve_role(jd)
            placed = lifecycle.plan_placement(
                apps_dir, cq.company, role, multi=cq.is_multi, taken=taken
            )
            if cq.is_multi:
                taken = taken | {placed.dest_dir}
            would_skip = lifecycle.deliverables_present(placed.dest_dir)
            rep.add(
                report.JDResult(
                    company=cq.company,
                    jd_name=jd.name,
                    role=role,
                    kind=report.SKIP_PRESENT if would_skip else report.PLANNED,
                    dest_dir=placed.dest_dir,
                    subfoldered=placed.subfoldered,
                    would_skip=would_skip,
                )
            )
    return rep


def _process_company_live(
    cq: discovery.CompanyQueue,
    apps_dir: Path,
    process_jd,
    rep: report.BulkReport,
) -> None:
    """Process one company's JDs live; continue-on-error per JD.

    The Que folder is removed only after all JDs are done and every JD was
    pruned. Any failed/skipped JD leaves a PDF behind, keeping the folder.
    """
    company_dir = lifecycle.target_company_dir(apps_dir, cq.company)
    # seed taken from existing on-disk subfolders to avoid cross-run collisions
    taken = _existing_subfolders(company_dir)

    for jd in cq.jds:
        role = _resolve_role(jd)
        placed = lifecycle.plan_placement(
            apps_dir, cq.company, role, multi=cq.is_multi, taken=taken
        )
        if cq.is_multi:
            taken = taken | {placed.dest_dir}

        # skip if completion sentinel already exists at dest
        if lifecycle.deliverables_present(placed.dest_dir):
            logger.info(
                "SKIP (already present): %s / %s -> %s",
                cq.company, role or jd.name, placed.dest_dir,
            )
            rep.add(report.JDResult(
                company=cq.company, jd_name=jd.name, role=role,
                kind=report.SKIP_PRESENT, dest_dir=placed.dest_dir,
                subfoldered=placed.subfoldered, would_skip=True,
            ))
            # prune its Que source so the company folder can finalize
            lifecycle.prune_que_source(jd)
            continue

        # establish the destination (dirs only, no prune yet). record whether
        # we created the leaf dir so a SKIP/failure can remove only that empty
        # leaf, never a pre-existing folder.
        company_pre_existing = placed.company_dir.exists()
        lifecycle.establish_company_dir(placed.company_dir)
        if placed.subfoldered:
            # Multi-JD: the leaf is the per-job subfolder.
            created_dest = not placed.dest_dir.exists()
            placed.dest_dir.mkdir(parents=True, exist_ok=True)
        else:
            # single-JD: leaf is the company folder itself
            created_dest = not company_pre_existing

        # run the core pipeline; any raise is recorded, batch continues
        try:
            result = process_jd(jd, options=_bulk_options(cq.company, role, placed.dest_dir))
        except Exception as exc:  # noqa: BLE001 (one JD must not abort the batch)
            logger.error("FAIL: %s / %s raised: %s", cq.company, role or jd.name, exc)
            _prune_empty_dest(placed.dest_dir, created_dest)
            rep.add(report.JDResult(
                company=cq.company, jd_name=jd.name, role=role,
                kind=report.FAILED, dest_dir=placed.dest_dir,
                subfoldered=placed.subfoldered, detail=str(exc),
            ))
            continue

        # use the role process_jd resolved (may be better than our pre-parse)
        resolved_role = result.role or role

        if result.failed:
            logger.error("FAIL: %s / %s -> %s", cq.company, resolved_role, result.error)
            _prune_empty_dest(placed.dest_dir, created_dest)
            rep.add(report.JDResult(
                company=cq.company, jd_name=jd.name, role=resolved_role,
                kind=report.FAILED, dest_dir=placed.dest_dir,
                subfoldered=placed.subfoldered, detail=result.error,
            ))
            continue

        if result.skipped:
            kind = {
                "ghost_high": report.SKIP_GHOST,
                "hard_pass": report.SKIP_HARD_PASS,
            }.get(result.skip_reason, report.FAILED)
            logger.warning(
                "SKIP (%s): %s / %s, left in Que",
                result.skip_reason, cq.company, resolved_role,
            )
            # ghost/hard-pass wrote no deliverables; clean up the empty dest
            _prune_empty_dest(placed.dest_dir, created_dest)
            rep.add(report.JDResult(
                company=cq.company, jd_name=jd.name, role=resolved_role,
                kind=kind, dest_dir=placed.dest_dir,
                subfoldered=placed.subfoldered, detail=result.skip_reason,
            ))
            # leave the JD in the Que for review/re-run
            continue

        # success: crash-safe tail (copy -> verify -> prune)
        copied = lifecycle.copy_jd_into_dest(jd, placed.dest_dir)
        if lifecycle.verify_copy(jd, copied):
            lifecycle.prune_que_source(jd)
        else:
            logger.warning(
                "JD copy not verified, keeping Que source: %s -> %s", jd, copied
            )

        # tracker entry written by process_jd's Stage 10 add_entry
        logger.info("OK: %s / %s -> %s", cq.company, resolved_role, placed.dest_dir)
        rep.add(report.JDResult(
            company=cq.company, jd_name=jd.name, role=resolved_role,
            kind=report.GENERATED, dest_dir=placed.dest_dir,
            subfoldered=placed.subfoldered,
        ))

    # remove the Que folder only if every JD was pruned
    removed = lifecycle.finalize_que_removal(cq.folder)
    if removed:
        logger.info("Company Que folder finalized (removed): %s", cq.company)
    else:
        logger.info("Company Que folder kept (PARTIAL, JDs remain): %s", cq.company)


def run_bulk(*, dry_run: bool, limit: int | None) -> report.BulkReport:
    """Run the bulk batch; returns a `BulkReport` for the caller to print.

    `dry_run`: read-only, no-API plan preview (writes nothing).
    `limit`: max companies to process (smoke test or cost cap).
    """
    que = _resolve_que()
    logger.info("Bulk %s, Que: %s", "DRY RUN" if dry_run else "LIVE", que)

    if dry_run:
        return _run_dry(que, limit)

    from pipeline.core import process_jd

    disc = discovery.discover(que)
    apps_dir = que.parent

    rep = report.BulkReport(
        dry_run=False,
        que_dir=que,
        companies_discovered=len(disc.queues),
        skipped_empty=list(disc.skipped_empty),
    )
    if disc.skipped_empty:
        logger.info("Empty company folders skipped: %s", ", ".join(disc.skipped_empty))

    # snapshot tracker before first live write so a bad run is recoverable
    _backup_tracker_prebulk()

    queues = disc.queues if limit is None else disc.queues[:limit]
    rep.companies_processed = len(queues)
    logger.info(
        "Processing %d of %d companies (limit=%s)",
        len(queues), len(disc.queues), limit,
    )

    for cq in queues:
        _process_company_live(cq, apps_dir, process_jd, rep)

    # one CSV/HTML sync at the end, not per JD
    if any(r.kind == report.GENERATED for r in rep.results):
        try:
            from pipeline.tracker.csv_sync import sync as sync_tracker
            sync_tracker()
            rep.sync_ran = True
            logger.info("Tracker CSV + HTML synced (end of batch).")
        except Exception as exc:  # noqa: BLE001 (sync failure must not lose the batch)
            logger.warning("End-of-batch tracker sync skipped: %s", exc)
    else:
        logger.info("No JDs generated; skipping end-of-batch sync.")

    return rep
