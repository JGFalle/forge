"""Tests for pipeline.bulk.lifecycle — pure path planning + crash-safe placement.

All against tmp_path. No real Drive writes, no API calls.
"""

from pathlib import Path

from pipeline.bulk import lifecycle
from pipeline.bulk.lifecycle import (
    copy_jd_into_dest,
    deliverables_present,
    establish_company_dir,
    finalize_que_removal,
    mark_deliverables_complete,
    place_jd,
    plan_placement,
    prune_que_source,
    target_company_dir,
    verify_copy,
)


def _make_jd(folder: Path, name: str, content: bytes = b"%PDF jd") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(content)
    return p


def _write_final_resume(dest_dir: Path) -> Path:
    """Simulate _copy_to_gdrive writing the final resume only (NO sentinel)."""
    final = dest_dir / "01_Final Documents"
    final.mkdir(parents=True, exist_ok=True)
    p = final / "Jack_Falle_Resume.docx"
    p.write_bytes(b"resume")
    return p


def _complete_deliverables(dest_dir: Path) -> Path:
    """Simulate a FINISHED _copy_to_gdrive: resume written, then sentinel."""
    _write_final_resume(dest_dir)
    return mark_deliverables_complete(dest_dir)


# ---- pure planning ----------------------------------------------------------


def test_target_company_dir(tmp_path):
    assert target_company_dir(tmp_path / "Apps", "Clorox") == tmp_path / "Apps" / "Clorox"


# ---- fix 5: target_company_dir path-traversal defense -----------------------


def test_target_company_dir_blocks_dotdot(tmp_path):
    apps = (tmp_path / "Apps").resolve()
    result = target_company_dir(apps, "..").resolve()
    # must NOT escape apps_dir
    assert apps in result.parents or result == apps
    assert result.parent == apps


def test_target_company_dir_blocks_slashes(tmp_path):
    apps = (tmp_path / "Apps").resolve()
    result = target_company_dir(apps, "a/b").resolve()
    # company "a/b" must collapse to a single component under apps
    assert result.parent == apps
    assert result == apps / "b"


def test_target_company_dir_blocks_traversal_path(tmp_path):
    apps = (tmp_path / "Apps").resolve()
    result = target_company_dir(apps, "../../escape").resolve()
    assert result.parent == apps


def test_plan_placement_single_jd_direct(tmp_path):
    apps = tmp_path / "Apps"
    placed = plan_placement(apps, "HP", "Director, Planning", multi=False)
    assert placed.subfoldered is False
    assert placed.dest_dir == apps / "HP"
    assert placed.company_dir == apps / "HP"


def test_plan_placement_multi_jd_subfolder(tmp_path):
    apps = tmp_path / "Apps"
    placed = plan_placement(
        apps, "Clorox", "Senior Manager – Network Planning Process Excellence", multi=True
    )
    assert placed.subfoldered is True
    assert placed.company_dir == apps / "Clorox"
    assert placed.dest_dir == (
        apps / "Clorox" / "Senior Manager - Network Planning Process Excellence"
    )


def test_plan_placement_is_pure(tmp_path):
    apps = tmp_path / "Apps"
    plan_placement(apps, "HP", "Director", multi=False)
    assert not apps.exists()  # nothing created


# ---- move-on-first-JD / establish ------------------------------------------


def test_establish_company_dir_creates_and_is_idempotent(tmp_path):
    apps = tmp_path / "Apps"
    company_dir = apps / "Clorox"
    establish_company_dir(company_dir)
    assert company_dir.is_dir()
    # second call must not clobber existing contents
    (company_dir / "existing.txt").write_text("keep me")
    establish_company_dir(company_dir)
    assert (company_dir / "existing.txt").read_text() == "keep me"


