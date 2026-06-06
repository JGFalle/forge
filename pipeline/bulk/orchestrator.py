"""Bulk batch orchestrator: wire discovery + lifecycle + process_jd together.

`run_bulk(dry_run=, limit=)` is the single entry point behind
`python run.py --bulk`. It resolves the real Drive Que, discovers companies and
their JDs, and either:

  - DRY-RUN (read-only, API-FREE): ingest each JD PDF LOCALLY to get its role
    (no Claude, no Perplexity), plan its destination folder, check whether it
    would be skipped (completion sentinel already present), and report the plan.
    Writes NOTHING: no folders, no tracker, no Drive, no API.

  - LIVE: for each JD call `process_jd` non-interactively with fixed
    AutoDecisions (ghost-HIGH and HARD_PASS resolve to SKIP, exec summary off,
    Drive copy on to the resolved per-JD dest). It then drives the crash-safe
    sequence (copy-JD-into-dest -> verify -> prune), adds an `in_que` tracker
    entry, and finalizes the company's Que folder removal only after all its JDs
    are done. One CSV/HTML sync runs at the very end.

Design contracts honored:
  - Hard-fail fast if the Drive mount / Que folder is missing (never silently
    fall back to outputs/).
  - Seed the collision `taken` set with the company's EXISTING on-disk
    subfolders before placing, so a cross-run different-role collision into an
    existing company folder stays safe.
  - Continue-on-error: one JD that raises or returns FAILURE never aborts the
    batch.
  - Ghost-HIGH / HARD_PASS -> SKIP, leave in Que, log loudly.
  - Skip-if-present: a JD whose dest already has the completion sentinel is
    skipped and reported.
  - Tracker writes are batched: one `csv_sync.sync()` at the END (none in
    dry-run).

No `print` here (project rule): the orchestrator logs; `run.py` prints the
formatted report.
"""

from __future__ import annotations

from pathlib import Path

from utils.logging import get_logger

from . import discovery, lifecycle, report

logger = get_logger(__name__)


def _ingest_role(jd_pdf: Path) -> str:
    """Parse just the ROLE from a JD PDF locally (no API).

    Company always comes from the folder; only the role is parsed here. Returns
    "" if the PDF cannot be read or the role is not found by the local regex
    parser. NEVER calls Claude/Perplexity, so it is safe for the API-free
    dry-run and for the cheap pre-ingest in the live path.
    """
    from pipeline.ingest.jd_parser import parse
    from pipeline.ingest.pdf_reader import extract_text

    try:
        raw_text = extract_text(jd_pdf)
        jd = parse(raw_text)
    except Exception as exc:  # noqa: BLE001 — any parse failure -> role unparsed
        logger.warning("Could not ingest role from %s: %s", jd_pdf.name, exc)
        return ""
    return (jd.role or "").strip()


def _resolve_role(jd_pdf: Path) -> str:
    """Resolve a role for a JD with NO API: local parse, else the filename.

    Prefers the local regex parser's role. When that is empty (the real Que PDFs
    are "Reader View" exports the parser extracts nothing from), falls back to
    the filename-derived role. This one resolved role is used for placement, the
    dry-run plan, AND is passed as `options.role` so `process_jd`'s
    `options.role or jd.role` chain short-circuits to it: the Drive dest folder,
    `result.role`, the local outputs folder, and the tracker entry then ALL agree
    on a single readable role. Never returns empty (filename fallback yields at
    least "Untitled").
    """
    return _ingest_role(jd_pdf) or discovery.role_from_filename(jd_pdf)


def _existing_subfolders(company_dir: Path) -> set[Path]:
    """On-disk per-job subfolders already under a company folder.

    Used to SEED the collision `taken` set so a new, DIFFERENT role that
    sanitizes to a name an earlier run already claimed gets a distinct subfolder
    instead of colliding into it. Returns an empty set if the company folder does
    not exist yet.
    """
    if not company_dir.exists():
        return set()
    return {c for c in company_dir.iterdir() if c.is_dir() and not c.name.startswith(".")}


