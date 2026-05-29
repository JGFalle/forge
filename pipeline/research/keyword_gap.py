"""Keyword gap report: surface JD terms missing from the tailoring JSON.

Extracts meaningful single words and 2-grams from the JD, then checks
which ones appear anywhere in the tailored resume content. Reports
coverage percentage and top missing terms.
"""

from __future__ import annotations

import re
from pathlib import Path

_BOILERPLATE_MARKERS = [
    "equal employment opportunity",
    "equal opportunity employer",
    "we are an equal",
    "eeo statement",
    "affirmative action",
    "we do not discriminate",
    "compensation range",
    "base pay range",
    "base salary range",
    "the pay range for this",
    "annual base pay",
    "base compensation",
    "benefits include",
    "medical, dental",
    "health insurance",
    "401(k)",
    "paid time off",
    "privacy policy",
    "do not sell my personal",
    "california notice at collection",
    "cookies settings",
]

_STOP_WORDS = {
    # Articles / prepositions / conjunctions
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by", "can",
    "do", "does", "did", "for", "from", "had", "has", "have", "having", "he",
    "her", "his", "if", "in", "into", "is", "it", "its", "may", "might",
    "not", "of", "on", "or", "our", "shall", "she", "so", "that", "the",
    "their", "them", "then", "there", "they", "this", "to", "up", "upon",
    "via", "was", "we", "were", "will", "with", "would", "you", "your",
    # Common adjectives / adverbs / pronouns
    "about", "above", "across", "after", "again", "against", "all", "along",
    "also", "although", "always", "among", "any", "appropriate", "around",
    "based", "before", "between", "both", "but", "each", "either", "else",
    "even", "every", "further", "given", "here", "however", "including",
    "least", "less", "many", "more", "most", "much", "must", "need", "next",
    "none", "once", "only", "other", "otherwise", "out", "over", "own",
    "same", "since", "some", "such", "sure", "than", "these", "those",
    "through", "thus", "under", "until", "very", "well", "when", "where",
    "whether", "which", "while", "who", "whose", "within", "without", "yet",
    # Common verbs (generic, not differentiating)
    "ability", "able", "achieve", "achieved", "across", "act", "acting",
    "action", "actions", "activities", "activity", "additional", "additionally",
    "address", "apply", "applying", "approach", "area", "areas", "assist",
    "bring", "build", "builds", "business", "candidate", "career", "click",
    "come", "communicate", "company", "complete", "connect", "continue",
    "create", "currently", "deliver", "delivery", "demonstrate", "develop",
    "development", "drive", "driving", "effectively", "enable", "ensure",
    "experience", "find", "focus", "following", "function", "functions",
    "goals", "help", "high", "identify", "implement", "important", "include",
    "including", "information", "initiatives", "interaction", "involve",
    "keep", "know", "large", "lead", "leading", "learn", "level", "like",
    "maintain", "make", "manage", "meet", "multiple", "new", "offer",
    "operate", "opportunity", "organization", "part", "perform", "place",
    "plan", "plans", "position", "potential", "present", "primary", "prior",
    "process", "provide", "related", "relevant", "require", "required",
    "requirements", "responsible", "result", "results", "role", "seek",
    "senior", "serve", "setting", "should", "skills", "specific", "strong",
    "success", "successful", "support", "take", "team", "teams", "think",
    "time", "type", "understand", "understanding", "use", "used", "using",
    "value", "various", "view", "what", "work", "working", "years",
}

_MIN_WORD_LEN = 4


def _clean_jd_text(text: str) -> str:
    """Strip PDF artifacts from raw JD text before term extraction.

    Handles web-scraped PDFs where content arrives as a large blob with
    embedded URLs, nav fragments, timestamps, and legal boilerplate.
    """
    # Remove URLs in parentheses: (https://...)
    text = re.sub(r"\(https?://[^\)]+\)", " ", text)
    # Remove bare URLs
    text = re.sub(r"https?://\S+", " ", text)
    # Remove timestamp / page header patterns: "5/10/26, 4:28 AM ..."
    text = re.sub(r"\d{1,2}/\d{1,2}/\d{2,4},\s*\d{1,2}:\d{2}\s*[AP]M", " ", text)
    # Remove page number lines: "1/11", ""
    text = re.sub(r"\b\d+/\d+\b", " ", text)
    # Remove unicode icon chars (from web PDF scraping)
    text = re.sub(r"[-￾-]", " ", text)
    # Remove known boilerplate blocks
    boilerplate = [
        r"Equal Opportunity Employer.*?privacy laws\.",
        r"The Coca.Cola Company will not offer sponsorship.*?United States\.",
        r"Cookies\s+Settings",
        r"©\s+\d{4}.*?All rights reserved\.",
        r"ABOUT US\s+NEED HELP.*",
        r"Do Not Sell My Personal Information.*",
        r"California Notice at Collection.*",
    ]
    for pattern in boilerplate:
        text = re.sub(pattern, " ", text, flags=re.DOTALL | re.IGNORECASE)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _content_end(text: str) -> int:
    """Return index where boilerplate sections begin, or len(text)."""
    text_lower = text.lower()
    for marker in _BOILERPLATE_MARKERS:
        idx = text_lower.find(marker)
        if idx != -1 and idx > len(text) * 0.25:
            return idx
    return len(text)


