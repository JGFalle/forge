"""Que discovery for bulk processing.

`discover(que_dir)` is a PURE filesystem read: walk `Que/<Company>/` subfolders
and return a deterministic, structured list of company queues. It has no side
effects, writes nothing, and is fully testable against a `tmp_path` tree.

The company name is ALWAYS taken from the subfolder name and never parsed from a
PDF. The role is parsed from the PDF later by the orchestrator, not here.

`que_dir()` is a SEPARATE config resolver that builds the real Drive Que path
from `config/config.yaml`. It is kept apart from `discover()` so discovery
stays unit-testable on arbitrary temp directories.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from utils.config import get
from utils.logging import get_logger

logger = get_logger(__name__)

# Careers-page "Reader View" PDF exports carry a fixed marker before the
# extension: "<Role> __ Reader View.pdf". The local regex JD parser extracts no
# role from these (they are reformatted reader text), so the filename stem IS the
# cleanest available role. This strips the trailing marker case-insensitively and
# tolerates spacing variants ("__ Reader View", "__Reader View", " __  Reader
# View"). Anchored to the end of the stem so a role that merely contains the word
# "view" is untouched.
_READER_VIEW_MARKER = re.compile(r"\s*_+\s*reader\s+view\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class CompanyQueue:
    """One company folder in the Que and its JD PDFs.

    `company` is the subfolder name. `folder` is the absolute (or as-passed) path
    to that subfolder. `jds` is the sorted list of top-level `*.pdf` files inside.
    """

    company: str
    folder: Path
    jds: list[Path]

    @property
    def is_multi(self) -> bool:
        """True when this company has more than one JD (drives subfoldering)."""
        return len(self.jds) > 1


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of a Que scan.

    `queues` are companies with >=1 JD (deterministically ordered). `skipped_empty`
    are company folder names that contained no PDFs, reported separately so the
    orchestrator can log them rather than silently dropping them.
    """

    queues: list[CompanyQueue] = field(default_factory=list)
    skipped_empty: list[str] = field(default_factory=list)


def _is_hidden(p: Path) -> bool:
    return p.name.startswith(".")


def role_from_filename(jd_pdf: Path) -> str:
    """Derive a human-readable role from a JD PDF filename (API-free fallback).

    Used when the local regex parser extracts no role (the real Que PDFs are
    careers-page "Reader View" exports, which parse to an empty role). Takes the
    filename STEM, strips a trailing " __ Reader View" marker case-insensitively
    (tolerating spacing variants), and collapses internal whitespace. If no
    marker is present the bare stem is returned unchanged (so a future non-Reader-
    View PDF is unaffected). Returns "Untitled" only when the filename itself is
    empty, never for a real name. Pure string logic; reads no file content.
    """
    stem = jd_pdf.stem
    stem = _READER_VIEW_MARKER.sub("", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem or "Untitled"


def _pdfs_in(folder: Path) -> list[Path]:
    """Top-level, non-hidden `*.pdf` files in `folder`, sorted by name.

    Case-insensitive on the extension; ignores subdirectories and hidden files.
    """
    pdfs = [
        child
        for child in folder.iterdir()
        if child.is_file()
        and not _is_hidden(child)
        and child.suffix.lower() == ".pdf"
    ]
    return sorted(pdfs, key=lambda p: p.name)


def discover(que_dir: Path) -> DiscoveryResult:
    """Scan a Que directory and return its company queues.

    Pure read. Rules:
    - Each immediate, non-hidden subdirectory of `que_dir` is one company; the
      company name is the subfolder name.
    - JDs are the top-level non-hidden `*.pdf` files in that subfolder.
    - Company folders with zero PDFs are skipped and reported in
      `skipped_empty` (not raised, not dropped silently).
    - Hidden files/dirs (dotfiles) and non-PDF files are ignored.
    - Loose files directly under `que_dir` (not in a company subfolder) are
      ignored.
    - Deterministic ordering: companies sorted by name, JDs sorted by filename.

    A non-existent `que_dir` yields an empty result (the orchestrator decides
    whether an absent Que is a hard error).
    """
    result = DiscoveryResult()
    if not que_dir.exists() or not que_dir.is_dir():
        logger.debug("Que dir does not exist or is not a directory: %s", que_dir)
        return result

    companies = sorted(
        (c for c in que_dir.iterdir() if c.is_dir() and not _is_hidden(c)),
        key=lambda p: p.name,
    )

    for company_dir in companies:
        jds = _pdfs_in(company_dir)
        if not jds:
            result.skipped_empty.append(company_dir.name)
            continue
        result.queues.append(
            CompanyQueue(company=company_dir.name, folder=company_dir, jds=jds)
        )

    return result


def que_dir() -> Path:
    """Resolve the real Drive Que path from config.

    Built as `gdrive.mount_base` / `gdrive.applications_folder` / `bulk.que_folder`,
    expanded and made absolute. No hardcoded paths in source (project rule).
    Kept SEPARATE from `discover()` so discovery stays testable on temp dirs.
    """
    mount_base = get("gdrive.mount_base")
    applications_folder = get("gdrive.applications_folder")
    if not mount_base or not applications_folder:
        raise ValueError(
            "gdrive.mount_base and gdrive.applications_folder must be set in "
            "config/config.yaml to resolve the Que path."
        )
    que_folder = get("bulk.que_folder", "Que")
    return (
        Path(mount_base).expanduser() / applications_folder / que_folder
    ).resolve()