def _prune_empty_dest(dest_dir: Path, created_dest: bool) -> None:
    """Remove a dest dir WE just created if a JD wrote nothing into it.

    Called on a ghost/hard-pass SKIP or a failure where `process_jd` produced no
    deliverables. Only acts when `created_dest` is True (we made this leaf this
    run) AND the dir is genuinely empty: never `rmtree`s a non-empty or
    pre-existing folder (MERGE-safe). This keeps Applications free of empty leaf
    folders for reqs the North Star says to walk away from, without ever touching
    a pre-existing company folder or another JD's work.
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
    """Snapshot the tracker JSON before the FIRST live write of a batch.

    Copies the tracker to a timestamped `application_tracker.prebulk_<stamp>.json`
    beside it (reuses the shared `tracker.backup_tracker` helper). Called exactly
    once per live `run_bulk` and NEVER in dry-run, so a live batch creates exactly
    one prebulk backup and a dry-run creates none. A backup failure must not abort
    the batch, but it is logged loudly so the user notices the safety net failed.
    """
    from pipeline.tracker.tracker import backup_tracker

    try:
        path = backup_tracker("prebulk")
        logger.info("Tracker backed up before bulk run: %s", path)
    except Exception as exc:  # noqa: BLE001 — backup failure must not lose the batch
        logger.warning("Pre-bulk tracker backup failed (continuing): %s", exc)


def _resolve_que() -> Path:
    """Resolve the real Drive Que dir, hard-failing fast if it is absent.

    Never silently falls back to outputs/: a missing mount/Que means the batch
    would be lost, so this raises with a clear, actionable message.
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

    AutoDecisions are fixed for bulk: ghost-HIGH and HARD_PASS resolve to SKIP
    (not override), exec summary OFF, Drive copy ON to the resolved per-JD dest.
    Company comes from the folder; role from local ingest. `context=""` so the
    ghost/hard-pass auto-override paths are never taken.

    `tracker_status="in_que"` makes process_jd's SINGLE Stage 10 `add_entry`
    write the entry as `in_que` directly. The Phase 1 downgrade guard still
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
    """Read-only, API-free plan preview. Writes nothing."""
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
    """Process one company's JDs live. Continue-on-error per JD.

    The company's Que folder is finalized (removed) only after ALL its JDs are
    done AND every JD was pruned (any failed/skipped JD leaves a PDF behind, so
    `finalize_que_removal`'s hidden-only whitelist keeps the folder).
    """
    company_dir = lifecycle.target_company_dir(apps_dir, cq.company)
    # Seed taken from existing on-disk subfolders so a new different role can't
    # collide into a folder an earlier run already placed.
    taken = _existing_subfolders(company_dir)

    for jd in cq.jds:
        role = _resolve_role(jd)
        placed = lifecycle.plan_placement(
            apps_dir, cq.company, role, multi=cq.is_multi, taken=taken
        )
        if cq.is_multi:
            taken = taken | {placed.dest_dir}

        # Skip-if-present: completion sentinel already at dest.
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
            # An already-present JD is done; prune its Que source so the company
            # folder can finalize.
            lifecycle.prune_que_source(jd)
            continue

        # Establish the destination (crash-safe: dirs only, no prune yet). Record
        # whether WE created the leaf dest dir (vs. it pre-existing) so a
        # SKIP/failure that writes nothing can remove only the empty leaf we made,
        # never a pre-existing company/subfolder.
        company_pre_existing = placed.company_dir.exists()
        lifecycle.establish_company_dir(placed.company_dir)
        if placed.subfoldered:
            # Multi-JD: the leaf is the per-job subfolder.
            created_dest = not placed.dest_dir.exists()
            placed.dest_dir.mkdir(parents=True, exist_ok=True)
        else:
            # Single-JD: the leaf IS the company folder; only ours to remove if we
            # just created it this run.
            created_dest = not company_pre_existing

        # Run the core pipeline. Continue-on-error: any raise is recorded.
        try:
            result = process_jd(jd, options=_bulk_options(cq.company, role, placed.dest_dir))
        except Exception as exc:  # noqa: BLE001 — one JD must not abort the batch
            logger.error("FAIL: %s / %s raised: %s", cq.company, role or jd.name, exc)
            _prune_empty_dest(placed.dest_dir, created_dest)
            rep.add(report.JDResult(
                company=cq.company, jd_name=jd.name, role=role,
                kind=report.FAILED, dest_dir=placed.dest_dir,
                subfoldered=placed.subfoldered, detail=str(exc),
            ))
            continue

        # Use the role process_jd resolved (it may have parsed a better one).
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
                "SKIP (%s): %s / %s — left in Que",
                result.skip_reason, cq.company, resolved_role,
            )
            # Ghost/hard-pass wrote no deliverables: don't strand an empty dest
            # folder in Applications for a req the North Star says to walk from.
            _prune_empty_dest(placed.dest_dir, created_dest)
            rep.add(report.JDResult(
                company=cq.company, jd_name=jd.name, role=resolved_role,
                kind=kind, dest_dir=placed.dest_dir,
                subfoldered=placed.subfoldered, detail=result.skip_reason,
            ))
            # Ghost/hard-pass: leave the JD in the Que (NOT pruned), so the
            # company folder is kept for review/re-run.
            continue

        # SUCCESS: deliverables were written by process_jd. Now the crash-safe
        # tail: copy the JD into dest -> verify -> prune the Que source.
        copied = lifecycle.copy_jd_into_dest(jd, placed.dest_dir)
        if lifecycle.verify_copy(jd, copied):
            lifecycle.prune_que_source(jd)
        else:
            logger.warning(
                "JD copy not verified, keeping Que source: %s -> %s", jd, copied
            )

        # The `in_que` tracker entry was written by process_jd itself (its single
        # Stage 10 add_entry, driven by ProcessOptions.tracker_status="in_que").
        logger.info("OK: %s / %s -> %s", cq.company, resolved_role, placed.dest_dir)
        rep.add(report.JDResult(
            company=cq.company, jd_name=jd.name, role=resolved_role,
            kind=report.GENERATED, dest_dir=placed.dest_dir,
            subfoldered=placed.subfoldered,
        ))

    # Finalize: remove the company's Que folder only if every JD was pruned
    # (hidden-only whitelist keeps it when any failed/skipped PDF remains).
    removed = lifecycle.finalize_que_removal(cq.folder)
    if removed:
        logger.info("Company Que folder finalized (removed): %s", cq.company)
    else:
        logger.info("Company Que folder kept (PARTIAL — JDs remain): %s", cq.company)