def compute_gap(jd_text: str, tailoring_data: dict) -> dict:
    """
    Compare JD keywords against tailoring JSON content.
    Returns {missing, present, coverage_pct, total_jd_terms}.
    """
    cutoff = _content_end(jd_text)
    jd_terms = _extract_terms(_clean_jd_text(jd_text[:cutoff]))
    resume_terms = _extract_terms(_tailoring_text(tailoring_data))

    missing = sorted(jd_terms - resume_terms)
    present = sorted(jd_terms & resume_terms)
    total = len(jd_terms)
    coverage = round(len(present) / total * 100, 1) if total else 0.0

    return {
        "missing": missing,
        "present": present,
        "coverage_pct": coverage,
        "total_jd_terms": total,
    }


def display_gap(gap: dict, top_n: int = 20) -> None:
    """Print keyword gap report to stdout."""
    print(f"\n{'='*60}")
    print(f"  KEYWORD GAP REPORT")
    print(
        f"  Coverage: {gap['coverage_pct']}%  "
        f"({len(gap['present'])}/{gap['total_jd_terms']} JD terms in tailoring)"
    )
    print(f"{'='*60}")

    missing = gap["missing"]
    if not missing:
        print("  No gaps — all JD terms accounted for.\n")
        return

    two_grams = [t for t in missing if " " in t][:top_n]
    one_grams = [t for t in missing if " " not in t][:top_n]

    if two_grams:
        print(f"\n  MISSING PHRASES (consider adding to summary/bullets):")
        for t in two_grams:
            print(f"    - {t}")

    if one_grams:
        print(f"\n  MISSING KEYWORDS:")
        for t in one_grams:
            print(f"    - {t}")

    print()


def save_gap_report(gap: dict, company: str, role: str, output_dir: Path) -> Path:
    """Save gap report as a plain text file alongside research artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", f"{company}_{role}".lower()).strip("_")[:60]
    out = output_dir / f"keyword_gap_{slug}.txt"

    lines = [
        f"KEYWORD GAP REPORT — {company} / {role}",
        f"Coverage: {gap['coverage_pct']}% "
        f"({len(gap['present'])}/{gap['total_jd_terms']} JD terms found in tailoring)",
        "",
        "MISSING PHRASES (2-grams):",
        *[f"  - {t}" for t in gap["missing"] if " " in t],
        "",
        "MISSING KEYWORDS:",
        *[f"  - {t}" for t in gap["missing"] if " " not in t],
        "",
        "PRESENT TERMS (spot-check):",
        *[f"  + {t}" for t in list(gap["present"])[:40]],
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def _extract_terms(text: str) -> set[str]:
    text = text.lower()
    words = re.findall(r"[a-z][a-z0-9\-]+", text)
    words = [w for w in words if len(w) >= _MIN_WORD_LEN and w not in _STOP_WORDS]

    terms: set[str] = set(words)
    for i in range(len(words) - 1):
        terms.add(f"{words[i]} {words[i + 1]}")

    return terms


def _tailoring_text(data: dict) -> str:
    """Flatten tailoring JSON to a single text blob for comparison."""
    parts = [
        data.get("headline", ""),
        data.get("summary", ""),
        " ".join(data.get("competencies", [])),
        " ".join(data.get("technical_skills", [])),
        data.get("cover_letter", ""),
    ]
    for mod in data.get("experience_modifications", []):
        parts.append(mod.get("replacement_lead_in", ""))
        parts.extend(mod.get("replacement_bullets", []))
    return " ".join(parts)