def test_place_jd_first_jd_establishes_company_then_reuses(tmp_path):
    apps = tmp_path / "Apps"
    # First JD of a multi-JD company establishes the company folder + subfolder
    p1 = place_jd(
        Path("ignored.pdf"), apps, "Clorox", "Director, Customer Supply Chain", multi=True
    )
    assert (apps / "Clorox").is_dir()
    assert p1.dest_dir.is_dir()
    # Second JD reuses the already-established company folder
    p2 = place_jd(
        Path("ignored2.pdf"), apps, "Clorox", "Senior Manager - S&OE Process Excellence",
        multi=True,
    )
    assert p2.company_dir == p1.company_dir
    assert p2.dest_dir != p1.dest_dir
    assert p2.dest_dir.is_dir()


def test_place_jd_single_writes_directly_in_company(tmp_path):
    apps = tmp_path / "Apps"
    placed = place_jd(Path("x.pdf"), apps, "HP", "Director, Planning", multi=False)
    assert placed.dest_dir == apps / "HP"
    assert placed.subfoldered is False
    assert (apps / "HP").is_dir()


# ---- collision MERGE --------------------------------------------------------


def test_collision_merge_preserves_existing_folder_and_files(tmp_path):
    apps = tmp_path / "Apps"
    company_dir = apps / "Clorox"
    # Pre-existing destination with a prior application's files
    prior = company_dir / "Prior Role" / "01_Final Documents"
    prior.mkdir(parents=True)
    (prior / "Jack_Falle_Resume.docx").write_bytes(b"prior resume")
    (company_dir / "notes.md").write_text("hand notes")

    # New JD for the same company merges in, never clobbering
    placed = place_jd(Path("x.pdf"), apps, "Clorox", "New Role", multi=True)
    assert placed.dest_dir == company_dir / "New Role"
    assert placed.dest_dir.is_dir()
    # prior work intact
    assert (prior / "Jack_Falle_Resume.docx").read_bytes() == b"prior resume"
    assert (company_dir / "notes.md").read_text() == "hand notes"


def test_copy_jd_non_clobbering_on_same_name(tmp_path):
    apps = tmp_path / "Apps"
    dest = apps / "HP"
    dest.mkdir(parents=True)
    existing = dest / "Director.pdf"
    existing.write_bytes(b"original existing pdf")

    src = _make_jd(tmp_path / "Que" / "HP", "Director.pdf", content=b"incoming")
    written = copy_jd_into_dest(src, dest)

    # existing left intact; incoming written to suffixed name
    assert existing.read_bytes() == b"original existing pdf"
    assert written != existing
    assert written.name == "Director (2).pdf"
    assert written.read_bytes() == b"incoming"


def test_copy_jd_clean_case(tmp_path):
    dest = tmp_path / "Apps" / "HP"
    src = _make_jd(tmp_path / "Que" / "HP", "Director.pdf", content=b"abc")
    written = copy_jd_into_dest(src, dest)
    assert written == dest / "Director.pdf"
    assert written.read_bytes() == b"abc"


# ---- fix 1: collision-proof multi-JD subfolder placement -------------------


def test_colliding_role_names_get_distinct_subfolders(tmp_path):
    """Clorox-style: 4 roles, two pairs that sanitize identically.

    "Director / Logistics" and "Director  Logistics" both sanitize to
    "Director Logistics"; two all-illegal roles both sanitize to "Untitled".
    All 4 must land in DISTINCT subfolders (no overwrite, no false skip).
    """
    apps = tmp_path / "Apps"
    roles = [
        "Director / Logistics",   # -> "Director Logistics"
        "Director  Logistics",    # -> "Director Logistics" (collides)
        "///",                    # -> "Untitled"
        '???',                    # -> "Untitled" (collides)
    ]
    taken: set[Path] = set()
    placed = []
    for r in roles:
        p = place_jd(Path("x.pdf"), apps, "Clorox", r, multi=True, taken=taken)
        placed.append(p)
        taken.add(p.dest_dir)

    dests = [p.dest_dir for p in placed]
    # 4 distinct dirs
    assert len(set(dests)) == 4
    # all actually exist on disk
    for d in dests:
        assert d.is_dir()
    # first claimant of each sanitized name keeps the clean human name; the
    # colliding distinct role is disambiguated with its STABLE role slug.
    assert dests[0].name == "Director Logistics"
    assert dests[1].name.startswith("Director Logistics (")
    assert dests[2].name == "Untitled"
    assert dests[3].name.startswith("Untitled (")


