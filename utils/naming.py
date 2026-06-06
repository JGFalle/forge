"""Shared naming helpers: output slugs and human-facing folder names.

`make_slug` is the single source of truth for the short, lowercased,
underscore-joined identifier used for local output directories and tailoring
JSON filenames. It must stay byte-identical to the historical
`run.py::_make_slug` / `pipeline.core._slug` implementations.

`safe_folder_name` produces a human-facing folder name (per-job subfolders
in bulk multi-JD companies). Unlike `make_slug` it preserves spaces and
casing, only stripping filesystem-illegal characters.
"""

import re

# Characters that are illegal in folder names on common filesystems
# (Windows-strict superset so Drive-synced folders stay portable), plus
# control chars handled separately.
_ILLEGAL_FOLDER_CHARS = r'[<>:"/\\|?*]'

# Dash variants that show up in real JD role names (en dash, em dash, etc.).
_DASH_VARIANTS = "‐‑‒–—―−"


def make_slug(company: str, role: str) -> str:
    """Short lowercase identifier for output dirs and tailoring filenames.

    Lowercase, collapse any run of non-alphanumerics to a single underscore,
    strip leading/trailing underscores, truncate to 60 chars.
    """
    combined = f"{company}_{role}".lower()
    return re.sub(r"[^a-z0-9]+", "_", combined).strip("_")[:60]


def safe_folder_name(role: str, max_length: int = 120) -> str:
    """Sanitize a role title into a safe, human-facing folder name.

    - Normalize en/em/other dash variants to a plain hyphen.
    - Replace "&" with " and " so "S&OE" reads as "S and OE".
    - Remove filesystem-illegal chars and control chars.
    - Collapse whitespace runs to a single space.
    - Strip leading/trailing whitespace, dots, and hyphens.
    - Truncate to max_length; re-strip trailing junk after truncation.
    - Never returns empty: falls back to "Untitled".
    """
    if not role:
        return "Untitled"

    name = role
    # Normalize dash variants to a plain hyphen.
    name = re.sub(f"[{_DASH_VARIANTS}]", "-", name)
    # "&" reads better spelled out than stripped. Pad with spaces so a
    # no-space "S&OE" renders readably as "S and OE"; the whitespace collapse
    # below then folds any resulting double spaces (e.g. "Engineering &
    # Systems") back to single spaces.
    name = name.replace("&", " and ")
    # Drop filesystem-illegal characters.
    name = re.sub(_ILLEGAL_FOLDER_CHARS, "", name)
    # Collapse whitespace runs (incl. tabs/newlines) to a single space BEFORE
    # dropping control chars, so "a\tb" -> "a b" not "ab".
    name = re.sub(r"\s+", " ", name)
    # Drop any remaining non-printable characters (spaces are printable).
    name = "".join(ch for ch in name if ch.isprintable())
    # Strip leading/trailing whitespace, dots, hyphens (prevents "..",
    # hidden ".name", and trailing-dot folders).
    name = name.strip(" .-")

    if len(name) > max_length:
        name = name[:max_length].strip(" .-")

    return name or "Untitled"
