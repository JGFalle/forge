"""Parity + behavior tests for the consolidated naming helpers.

`make_slug` MUST stay byte-identical to the historical `_make_slug`/`_slug`
implementations (both were the same logic, duplicated in run.py and
pipeline/core.py). The parity test below pins that contract against a table of
known inputs/outputs so the consolidation cannot silently change folder naming.
"""

import re

import pytest

from utils.naming import make_slug, safe_folder_name


def _legacy_slug(company: str, role: str) -> str:
    """The exact historical implementation (run.py._make_slug / core._slug)."""
    combined = f"{company}_{role}".lower()
    return re.sub(r"[^a-z0-9]+", "_", combined).strip("_")[:60]


# Table of known inputs; outputs are derived from the legacy implementation so a
# future change to make_slug that drifts from legacy fails loudly.
SLUG_CASES = [
    ("Clorox", "Director, Customer Supply Chain"),
    ("Clorox", "Senior Manager – Network Planning Process Excellence"),
    ("Clorox", "Director - Logistics, Value Transformation Office"),
    ("HP", "Director, Planning Continuous improvement"),
    ("Monster Energy", "Director Distribution Engineering & Systems"),
    ("Boston Dynamics", "Senior Director of Strategic Supply Chain"),
    ("The Coca-Cola Company", "VP, Supply Chain"),
    ("A&M", "PEPI Senior Director — Supply Chain"),
    ("___leading", "trailing___"),
    ("", ""),
    ("x" * 80, "y" * 80),  # truncation to 60 chars
    ("Ünïcödé Ço", "Røle Nåme"),  # non-ascii collapses to underscores
]


@pytest.mark.parametrize(("company", "role"), SLUG_CASES)
def test_make_slug_matches_legacy(company, role):
    assert make_slug(company, role) == _legacy_slug(company, role)


def test_make_slug_known_outputs():
    # A few explicit expected strings as a second guard against accidental drift.
    assert make_slug("Clorox", "Director, Customer Supply Chain") == (
        "clorox_director_customer_supply_chain"
    )
    assert make_slug("HP", "Director, Planning Continuous improvement") == (
        "hp_director_planning_continuous_improvement"
    )
    assert make_slug(
        "Monster Energy", "Director Distribution Engineering & Systems"
    ) == "monster_energy_director_distribution_engineering_systems"


def test_make_slug_truncates_to_60():
    out = make_slug("x" * 80, "y" * 80)
    assert len(out) == 60


def test_make_slug_strips_edges():
    assert make_slug("___lead", "trail___") == "lead_trail"


def test_run_and_core_delegate_to_shared():
    import run
    from pipeline import core

    company, role = "Clorox", "Senior Manager – S&OE Process Excellence"
    expected = make_slug(company, role)
    assert run._make_slug(company, role) == expected
    assert core._slug(company, role) == expected


# ---- safe_folder_name -------------------------------------------------------


def test_safe_folder_name_real_clorox_roles():
    assert safe_folder_name(
        "Senior Manager – Network Planning Process Excellence"
    ) == "Senior Manager - Network Planning Process Excellence"
    assert safe_folder_name(
        "Director - Logistics, Value Transformation Office"
    ) == "Director - Logistics, Value Transformation Office"


def test_safe_folder_name_ampersand_becomes_and():
    # Already-spaced "&" stays clean (no double space) after collapse.
    assert safe_folder_name("Distribution Engineering & Systems") == (
        "Distribution Engineering and Systems"
    )


def test_safe_folder_name_no_space_ampersand_reads_readably():
    # The real Clorox "S&OE" name: "&" must space out to "S and OE", never the
    # ugly run-together "SandOE".
    assert safe_folder_name("Senior Manager – S&OE Process Excellence") == (
        "Senior Manager - S and OE Process Excellence"
    )
    assert safe_folder_name("S&OE") == "S and OE"
    # Mid-word ampersand without surrounding spaces still spaces out.
    assert safe_folder_name("R&D Operations") == "R and D Operations"


def test_safe_folder_name_strips_illegal_chars():
    out = safe_folder_name('Dir/Ops: "Lead" <Team> | Region?*')
    for ch in '<>:"/\\|?*':
        assert ch not in out
    assert out  # not empty


def test_safe_folder_name_no_path_traversal():
    assert safe_folder_name("../../etc/passwd") == "etcpasswd"
    assert ".." not in safe_folder_name("..")
    assert safe_folder_name("..") == "Untitled"


def test_safe_folder_name_no_hidden_or_trailing_dot():
    assert not safe_folder_name(".hidden role").startswith(".")
    assert not safe_folder_name("role.").endswith(".")


def test_safe_folder_name_collapses_whitespace():
    assert safe_folder_name("Director    of\t\nOps") == "Director of Ops"


def test_safe_folder_name_empty_falls_back():
    assert safe_folder_name("") == "Untitled"
    assert safe_folder_name("   ") == "Untitled"
    assert safe_folder_name("///") == "Untitled"


def test_safe_folder_name_truncates_and_restrips():
    out = safe_folder_name("Director " * 50, max_length=40)
    assert len(out) <= 40
    assert not out.endswith(" ")


def test_safe_folder_name_em_dash_variants():
    # em dash, en dash, figure dash, minus sign all normalize to hyphen
    for dash in ("—", "–", "‒", "−"):
        assert safe_folder_name(f"Director {dash} Ops") == "Director - Ops"