def test_colliding_roles_no_overwrite_no_false_skip(tmp_path):
    """Write+complete JD #1, place JD #2 (colliding name): JD #2 is NOT skipped
    and does NOT clobber JD #1's deliverables."""
    apps = tmp_path / "Apps"
    taken: set[Path] = set()
    p1 = place_jd(Path("x.pdf"), apps, "Clorox", "Director / Logistics", multi=True, taken=taken)
    taken.add(p1.dest_dir)
    _complete_deliverables(p1.dest_dir)

    p2 = place_jd(Path("y.pdf"), apps, "Clorox", "Director  Logistics", multi=True, taken=taken)
    taken.add(p2.dest_dir)

    # distinct dest -> JD #2 NOT seen as already-done (no false skip)
    assert p2.dest_dir != p1.dest_dir
    assert deliverables_present(p2.dest_dir) is False
    # JD #1's completed deliverables untouched
    assert deliverables_present(p1.dest_dir) is True
    assert (p1.dest_dir / "01_Final Documents" / "Jack_Falle_Resume.docx").read_bytes() == b"resume"


def test_same_role_rerun_resolves_to_same_subfolder(tmp_path):
    """A re-run of the SAME role re-resolves to the SAME dest (so decision-7
    skip-if-present can detect an already-finished job). Stability is the other
    half of collision-proofing: unique across roles, stable within a role."""
    apps = tmp_path / "Apps"
    p1 = plan_placement(apps, "Clorox", "Director, Customer Supply Chain", multi=True)
    p2 = plan_placement(apps, "Clorox", "Director, Customer Supply Chain", multi=True)
    assert p1.dest_dir == p2.dest_dir


def test_colliding_role_disambiguated_against_seeded_taken(tmp_path):
    """A DIFFERENT role that sanitizes to an already-claimed name is given a
    distinct, role-stable subfolder when the claimed dest is supplied in
    `taken` (the orchestrator seeds this from existing/placed dests)."""
    apps = tmp_path / "Apps"
    company_dir = apps / "Clorox"
    existing = company_dir / "Director Logistics"
    placed = plan_placement(
        apps, "Clorox", "Director / Logistics", multi=True, taken={existing}
    )
    assert placed.dest_dir != existing
    assert placed.dest_dir.name.startswith("Director Logistics (")


# ---- crash-safe ordering ----------------------------------------------------


def test_verify_copy_detects_good_and_bad(tmp_path):
    src = tmp_path / "src.pdf"
    src.write_bytes(b"abcdef")
    good = tmp_path / "good.pdf"
    good.write_bytes(b"abcdef")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"abc")  # size mismatch
    assert verify_copy(src, good) is True
    assert verify_copy(src, bad) is False
    assert verify_copy(src, tmp_path / "missing.pdf") is False


# ---- fix 2: verify_copy rejects empty/suspicious copies ---------------------


def test_verify_copy_rejects_zero_byte_source(tmp_path):
    src = tmp_path / "empty.pdf"
    src.write_bytes(b"")
    dest = tmp_path / "empty_copy.pdf"
    dest.write_bytes(b"")  # faithful 0-byte copy, but source produced nothing
    assert verify_copy(src, dest) is False


def test_verify_copy_rejects_size_mismatch(tmp_path):
    src = tmp_path / "src.pdf"
    src.write_bytes(b"abcdef")
    dest = tmp_path / "dest.pdf"
    dest.write_bytes(b"abc")  # truncated
    assert verify_copy(src, dest) is False