def run_bulk(*, dry_run: bool, limit: int | None) -> report.BulkReport:
    """Run the bulk batch. Returns a `BulkReport` for the caller to print.

    `dry_run`: read-only, API-free plan preview (writes nothing).
    `limit`: maximum number of COMPANIES to process (smoke test / cost control).
    """
    que = _resolve_que()
    logger.info("Bulk %s — Que: %s", "DRY RUN" if dry_run else "LIVE", que)

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

    # Safety: snapshot the tracker JSON BEFORE the first live write of this batch
    # so a bad bulk run is always recoverable. Dry-run never reaches here.
    _backup_tracker_prebulk()

    queues = disc.queues if limit is None else disc.queues[:limit]
    rep.companies_processed = len(queues)
    logger.info(
        "Processing %d of %d companies (limit=%s)",
        len(queues), len(disc.queues), limit,
    )

    for cq in queues:
        _process_company_live(cq, apps_dir, process_jd, rep)

    # Batch the tracker sync: ONE CSV/HTML sync at the very end (not per JD).
    if any(r.kind == report.GENERATED for r in rep.results):
        try:
            from pipeline.tracker.csv_sync import sync as sync_tracker
            sync_tracker()
            rep.sync_ran = True
            logger.info("Tracker CSV + HTML synced (end of batch).")
        except Exception as exc:  # noqa: BLE001 — sync failure must not lose the batch
            logger.warning("End-of-batch tracker sync skipped: %s", exc)
    else:
        logger.info("No JDs generated — skipping end-of-batch sync.")

    return rep
