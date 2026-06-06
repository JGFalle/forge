"""Folder-lifecycle path planning and crash-safe placement for bulk.

Pure path logic plus tightly-scoped filesystem placement. Every function takes
EXPLICIT path arguments. No hardcoded Drive paths, no API calls. The orchestrator
resolves the real Drive `apps_dir` and the generated deliverables, then drives
these helpers.

Design:

- A company's permanent home is `apps_dir / <Company>/`.
- Single-JD company: deliverables land DIRECTLY in the company folder.
- Multi-JD company: each JD's deliverables land in a per-job SUBFOLDER named
  from the sanitized role.
- Move-on-first-JD: the company folder is established at the destination on the
  first JD; subsequent JDs write into the already-established folder.
- Collision policy = MERGE: if the destination company folder already exists,
  merge into it. New per-job subfolders are added; the JD PDF is copied in
  without ever clobbering an existing file.
- Crash-safe ordering for the Que -> Applications move:
      establish-dest -> copy-jd-into-dest -> verify -> prune-source.
  A crash mid-op leaves either the source intact or the dest complete, never a
  half-moved state. `prune_que_source` / `finalize_que_removal` only run AFTER
  a verified copy.

`PlacedJob` describes where a single JD's deliverables should be written. The
`dest_dir` is exactly what the orchestrator passes to `_copy_to_gdrive`'s
`gdrive_target`.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from utils.config import get
from utils.logging import get_logger
from utils.naming import make_slug, safe_folder_name

logger = get_logger(__name__)


def _sentinel_name() -> str:
    """Completion sentinel filename, from config (default ``.forge_complete``).

    `_copy_to_gdrive` writes this file as the LAST step, only after every
    deliverable has been copied. `deliverables_present` keys on it so a crash
    mid-copy (resume written, cover letter/intel not yet) is NOT mistaken for a
    finished job. Keying on the sentinel (not the resume filename) also avoids
    duplicating the tailoring `filename` literal here.
    """
    return get("bulk.sentinel", ".forge_complete")


@dataclass(frozen=True)
class PlacedJob:
    """Resolved destination for one JD's deliverables.

    `company_dir` is `apps_dir / <Company>/`. `dest_dir` is where this JD's
    `01/02/03` deliverable subdirs live: the company folder itself for a
    single-JD company, or a per-job subfolder for a multi-JD company. The
    orchestrator passes `dest_dir` as `_copy_to_gdrive(gdrive_target=...)`.
    """

    company: str
    company_dir: Path
    dest_dir: Path
    subfoldered: bool


# ----------------------------------------------------------------------------
# Pure path planning (no filesystem side effects)
# ----------------------------------------------------------------------------


def _safe_company_component(company: str) -> str:
    """Sanitize a company name into a single, traversal-safe path component.

    Discovery already filters dotted dirs, but this builder must be safe
    regardless of caller (defense in depth). Strips any path separators so the
    name can never span directories, drops a leading/trailing-dot result so
    `.`, `..`, and hidden names cannot escape or hide, and falls back to
    "Unknown Company" if nothing safe survives.
    """
    # Take only the final path component: a company containing slashes (e.g.
    # "a/b" or "../escape") collapses to its basename, never spanning dirs.
    name = Path(company).name
    name = name.strip(" .")
    if not name or name in (".", ".."):
        return "Unknown Company"
    return name


def target_company_dir(apps_dir: Path, company: str) -> Path:
    """The permanent home for a company: `apps_dir / <Company>/`.

    The company name normally comes from the Que subfolder name and is already a
    real directory name, but the component is sanitized so a company literally
    named ".." or one containing slashes can never escape `apps_dir`. Pure;
    touches nothing.
    """
    return apps_dir / _safe_company_component(company)


def _unique_subfolder(
    company_dir: Path, company: str, role: str, taken: set[Path]
) -> Path:
    """Pick a per-job subfolder under `company_dir`, unique AND role-stable.

    `safe_folder_name` is NOT injective: two distinct roles (e.g.
    "Director / Logistics" and "Director  Logistics", or two all-illegal names
    that both map to "Untitled") can sanitize to the SAME folder. If two JDs for
    one company resolved to the same `dest_dir`, JD #2 would overwrite JD #1's
    deliverables (and `deliverables_present` could then silently skip JD #2).

    Resolution must satisfy TWO properties at once:
      - UNIQUE across distinct roles (no overwrite, no false skip), and
      - STABLE for a given role across re-runs (so skip-if-present re-resolves
        the SAME dest and detects an already-finished job).

    So uniqueness is keyed on ROLE IDENTITY, not on mutable on-disk state. The
    preferred, human-readable name is `safe_folder_name(role)`. It is used as-is
    only when no OTHER role in this company already claimed it (`taken`). On a
    genuine name collision with a different role, the tiebreak is the role's
    stable `make_slug` (deterministic for that exact role), then a numeric
    suffix as a final guard. A same-role re-run produces the same base name and,
    when it is the only/first claimant, the same path -> idempotency holds.
    """
    base = company_dir / safe_folder_name(role)
    if base not in taken:
        return base
    # Collision with a DIFFERENT role's already-placed dest. Disambiguate with
    # the role's stable slug so the same role always maps to the same folder.
    slugged = company_dir / f"{safe_folder_name(role)} ({make_slug(company, role)})"
    if slugged not in taken:
        return slugged
    # Final guard (distinct roles whose slugs also collide): numeric suffix.
    n = 2
    while True:
        candidate = company_dir / f"{safe_folder_name(role)} ({make_slug(company, role)}-{n})"
        if candidate not in taken:
            return candidate
        n += 1


def plan_placement(
    apps_dir: Path,
    company: str,
    role: str,
    *,
    multi: bool,
    taken: set[Path] | None = None,
) -> PlacedJob:
    """Resolve where one JD's deliverables should be written. Pure.

    Single-JD (`multi=False`): `dest_dir` is the company folder.
    Multi-JD (`multi=True`): `dest_dir` is a sanitized per-job subfolder,
    guaranteed unique against `taken` (the dest_dirs already chosen for OTHER
    JDs of this same company in this run). Collision-proof by construction:
    `plan_placement` never returns a `dest_dir` that collides with a different
    job's dest, so two roles that sanitize identically still get distinct
    subfolders and never overwrite each other. The disambiguator is the role's
    STABLE slug, so a same-role re-run re-resolves to the same folder and
    skip-if-present still works.

    Pass the running set of placed dest_dirs as `taken` when planning a
    multi-JD company JD-by-JD. Creates nothing; the orchestrator establishes the
    dest via `establish_*`.
    """
    company_dir = target_company_dir(apps_dir, company)
    if multi:
        dest_dir = _unique_subfolder(company_dir, company, role, taken or set())
        return PlacedJob(company, company_dir, dest_dir, subfoldered=True)
    return PlacedJob(company, company_dir, company_dir, subfoldered=False)


def deliverables_present(dest_dir: Path) -> bool:
    """Idempotency check: have this JD's deliverables already been written?

    True ONLY when the completion sentinel exists in `dest_dir`. The sentinel is
    written by `mark_deliverables_complete` as the very last step of the
    deliverable copy, so a crash that wrote the resume but not the cover letter /
    intel PDF leaves NO sentinel and the next run correctly re-processes the JD
    instead of skip-as-done with a half-written destination. Read-only.
    """
    return (dest_dir / _sentinel_name()).is_file()


def mark_deliverables_complete(dest_dir: Path) -> Path:
    """Write the completion sentinel after ALL deliverables are copied.

    Must be called only once every deliverable has landed in `dest_dir`. Makes
    `deliverables_present` true. Returns the sentinel path. Idempotent (an
    already-present sentinel is simply rewritten).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    sentinel = dest_dir / _sentinel_name()
    sentinel.write_text("complete\n", encoding="utf-8")
    logger.debug("Wrote completion sentinel: %s", sentinel)
    return sentinel


# ----------------------------------------------------------------------------
# Filesystem placement (side-effecting, only touches passed-in paths)
# ----------------------------------------------------------------------------


def _non_clobbering_dest(dest_file: Path) -> Path:
    """Return a path that does not overwrite an existing file.

    If `dest_file` is free, return it. Otherwise append ` (2)`, ` (3)`, ...
    before the suffix until a free name is found. Never overwrites.
    """
    if not dest_file.exists():
        return dest_file
    stem, suffix = dest_file.stem, dest_file.suffix
    n = 2
    while True:
        candidate = dest_file.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def establish_company_dir(company_dir: Path) -> Path:
    """Ensure the company folder exists at the destination (MERGE-safe).

    Idempotent: creating an existing folder is a no-op merge (never clobbers
    existing contents). This is the move-on-first-JD anchor and the subsequent-
    JD reuse path in one call. Returns `company_dir`.
    """
    company_dir.mkdir(parents=True, exist_ok=True)
    return company_dir


def copy_jd_into_dest(jd_pdf: Path, dest_dir: Path) -> Path:
    """Copy the source JD PDF into `dest_dir`, never clobbering.

    Part of the crash-safe ordering (copy-to-dest BEFORE pruning the source).
    If a same-named PDF already exists at the destination (MERGE collision),
    the incoming one is written to a numeric-suffixed name; the existing file
    is left intact. Returns the destination path actually written.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _non_clobbering_dest(dest_dir / jd_pdf.name)
    shutil.copy2(jd_pdf, dest)
    logger.debug("Copied JD %s -> %s", jd_pdf, dest)
    return dest


def verify_copy(src: Path, dest: Path) -> bool:
    """Verify a copied file is fully present at the destination.

    Gates the irreversible `prune_que_source`, so it must reject anything
    suspicious. Requires ALL of:
      - `dest` exists and is a file,
      - the SOURCE size is greater than zero (a 0-byte source means nothing real
        was produced; pruning it would silently lose the slot, and an empty
        "copy" must never green-light a prune),
      - `dest` size exactly equals `src` size (catches truncated / half-written
        copies).
    A 0-byte source, a size mismatch, or a stat failure all return False so the
    Que source is kept for re-run rather than pruned.
    """
    if not dest.is_file():
        return False
    try:
        src_size = src.stat().st_size
        dest_size = dest.stat().st_size
    except OSError:
        return False
    if src_size <= 0:
        logger.debug("verify_copy rejected zero-byte source: %s", src)
        return False
    if src_size != dest_size:
        logger.debug(
            "verify_copy size mismatch: src=%d dest=%d (%s -> %s)",
            src_size,
            dest_size,
            src,
            dest,
        )
        return False
    return True


def prune_que_source(jd_pdf: Path) -> None:
    """Remove a single JD PDF from the Que source AFTER its copy is verified.

    Multi-JD companies prune per-JD as each finishes; the company folder is
    removed only once empty (see `finalize_que_removal`). Tolerant of an
    already-removed file (idempotent re-run).
    """
    if jd_pdf.exists():
        jd_pdf.unlink()
        logger.debug("Pruned Que JD source: %s", jd_pdf)


def finalize_que_removal(que_company_dir: Path) -> bool:
    """Remove the Que company folder once all its JDs are placed.

    Removal predicate is a HIDDEN-ONLY WHITELIST: the folder is rmtree'd ONLY
    when every remaining entry is hidden (a dotfile/dotdir, e.g. `.DS_Store`).
    If ANY non-hidden entry remains, the folder is KEPT and reported, never
    silently deleted, even if that entry is a non-PDF. This protects a stray
    user file or subdirectory (e.g. a `notes.md` or `_drafts/`) left alongside
    the JDs: a remaining JD PDF keeps the folder for re-run, and any other
    non-hidden content keeps it for inspection rather than being nuked by
    rmtree.

    Returns True if the folder was removed, False if it was kept (non-hidden
    content remains) or never existed.
    """
    if not que_company_dir.exists():
        return False

    non_hidden = [
        c for c in que_company_dir.iterdir() if not c.name.startswith(".")
    ]
    if non_hidden:
        logger.info(
            "Keeping Que folder %s: %d non-hidden entr(ies) remain (%s)",
            que_company_dir,
            len(non_hidden),
            ", ".join(sorted(c.name for c in non_hidden)),
        )
        return False

    shutil.rmtree(que_company_dir)
    logger.debug("Removed hidden-only Que company folder: %s", que_company_dir)
    return True


def place_jd(
    jd_pdf: Path,
    apps_dir: Path,
    company: str,
    role: str,
    *,
    multi: bool,
    taken: set[Path] | None = None,
) -> PlacedJob:
    """Establish the destination for one JD, crash-safely, before generation.

    Performs the non-pruning half of the crash-safe sequence:
      1. resolve placement (pure, collision-proof against `taken` + on-disk),
      2. establish the company folder at the destination (MERGE-safe),
      3. ensure the per-job subfolder exists (multi-JD only).

    For a multi-JD company, pass the set of dest_dirs already placed for OTHER
    JDs of the same company as `taken` so two roles that sanitize to the same
    folder name still get distinct subfolders (no overwrite, no false skip).
    Because the subfolder is also created here, two sequential `place_jd` calls
    that omit `taken` still diverge (the second sees the first on disk).

    It deliberately does NOT copy the JD PDF or prune the Que source: the
    orchestrator copies deliverables into `dest_dir` (via `_copy_to_gdrive`),
    then calls `copy_jd_into_dest` + `verify_copy` + `prune_que_source` /
    `finalize_que_removal` so pruning is always the LAST step. Returns the
    resolved `PlacedJob`.
    """
    placed = plan_placement(apps_dir, company, role, multi=multi, taken=taken)
    establish_company_dir(placed.company_dir)
    if placed.subfoldered:
        placed.dest_dir.mkdir(parents=True, exist_ok=True)
    return placed