def test_verify_copy_passes_good_nonempty_copy(tmp_path):
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF real bytes")
    dest = tmp_path / "dest.pdf"
    dest.write_bytes(b"%PDF real bytes")
    assert verify_copy(src, dest) is True


def test_crash_before_prune_leaves_source_intact(tmp_path):
    """Simulate a crash AFTER copy-to-dest but BEFORE prune: source survives."""
    apps = tmp_path / "Apps"
    que_company = tmp_path / "Que" / "HP"
    jd = _make_jd(que_company, "Director.pdf", content=b"jd bytes")

    placed = place_jd(jd, apps, "HP", "Director", multi=False)
    written = copy_jd_into_dest(jd, placed.dest_dir)
    assert verify_copy(jd, written) is True
    # CRASH here, before prune_que_source. Re-run must find:
    #  - source still present (not half-moved)
    #  - dest present (copy committed)
    assert jd.exists()
    assert written.exists()


def test_prune_only_after_verify_then_source_gone(tmp_path):
    apps = tmp_path / "Apps"
    que_company = tmp_path / "Que" / "HP"
    jd = _make_jd(que_company, "Director.pdf")
    placed = place_jd(jd, apps, "HP", "Director", multi=False)
    written = copy_jd_into_dest(jd, placed.dest_dir)
    assert verify_copy(jd, written)
    prune_que_source(jd)
    assert not jd.exists()
    assert written.exists()


def test_prune_is_idempotent(tmp_path):
    jd = _make_jd(tmp_path / "Que" / "HP", "Director.pdf")
    prune_que_source(jd)
    prune_que_source(jd)  # already gone, no error
    assert not jd.exists()


def test_finalize_removes_empty_que_folder(tmp_path):
    que_company = tmp_path / "Que" / "HP"
    que_company.mkdir(parents=True)
    assert finalize_que_removal(que_company) is True
    assert not que_company.exists()


def test_finalize_keeps_partial_company_with_remaining_pdf(tmp_path):
    que_company = tmp_path / "Que" / "Clorox"
    done = _make_jd(que_company, "done.pdf")
    failed = _make_jd(que_company, "failed.pdf")
    # done JD pruned, failed JD remains -> PARTIAL -> folder kept
    prune_que_source(done)
    assert finalize_que_removal(que_company) is False
    assert que_company.exists()
    assert failed.exists()


def test_finalize_ignores_hidden_files(tmp_path):
    que_company = tmp_path / "Que" / "HP"
    que_company.mkdir(parents=True)
    (que_company / ".DS_Store").write_bytes(b"junk")
    assert finalize_que_removal(que_company) is True
    assert not que_company.exists()


def test_finalize_on_missing_folder(tmp_path):
    assert finalize_que_removal(tmp_path / "Que" / "Nope") is False


# ---- fix 4: finalize hidden-only whitelist (stray file blocks removal) ------


def test_finalize_keeps_folder_with_stray_nonpdf_file(tmp_path):
    """A stray NON-pdf file must block removal (don't nuke a user's file)."""
    que_company = tmp_path / "Que" / "HP"
    que_company.mkdir(parents=True)
    stray = que_company / "notes.md"
    stray.write_text("important hand notes")
    assert finalize_que_removal(que_company) is False
    assert que_company.exists()
    assert stray.read_text() == "important hand notes"


def test_finalize_keeps_folder_with_stray_subdir(tmp_path):
    que_company = tmp_path / "Que" / "HP"
    sub = que_company / "_drafts"
    sub.mkdir(parents=True)
    (sub / "draft.txt").write_text("wip")
    assert finalize_que_removal(que_company) is False
    assert sub.is_dir()
    assert (sub / "draft.txt").read_text() == "wip"


# ---- idempotency / skip-if-present -----------------------------------------


