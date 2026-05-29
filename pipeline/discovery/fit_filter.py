"""Filter normalized jobs against the user's target profile from config."""

from __future__ import annotations

import re

from utils.config import get
from utils.logging import get_logger

logger = get_logger(__name__)

_SALARY_FLOOR = int(get("discovery.salary_filter_floor", 140000))

_EXCLUDE_TITLE_PATTERNS = [
    r"\bmanager\b(?!.*director)",
    r"\banalyst\b(?!.*director)",
    r"\bcoordinator\b",
    r"\bspecialist\b",
    r"\bassistant\b",
    r"\bassociate\b",              # includes Associate Director, below target level
    r"\bvp\b",
    r"\bvice president\b",
    r"\bpresident\b(?!.*vice)",
    r"\bceo\b",
    r"\bcoo\b",
    r"\bchief\b",
    r"\bintern\b",
    r"\bpart.?time\b",
    r"\bcontract\b(?!.*director)",
    r"\btemporary\b",
    r"\bstaffing\b",
    # Healthcare/clinical: "operations" in title doesn't make these relevant
    r"\bclinical\b",
    r"\bnursing\b",
    r"\bmedical\b(?!.*device)",    # allow Medical Device (supply chain context)
    r"\bhospital\b",
    r"\bpatient\b",
    r"\bphysician\b",
    r"\btherapy\b",
    r"\bbehavioral\b",
    # Other clearly off-domain functions
    r"\bfinance\b(?!.*operations)", # block "Finance Director" but allow "Finance Operations"
    r"\baccounting\b",
    r"\bsales\b(?!.*operations)",   # block "Sales Director" but allow "Sales Operations"
    r"\bmarketing\b(?!.*operations)",
    r"\bcreative\b",
    r"\bmaintenance\b",
    r"\bfacilities\b(?!.*operations)",
    r"\bconcessions\b",
    r"\bcatering\b",
    r"\bcasino\b",
    r"\bhuman resources\b",
    r"\bpreschool\b",
    r"\bpeople operations\b",      # HR rebranded
    r"\brevenue cycle\b",          # healthcare billing, not supply chain
    r"\brevenue operations\b",     # SaaS RevOps, not supply chain
    r"\blaboratory\b",             # lab automation != industrial automation
    r"\bcybersecurity\b",
    r"\bparalegal\b",
    r"\bprincip(al)?\b(?!.*consultant)",  # block Principal Engineer etc, keep Principal Consultant
]

_DIRECTOR_PATTERN = re.compile(
    r"\b(director|sr\.?\s+director|senior\s+director|principal\s+consultant)\b",
    re.IGNORECASE,
)

# Title must contain at least one of these to indicate ops/supply chain domain.
# This is the primary noise filter - cuts healthcare, sales, creative, etc. that
# happen to have "Director" in the title.
_DOMAIN_PATTERN = re.compile(
    r"\b(operations|supply chain|logistics|network|automation|"
    r"transformation|analytics|strategy|consulting|warehouse|"
    r"distribution|procurement|manufacturing|industrial|fulfillment|"
    r"inventory|planning|transportation|freight|optimization|"
    r"process improvement|continuous improvement|e.?commerce operations)\b",
    re.IGNORECASE,
)


def passes(job: dict) -> bool:
    title = job.get("title", "")
    company = job.get("company", "")
    salary = job.get("salary_range", "")

    if not _is_director_level(title):
        return False

    if not _is_domain_relevant(title):
        logger.debug("Filtered (domain): %s @ %s", title, company)
        return False

    if _is_excluded_title(title):
        return False

    if _is_excluded_company(company):
        return False

    if _salary_below_floor(salary, _SALARY_FLOOR):
        logger.debug("Filtered (salary): %s @ %s — %s", title, company, salary)
        return False

    return True


def filter_jobs(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    passed, rejected = [], []
    for job in jobs:
        if passes(job):
            passed.append(job)
        else:
            rejected.append(job)
    return passed, rejected


def _is_director_level(title: str) -> bool:
    return bool(_DIRECTOR_PATTERN.search(title))


def _is_domain_relevant(title: str) -> bool:
    return bool(_DOMAIN_PATTERN.search(title))


def _is_excluded_title(title: str) -> bool:
    title_lower = title.lower()
    for pat in _EXCLUDE_TITLE_PATTERNS:
        if re.search(pat, title_lower):
            return True
    return False


def _salary_below_floor(salary_str: str, floor: int) -> bool:
    """Return True only when salary is explicitly posted AND max bound is clearly below floor."""
    upper = _parse_salary_upper(salary_str)
    if upper is None:
        return False
    return upper < floor


def _parse_salary_upper(salary_str: str) -> int | None:
    """Extract annualized upper bound from a salary string. Returns None if unparseable."""
    if not salary_str:
        return None

    s = salary_str.lower().replace(",", "")
    is_hourly = bool(re.search(r"\b(hour|hr|/hr)\b", s))

    amounts: list[float] = []

    # K-suffix amounts: $120k or 120k
    for m in re.finditer(r"\$?([\d]+(?:\.\d+)?)\s*k\b", s):
        amounts.append(float(m.group(1)) * 1000)

    # Large plain numbers (5+ digits = annual dollar amount)
    if not amounts:
        for m in re.finditer(r"\$?([\d]{5,})", s):
            amounts.append(float(m.group(1)))

    # Hourly: small numbers with hour/hr context
    if not amounts and is_hourly:
        for m in re.finditer(r"\$?([\d]+(?:\.\d+)?)", s):
            val = float(m.group(1))
            if 10 <= val <= 500:
                amounts.append(val * 2080)

    if not amounts:
        return None

    return int(max(amounts))


def _is_excluded_company(company: str) -> bool:
    staffing_signals = [
        "staffing", "recruiting", "search group", "talent solutions",
        "executive search", "headhunter", "placement", "hire", "workforce",
        "kforce", "robert half", "adecco", "manpower", "randstad",
        "insight global", "heidrick", "spencer stuart", "russell reynolds",
        "egon zehnder", "korn ferry",
    ]
    company_lower = company.lower()
    return any(sig in company_lower for sig in staffing_signals)
