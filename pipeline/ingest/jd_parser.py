"""Parse structured fields from raw JD text.

Best-effort extraction using regex heuristics. All fields are optional;
callers should handle empty strings gracefully.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class ParsedJD:
    company: str = ""
    role: str = ""
    location: str = ""
    req_number: str = ""
    salary_range: str = ""
    ats_system: str = ""       # e.g. "Workday", "Greenhouse", "Taleo"
    remote_ok: bool = False
    apply_by_date: str = ""    # ISO date string YYYY-MM-DD if found, else ""
    raw_text: str = ""
    key_requirements: list[str] = field(default_factory=list)


_SALARY_PATTERN = re.compile(
    r"\$[\d,]+(?:\.\d+)?(?:\s*[-–]\s*\$[\d,]+(?:\.\d+)?)?(?:\s*(?:per year|/yr|annually|k))?",
    re.IGNORECASE,
)
_REQ_NUM_PATTERN = re.compile(
    r"\b(?:Job\s*(?:ID|#|Number)|Req(?:uisition)?\s*(?:ID|#|Number)?|JR|REQ)[:\s#]*([A-Z0-9\-]*\d[A-Z0-9\-]*)",
    re.IGNORECASE,
)
_ATS_PATTERNS = {
    "Workday": re.compile(r"myworkdayjobs\.com|workday\.com", re.IGNORECASE),
    "Greenhouse": re.compile(r"greenhouse\.io", re.IGNORECASE),
    "Taleo": re.compile(r"taleo\.net", re.IGNORECASE),
    "iCIMS": re.compile(r"icims\.com", re.IGNORECASE),
    "Lever": re.compile(r"lever\.co", re.IGNORECASE),
    "SmartRecruiters": re.compile(r"smartrecruiters\.com", re.IGNORECASE),
}
_REMOTE_PATTERN = re.compile(r"\b(remote|hybrid|work from home|WFH)\b", re.IGNORECASE)

# Apply-by date, matches "apply by", "applications close", "deadline", "posting closes"
# followed by a date in various formats
_APPLY_BY_TRIGGERS = re.compile(
    r"(?:apply\s+by|applications?\s+(?:close[sd]?|due|deadline)|"
    r"posting\s+closes?|position\s+closes?|must\s+apply\s+by|"
    r"applications?\s+accepted\s+through|deadline[\s:]+)"
    r"\s*[:\-]?\s*"
    r"("
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4}"  # "May 25, 2026"
    r"|\d{1,2}/\d{1,2}/\d{4}"                 # "05/25/2026"
    r"|\d{4}-\d{2}-\d{2}"                      # "2026-05-25"
    r"|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?"             # "May 25" (no year)
    r")",
    re.IGNORECASE,
)


def parse(raw_text: str) -> ParsedJD:
    jd = ParsedJD(raw_text=raw_text)

    salary_match = _SALARY_PATTERN.search(raw_text)
    if salary_match:
        jd.salary_range = salary_match.group(0).strip()

    req_match = _REQ_NUM_PATTERN.search(raw_text)
    if req_match:
        jd.req_number = req_match.group(1).strip()

    for system, pattern in _ATS_PATTERNS.items():
        if pattern.search(raw_text):
            jd.ats_system = system
            break

    if _REMOTE_PATTERN.search(raw_text):
        jd.remote_ok = True

    apply_match = _APPLY_BY_TRIGGERS.search(raw_text)
    if apply_match:
        jd.apply_by_date = _parse_date_string(apply_match.group(1).strip())

    jd.key_requirements = _extract_requirements(raw_text)

    return jd


def _parse_date_string(s: str) -> str:
    """Normalize a date string to ISO YYYY-MM-DD. Returns '' if unparseable."""
    s = re.sub(r"(?:st|nd|rd|th)", "", s)  # strip ordinal suffixes
    formats = [
        "%B %d, %Y", "%b %d, %Y",  # May 25, 2026 / May 25, 2026
        "%B %d %Y",  "%b %d %Y",   # May 25 2026
        "%m/%d/%Y",                 # 05/25/2026
        "%Y-%m-%d",                 # 2026-05-25
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    # No year, anchor to current year, promote to next if already past
    today = date.today()
    for fmt in ["%B %d", "%b %d"]:
        try:
            parsed = datetime.strptime(f"{s.strip()} {today.year}", fmt + " %Y")
            if parsed.date() < today:
                parsed = parsed.replace(year=today.year + 1)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def _extract_requirements(text: str) -> list[str]:
    """Pull bullet-style requirement lines from the JD text."""
    lines = text.splitlines()
    results: list[str] = []
    in_requirements = False

    req_headers = re.compile(r"^\s*(?:requirements?|qualifications?|what you.ll need|what we.re looking for|minimum qualifications?)", re.IGNORECASE)
    bullet = re.compile(r"^\s*[•\-\*•]\s+(.+)$")

    for line in lines:
        if req_headers.match(line):
            in_requirements = True
            continue
        if in_requirements:
            m = bullet.match(line)
            if m:
                results.append(m.group(1).strip())
            elif line.strip() == "" and results:
                break

    return results[:20]