def test_deliverables_present_false_then_true(tmp_path):
    apps = tmp_path / "Apps"
    placed = place_jd(Path("x.pdf"), apps, "HP", "Director", multi=False)
    assert deliverables_present(placed.dest_dir) is False
    _complete_deliverables(placed.dest_dir)
    assert deliverables_present(placed.dest_dir) is True


def test_idempotent_rerun_skips_present_multi(tmp_path):
    apps = tmp_path / "Apps"
    placed = place_jd(
        Path("x.pdf"), apps, "Clorox", "Director, Customer Supply Chain", multi=True
    )
    _complete_deliverables(placed.dest_dir)
    # A re-run resolves the same dest and sees deliverables already present.
    # The re-run must pass the same `taken` discipline; here it's the first/only
    # placed dest so the resolved subfolder matches.
    placed2 = plan_placement(
        apps, "Clorox", "Director, Customer Supply Chain", multi=True
    )
    assert placed2.dest_dir == placed.dest_dir
    assert deliverables_present(placed2.dest_dir) is True


# ---- fix 3: completion sentinel (half-write must NOT skip) ------------------


def test_resume_present_but_no_sentinel_is_not_done(tmp_path):
    """A crash after the resume copy but before the sentinel must re-process."""
    apps = tmp_path / "Apps"
    placed = place_jd(Path("x.pdf"), apps, "HP", "Director", multi=False)
    # Resume landed (first copy) but cover letter/intel/sentinel did not.
    _write_final_resume(placed.dest_dir)
    assert deliverables_present(placed.dest_dir) is False


def test_sentinel_present_is_done(tmp_path):
    apps = tmp_path / "Apps"
    placed = place_jd(Path("x.pdf"), apps, "HP", "Director", multi=False)
    mark_deliverables_complete(placed.dest_dir)
    assert deliverables_present(placed.dest_dir) is True


def test_mark_complete_is_idempotent(tmp_path):
    dest = tmp_path / "dest"
    mark_deliverables_complete(dest)
    mark_deliverables_complete(dest)
    assert deliverables_present(dest) is True


def test_copy_to_gdrive_writes_sentinel_last(tmp_path):
    """End-to-end wiring: _copy_to_gdrive marks completion in the dest dir."""
    from pipeline import core

    resume = tmp_path / "Jack_Falle_Resume.docx"
    resume.write_bytes(b"resume")
    cover = tmp_path / "cover.docx"
    cover.write_bytes(b"cover")
    intel = tmp_path / "intel.pdf"
    intel.write_bytes(b"%PDF intel")
    dest = tmp_path / "Apps" / "HP"

    core._copy_to_gdrive(
        company="HP",
        role="Director",
        resume_path=resume,
        named_resume=None,
        cover_path=cover,
        named_cover=None,
        intel_pdf_path=intel,
        gdrive_target=str(dest),
    )
    assert deliverables_present(dest) is True
    assert (dest / "01_Final Documents" / "Jack_Falle_Resume.docx").is_file()


# ---- que_dir config resolver ------------------------------------------------


def test_que_dir_builds_from_config(monkeypatch):
    from pipeline.bulk import discovery

    def fake_get(key, default=None):
        return {
            "gdrive.mount_base": "/tmp/MyDrive",
            "gdrive.applications_folder": "Job Search HQ/Applications (by company)",
            "bulk.que_folder": "Que",
        }.get(key, default)

    monkeypatch.setattr(discovery, "get", fake_get)
    result = discovery.que_dir()
    assert result.name == "Que"
    assert "Applications (by company)" in str(result)
    assert result.is_absolute()


def test_que_dir_missing_config_raises(monkeypatch):
    from pipeline.bulk import discovery

    monkeypatch.setattr(discovery, "get", lambda key: None)
    try:
        discovery.que_dir()
    except ValueError as exc:
        assert "config" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError")


def test_lifecycle_module_has_no_hardcoded_drive_path():
    src = Path(lifecycle.__file__).read_text()
    assert "GoogleDrive" not in src
    assert "Job Search HQ" not in src
