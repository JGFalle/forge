import re


def clean_text(text: str) -> str:
    """Normalize whitespace and strip common Unicode noise characters."""
    if not text:
        return ""
    text = str(text)
    text = text.replace("‑", "-").replace("–", "-").replace("—", "-")
    text = text.replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("\xa0", " ").replace("​", " ")
    return re.sub(r"\s+", " ", text).strip()


def strip_em_dashes(text: str) -> str:
    """Replace em dashes with commas. Used as a final pass before writing output."""
    return text.replace("—", ",").replace("--", ",")


def word_count(text: str) -> int:
    return len(text.split()) if text.strip() else 0


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "..."
