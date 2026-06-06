"""Folder-lifecycle path planning and crash-safe placement for bulk.

Pure path logic plus tightly-scoped filesystem placement. Every function takes
explicit path arguments. No hardcoded Drive paths, no API calls.

Design:
- A company's permanent home is `apps_dir / <Company>/`.
- Single-JD company: deliverables land directly in the company folder.
- Multi-JD company: each JD's deliverables land in a per-job subfolder named
  from the sanitized role.
- Move-on-first-JD: the company folder is established on the first JD;
  subsequent JDs write into the already-established folder.
- Collision policy = MERGE: if the company folder already exists, merge.
  New per-job subfolders are added; the JD PDF never clobbers an existing file.
- Crash-safe ordering: establish-dest -> copy-jd -> verify -> prune-source.
  A crash mid-op leaves either the source intact or the dest complete, never a
  half-moved state. Pruning only runs after a verified copy.

`PlacedJob` describes where a single JD's deliverables should go. `dest_dir`
is what the orchestrator passes to `_copy_to_gdrive(gdrive_target=...)`.
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
    """Completion sentinel filename from config (default `.forge_complete`).

    Written as the last step by `_copy_to_gdrive`. `deliverables_present` keys
    on it so a crash mid-copy is not mistaken for a finished job.
    """
    return get("bulk.sentinel", ".forge_complete")


@dataclass(frozen=True)
class PlacedJob:
    """Resolved destination for one JD's deliverables.

    `company_dir` is `apps_dir / <Company>/`. `dest_dir` is where the 01/02/03
    subdirs live: the company folder for a single-JD company, or a per-job
    subfolder for a multi-JD company.
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

    Strips path separators so the name can't span directories, drops a result
    that starts/ends with a dot, and falls back to "Unknown Company" if nothing
    safe survives.
    """
    # Take only the final path component: a company containing slashes (e.g.
    # "a/b" or "../escape") collapses to its basename, never spanning dirs.
    name = Path(company).name
    name = name.strip(" .")
    if not name or name in (".", ".."):
        return "Unknown Company"
    return name


def target_company_dir(apps_dir: Path, company: str) -> Path:
    """Permanent home for a company: `apps_dir / <Company>/`.

    The name is sanitized so a company named ".." or containing slashes can't
    escape `apps_dir`. Pure; touches nothing.
    """
    return apps_dir / _safe_company_component(company)


def _unique_subfolder(
    company_dir: Path, company: str, role: str, taken: set[Path]
) -> Path:
    """Pick a per-job subfolder under `company_dir`, unique and role-stable.

    `safe_folder_name` is not injective: two distinct roles can sanitize to the
    same folder name. If that happens, JD #2 would overwrite JD #1's work.

    Resolution satisfies two properties:
      - UNIQUE across distinct roles (no overwrite, no false skip), and
      - STABLE for a given role across re-runs (skip-if-present re-resolves
        the same dest and detects an already-finished job).

    Preferred name is `safe_folder_name(role)`. On collision with a different
    role's claimed name, falls back to the role's stable slug, then a numeric
    suffix. A same-role re-run gets the same base name -> idempotent.
    """
    base = company_dir / safe_folder_name(role)
    if base not in taken:
        return base
    # collision: disambiguate with the role's stable slug
    slugged = company_dir / f"{safe_folder_name(role)} ({make_slug(company, role)})"
    if slugged not in taken:
        return slugged
    # final guard: numeric suffix if slugs also collide
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
    """Resolve where one JD's deliverables should go. Pure.

    Single-JD: `dest_dir` is the company folder.
    Multi-JD: `dest_dir` is a unique per-job subfolder guaranteed not to
    collide with `taken` (dest_dirs already placed for other JDs of this
    company in this run). The disambiguator is the role's stable slug so a
    same-role re-run resolves to the same folder and skip-if-present works.

    Pass the running set of placed dest_dirs as `taken` when planning
    multi-JD JDs. Creates nothing; the orchestrator calls `establish_*`.
    """
    company_dir = target_company_dir(apps_dir, company)
    if multi:
        dest_dir = _unique_subfolder(company_dir, company, role, taken or set())
        return PlacedJob(company, company_dir, dest_dir, subfoldered=True)
    return PlacedJob(company, company_dir, company_dir, subfoldered=False)


def deliverables_present(dest_dir: Path) -> bool:
    """True only when the completion sentinel exists in `dest_dir`.

    The sentinel is written as the last step of the deliverable copy. A crash
    that wrote the resume but not the cover letter leaves no sentinel, so the
    next run re-processes the JD instead of skipping it as done. Read-only.
    """
    return (dest_dir / _sentinel_name()).is_file()


def mark_deliverables_complete(dest_dir: Path) -> Path:
    """Write the completion sentinel after all deliverables are copied.

    Makes `deliverables_present` return True. Idempotent.
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
    """Return a path that doesn't overwrite an existing file.

    If `dest_file` is free, return it. Otherwise append ` (2)`, ` (3)`, ...
    before the suffix until a free name is found.
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
    """Ensure the company folder exists at the destination (merge-safe).

    Idempotent: creating an existing folder is a no-op. Returns `company_dir`.
    """
    company_dir.mkdir(parents=True, exist_ok=True)
    return company_dir


def copy_jd_into_dest(jd_pdf: Path, dest_dir: Path) -> Path:
    """Copy the JD PDF into `dest_dir`, never clobbering an existing file.

    On a name collision, the incoming file gets a numeric suffix. Part of the
    crash-safe ordering (copy BEFORE pruning the source). Returns the path
    actually written.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _non_clobbering_dest(dest_dir / jd_pdf.name)
    shutil.copy2(jd_pdf, dest)
    logger.debug("Copied JD %s -> %s", jd_pdf, dest)
    return dest


def verify_copy(src: Path, dest: Path) -> bool:
    """Verify a copied file is fully present at the destination.

    Gates the irreversible `prune_que_source`. Requires all of:
      - `dest` exists and is a file,
      - source size is greater than zero,
      - dest size exactly equals src size.
    Any failure returns False so the Que source is kept for re-run.
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
    """Remove a JD PDF from the Que after its copy is verified.

    Multi-JD companies prune per-JD; the folder is removed only once empty.
    Idempotent if the file is already gone.
    """
    if jd_pdf.exists():
        jd_pdf.unlink()
        logger.debug("Pruned Que JD source: %s", jd_pdf)


def finalize_que_removal(que_company_dir: Path) -> bool:
    """Remove the Que company folder once all its JDs are placed.

    Only removes when every remaining entry is hidden (e.g. `.DS_Store`). Any
    non-hidden entry (a remaining PDF, stray `notes.md`, etc.) keeps the folder
    for inspection or re-run. Returns True if removed, False if kept or absent.
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
    """Establish the destination for one JD before generation.

    Non-pruning half of the crash-safe sequence:
      1. resolve placement (pure, collision-proof against `taken`),
      2. establish the company folder (merge-safe),
      3. ensure the per-job subfolder exists (multi-JD only).

    Does NOT copy the JD PDF or prune the Que source. Returns the resolved
    `PlacedJob`.
    """
    placed = plan_placement(apps_dir, company, role, multi=multi, taken=taken)
    establish_company_dir(placed.company_dir)
    if placed.subfoldered:
        placed.dest_dir.mkdir(parents=True, exist_ok=True)
    return placed
