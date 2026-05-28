"""Extract raw text from a job description PDF."""

from pathlib import Path

import pdfplumber

from utils.text import clean_text


def extract_text(pdf_path: Path) -> str:
    """Return all text from a PDF as a single cleaned string."""
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pages: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            raw = page.extract_text()
            if raw:
                pages.append(raw)

    return clean_text("\n\n".join(pages))
