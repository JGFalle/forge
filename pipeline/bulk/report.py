"""Batch report structures + formatter for bulk processing.

`BulkReport` accumulates per-company, per-JD outcomes for one bulk run (dry-run
plan preview OR live results). `format_report` renders a clean, scannable text
block. The orchestrator builds the report; `run.py` prints the formatted text.
No `print` here (project rule: pipeline modules log, the CLI prints).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Per-JD result kinds. GENERATED = deliverables produced (live only). PLANNED =
# would generate (dry-run). SKIP_* = intentionally not generated. FAILED = error.
GENERATED = "generated"
PLANNED = "planned"
SKIP_GHOST = "skip_ghost"
SKIP_HARD_PASS = "skip_hard_pass"
SKIP_PRESENT = "skip_present"
FAILED = "failed"

# Human labels for the per-kind status column.
_KIND_LABEL = {
    GENERATED: "OK",
    PLANNED: "PLAN",
    SKIP_GHOST: "SKIP (ghost-HIGH)",
    SKIP_HARD_PASS: "SKIP (hard-pass)",
    SKIP_PRESENT: "SKIP (already present)",
    FAILED: "FAIL",
}

# Plain-English reasons for the loud skip section (ghost-HIGH / hard-pass).
_SKIP_REASON_TEXT = {
    SKIP_GHOST: "high ghost-job risk (North Star: do not generate against ghost reqs)",
    SKIP_HARD_PASS: "hard-pass fit verdict (does not meet the search filters)",
}


@dataclass
class JDResult:
    """Outcome for one JD within a company."""

    company: str
    jd_name: str
    role: str = ""
    kind: str = PLANNED
    dest_dir: Path | None = None
    subfoldered: bool = False
    would_skip: bool = False  # dry-run: deliverables already present at dest
    detail: str = ""  # failure error / skip reason text

    @property
    def label(self) -> str:
        return _KIND_LABEL.get(self.kind, self.kind)


@dataclass
class BulkReport:
    """All per-company, per-JD outcomes for one bulk run."""

    dry_run: bool = False
    que_dir: Path | None = None
    companies_discovered: int = 0
    companies_processed: int = 0
    skipped_empty: list[str] = field(default_factory=list)
    results: list[JDResult] = field(default_factory=list)
    sync_ran: bool = False

    def add(self, result: JDResult) -> None:
        self.results.append(result)

    # ── Counts ────────────────────────────────────────────────────────────────
    def _count(self, kind: str) -> int:
        return sum(1 for r in self.results if r.kind == kind)

    @property
    def generated(self) -> int:
        return self._count(GENERATED)

    @property
    def planned(self) -> int:
        return self._count(PLANNED)

    @property
    def skipped_ghost(self) -> int:
        return self._count(SKIP_GHOST)

    @property
    def skipped_hard_pass(self) -> int:
        return self._count(SKIP_HARD_PASS)

    @property
    def skipped_present(self) -> int:
        return self._count(SKIP_PRESENT)

    @property
    def failed(self) -> int:
        return self._count(FAILED)

    @property
    def total_jds(self) -> int:
        return len(self.results)

    @property
    def loud_skips(self) -> list[JDResult]:
        """Skips that must be surfaced loudly: ghost-HIGH and hard-pass.

        These are intentional North-Star guards (do not mass-generate against
        ghost reqs or hard passes), so you must SEE that a role was skipped,
        not silently lose it.
        """
        return [r for r in self.results if r.kind in (SKIP_GHOST, SKIP_HARD_PASS)]

    @property
    def present_skips(self) -> list[JDResult]:
        """Already-present skips: benign (deliverables already exist at dest)."""
        return [r for r in self.results if r.kind == SKIP_PRESENT]

    @property
    def failures(self) -> list[JDResult]:
        return [r for r in self.results if r.kind == FAILED]


def _company_order(report: BulkReport) -> list[str]:
    seen: list[str] = []
    for r in report.results:
        if r.company not in seen:
            seen.append(r.company)
    return seen


def format_report(report: BulkReport) -> str:
    """Render a scannable text block for the dry-run plan or live results."""
    lines: list[str] = []
    header = "FORGE BULK — DRY RUN (plan only)" if report.dry_run else "FORGE BULK — RESULTS"
    lines.append(header)
    lines.append("─" * 60)
    if report.que_dir is not None:
        lines.append(f"Que: {report.que_dir}")
    lines.append(
        f"{report.companies_discovered} companies discovered, "
        f"{report.companies_processed} processed, {report.total_jds} JDs."
    )
    lines.append("")

    for company in _company_order(report):
        lines.append(company)
        for r in (x for x in report.results if x.company == company):
            role = r.role or "(role unparsed)"
            dest = r.dest_dir.name if r.dest_dir is not None else "—"
            shape = "multi" if r.subfoldered else "single"
            if report.dry_run:
                skip_note = "  would-skip (already present)" if r.would_skip else ""
                action = (
                    "would add in_que" if not r.would_skip else "no tracker change"
                )
                lines.append(
                    f"  [{r.label}] {role}  ->  {dest}/  ({shape})"
                    f"{skip_note}  |  {action}"
                )
            else:
                detail = f"  ({r.detail})" if r.detail else ""
                lines.append(f"  [{r.label}] {role}  ->  {dest}/  ({shape}){detail}")
        lines.append("")

    if report.skipped_empty:
        lines.append(f"Empty company folders skipped: {', '.join(report.skipped_empty)}")
        lines.append("")

    lines.append("─" * 60)
    if report.dry_run:
        lines.append(
            f"PLAN: {report.planned} would generate, "
            f"{report.skipped_present} already present, "
            f"{report.skipped_ghost} ghost-HIGH, "
            f"{report.skipped_hard_pass} hard-pass. Nothing written."
        )
    else:
        lines.append(
            f"BULK COMPLETE: {report.generated} generated, "
            f"{report.skipped_present} already present, "
            f"{report.skipped_ghost} ghost-HIGH, "
            f"{report.skipped_hard_pass} hard-pass, "
            f"{report.failed} failed."
        )
        lines.append(
            "Tracker sync: " + ("ran once at end." if report.sync_ran else "not run.")
        )

    loud = report.loud_skips
    if loud:
        lines.append("")
        lines.append("=" * 60)
        lines.append(f"!! SKIPPED — LEFT IN THE QUE FOR YOUR REVIEW ({len(loud)})")
        lines.append("=" * 60)
        lines.append(
            "These JDs were NOT generated and were intentionally LEFT IN THE QUE."
        )
        lines.append(
            "Review each below. To process one anyway, run it manually with an "
            'override, e.g. python run.py <jd.pdf> --context "OVERRIDE: ...".'
        )
        for r in loud:
            reason = _SKIP_REASON_TEXT.get(r.kind, r.label)
            lines.append(f"  - {r.company} / {r.role or r.jd_name}")
            lines.append(f"      reason: {reason} — left in Que (not processed)")

    failures = report.failures
    if failures:
        lines.append("")
        lines.append("Failures (left in Que for re-run):")
        for r in failures:
            lines.append(f"  - {r.company} / {r.role or r.jd_name}: {r.detail or 'error'}")

    present = report.present_skips
    if present:
        lines.append("")
        lines.append(f"Already present (benign — left in Que, nothing to do) ({len(present)}):")
        for r in present:
            lines.append(f"  - {r.company} / {r.role or r.jd_name}")

    return "\n".join(lines)
