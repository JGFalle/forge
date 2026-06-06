"""Tests for pipeline.bulk.discovery — pure Que scanning against tmp_path."""

from pathlib import Path

import pytest

from pipeline.bulk.discovery import CompanyQueue, discover, role_from_filename


def _make_pdf(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(b"%PDF-1.4 fake")
    return p


def _build_real_shaped_que(root: Path) -> Path:
    """Mimic the real Que: Boston Dynamics 1, Clorox 4, HP 1, Monster Energy 1."""
    que = root / "Que"
    _make_pdf(que / "Boston Dynamics", "Senior Director of Strategic Supply Chain.pdf")
    _make_pdf(que / "Clorox", "Director, Customer Supply Chain.pdf")
    _make_pdf(que / "Clorox", "Senior Manager - Network Planning Process Excellence.pdf")
    _make_pdf(que / "Clorox", "Director - Logistics, Value Transformation Office.pdf")
    _make_pdf(que / "Clorox", "Senior Manager - S&OE Process Excellence.pdf")
    _make_pdf(que / "HP", "Director, Planning Continuous improvement.pdf")
    _make_pdf(que / "Monster Energy", "Director Distribution Engineering & Systems.pdf")
    return que


def test_discovers_companies_and_jds(tmp_path):
    que = _build_real_shaped_que(tmp_path)
    result = discover(que)

    by_name = {q.company: q for q in result.queues}
    assert set(by_name) == {"Boston Dynamics", "Clorox", "HP", "Monster Energy"}
    assert len(by_name["Clorox"].jds) == 4
    assert len(by_name["HP"].jds) == 1
    assert result.skipped_empty == []


def test_company_name_from_folder_not_pdf(tmp_path):
    # PDF content/name must never override the folder name (Requirement A).
    que = tmp_path / "Que"
    _make_pdf(que / "Clorox", "Some Totally Different Company Role.pdf")
    result = discover(que)
    assert result.queues[0].company == "Clorox"


def test_single_vs_multi_flag(tmp_path):
    que = _build_real_shaped_que(tmp_path)
    by_name = {q.company: q for q in discover(que).queues}
    assert by_name["Clorox"].is_multi is True
    assert by_name["HP"].is_multi is False


def test_empty_company_folder_is_skipped_and_reported(tmp_path):
    que = tmp_path / "Que"
    _make_pdf(que / "HP", "Director.pdf")
    (que / "EmptyCo").mkdir(parents=True)
    result = discover(que)
    assert [q.company for q in result.queues] == ["HP"]
    assert result.skipped_empty == ["EmptyCo"]


def test_non_pdf_files_ignored(tmp_path):
    que = tmp_path / "Que"
    folder = que / "HP"
    _make_pdf(folder, "Director.pdf")
    (folder / "notes.txt").write_text("ignore me")
    (folder / "logo.png").write_bytes(b"png")
    result = discover(que)
    assert [p.name for p in result.queues[0].jds] == ["Director.pdf"]


def test_pdf_extension_case_insensitive(tmp_path):
    que = tmp_path / "Que"
    _make_pdf(que / "HP", "Director.PDF")
    result = discover(que)
    assert len(result.queues) == 1
    assert result.queues[0].jds[0].name == "Director.PDF"


def test_hidden_files_and_dirs_ignored(tmp_path):
    que = tmp_path / "Que"
    folder = que / "HP"
    _make_pdf(folder, "Director.pdf")
    (folder / ".DS_Store").write_bytes(b"junk")
    _make_pdf(folder, ".hidden.pdf")  # hidden pdf ignored
    _make_pdf(que / ".HiddenCo", "Role.pdf")  # hidden company dir ignored
    result = discover(que)
    assert [q.company for q in result.queues] == ["HP"]
    assert [p.name for p in result.queues[0].jds] == ["Director.pdf"]


def test_loose_files_under_que_ignored(tmp_path):
    que = tmp_path / "Que"
    que.mkdir()
    (que / "stray.pdf").write_bytes(b"%PDF stray")
    _make_pdf(que / "HP", "Director.pdf")
    result = discover(que)
    assert [q.company for q in result.queues] == ["HP"]


def test_deterministic_ordering(tmp_path):
    que = tmp_path / "Que"
    _make_pdf(que / "Zeta", "z.pdf")
    _make_pdf(que / "Alpha", "b.pdf")
    _make_pdf(que / "Alpha", "a.pdf")
    _make_pdf(que / "Mid", "m.pdf")
    result = discover(que)
    assert [q.company for q in result.queues] == ["Alpha", "Mid", "Zeta"]
    assert [p.name for p in result.queues[0].jds] == ["a.pdf", "b.pdf"]


def test_missing_que_dir_returns_empty(tmp_path):
    result = discover(tmp_path / "does_not_exist")
    assert result.queues == []
    assert result.skipped_empty == []


def test_que_path_pointing_at_file_returns_empty(tmp_path):
    f = tmp_path / "notadir"
    f.write_text("x")
    result = discover(f)
    assert result.queues == []


def test_company_queue_is_frozen_dataclass(tmp_path):
    cq = CompanyQueue(company="HP", folder=tmp_path, jds=[])
    assert cq.company == "HP"


# ── role_from_filename (API-free role resolver) ──────────────────────────────


# The 4 real Clorox names + HP / Boston Dynamics / Monster Energy names, each as
# the actual "<Role> __ Reader View.pdf" Reader-View export, mapped to the clean
# role the resolver must yield (marker stripped, whitespace collapsed).
_REAL_QUE_NAMES = [
    (
        "Director - Logistics, Value Transformation Office __ Reader View.pdf",
        "Director - Logistics, Value Transformation Office",
    ),
    ("Director, Customer Supply Chain __ Reader View.pdf", "Director, Customer Supply Chain"),
    (
        "Senior Manager – Network Planning Process Excellence __ Reader View.pdf",
        "Senior Manager – Network Planning Process Excellence",
    ),
    (
        "Senior Manager – S&OE Process Excellence __ Reader View.pdf",
        "Senior Manager – S&OE Process Excellence",
    ),
    (
        "Senior Director of Strategic Supply Chain __ Reader View.pdf",
        "Senior Director of Strategic Supply Chain",
    ),
    (
        "Director, Planning Continuous improvement __ Reader View.pdf",
        "Director, Planning Continuous improvement",
    ),
    (
        "Director Distribution Engineering & Systems __ Reader View.pdf",
        "Director Distribution Engineering & Systems",
    ),
]


@pytest.mark.parametrize("filename,expected", _REAL_QUE_NAMES)
def test_role_from_filename_real_que_names(tmp_path, filename, expected):
    assert role_from_filename(tmp_path / filename) == expected


def test_role_from_filename_strips_marker_case_insensitively(tmp_path):
    assert role_from_filename(tmp_path / "Director, Ops __ READER VIEW.pdf") == "Director, Ops"
    assert role_from_filename(tmp_path / "Director, Ops __ reader view.pdf") == "Director, Ops"


def test_role_from_filename_tolerates_marker_spacing_variants(tmp_path):
    # "__Reader View" (no space after underscores) and a single underscore.
    assert role_from_filename(tmp_path / "Director, Ops __Reader View.pdf") == "Director, Ops"
    assert role_from_filename(tmp_path / "Director, Ops _ Reader View.pdf") == "Director, Ops"
    assert role_from_filename(tmp_path / "Director, Ops __  Reader  View.pdf") == "Director, Ops"


def test_role_from_filename_no_marker_uses_bare_stem(tmp_path):
    # A non-Reader-View PDF: the bare stem is the role, unchanged.
    assert role_from_filename(tmp_path / "VP of Operations.pdf") == "VP of Operations"


def test_role_from_filename_collapses_internal_whitespace(tmp_path):
    assert role_from_filename(tmp_path / "Director   of   Ops.pdf") == "Director of Ops"


def test_role_from_filename_does_not_strip_unrelated_view_word(tmp_path):
    # "view" not in the trailing marker position must survive.
    assert role_from_filename(tmp_path / "Director, Birds Eye View.pdf") == "Director, Birds Eye View"


def test_role_from_filename_empty_name_falls_back_to_untitled(tmp_path):
    # A stem that reduces to nothing (marker-only, or an empty stem) is the only
    # case that yields the last-resort "Untitled".
    assert role_from_filename(tmp_path / " __ Reader View.pdf") == "Untitled"
    assert role_from_filename(Path("")) == "Untitled"
