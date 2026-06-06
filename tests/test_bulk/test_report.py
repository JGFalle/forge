"""Tests for the bulk batch report formatter, focused on LOUD skip reporting.

Ghost-HIGH and hard-pass skips must be prominent and explicitly state the JD was
LEFT IN THE QUE for review (not silently dropped). Already-present skips are
benign but should still be listed (not buried in the summary line).
"""

from __future__ import annotations

from pathlib import Path

from pipeline.bulk import report


def _result(company, jd, role, kind, dest=None):
    return report.JDResult(
        company=company,
        jd_name=jd,
        role=role,
        kind=kind,
        dest_dir=Path(dest) if dest else None,
    )


def test_loud_skips_are_prominent_and_say_left_in_que():
    rep = report.BulkReport(dry_run=False)
    rep.add(_result("Monster Energy", "me.pdf", "Director Distribution", report.SKIP_HARD_PASS))
    rep.add(_result("GhostCo", "g.pdf", "VP Operations", report.SKIP_GHOST))
    rep.add(_result("HP", "hp.pdf", "Director CI", report.GENERATED, dest="x/HP"))

    text = report.format_report(rep)

    # A clearly-labeled, hard-to-miss section header.
    assert "SKIPPED — LEFT IN THE QUE FOR YOUR REVIEW (2)" in text
    # Each skipped JD is named with its company/role.
    assert "Monster Energy / Director Distribution" in text
    assert "GhostCo / VP Operations" in text
    # The reason is spelled out in plain English, not just a code.
    assert "hard-pass" in text
    assert "ghost" in text.lower()
    # It explicitly states the JD was left in the Que (not processed).
    assert "left in Que" in text
    # The override guidance is present so Jack knows how to act on a skip.
    assert "OVERRIDE" in text


def test_present_skips_listed_but_marked_benign():
    rep = report.BulkReport(dry_run=False)
    rep.add(_result("Clorox", "a.pdf", "Director CSC", report.SKIP_PRESENT, dest="x/Clorox/a"))
    rep.add(_result("Clorox", "b.pdf", "Director VTO", report.SKIP_PRESENT, dest="x/Clorox/b"))

    text = report.format_report(rep)

    assert "Already present" in text
    assert "benign" in text
    assert "Clorox / Director CSC" in text
    assert "Clorox / Director VTO" in text
    # Benign skips are NOT in the loud section.
    assert "SKIPPED — LEFT IN THE QUE FOR YOUR REVIEW" not in text


def test_no_skip_sections_when_clean_run():
    rep = report.BulkReport(dry_run=False)
    rep.add(_result("HP", "hp.pdf", "Director CI", report.GENERATED, dest="x/HP"))

    text = report.format_report(rep)

    assert "SKIPPED — LEFT IN THE QUE" not in text
    assert "Already present" not in text


def test_present_skips_property():
    rep = report.BulkReport()
    rep.add(_result("A", "a.pdf", "r", report.SKIP_PRESENT))
    rep.add(_result("B", "b.pdf", "r", report.GENERATED))
    rep.add(_result("C", "c.pdf", "r", report.SKIP_GHOST))
    assert [r.company for r in rep.present_skips] == ["A"]
    assert [r.company for r in rep.loud_skips] == ["C"]
