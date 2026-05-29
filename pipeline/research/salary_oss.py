"""Open-source salary intelligence.

Two data sources, no paid API:
  1. BLS OES public API  — national median/mean wage by occupation (SOC code)
  2. DOL H-1B LCA data   — actual filed wages at named employers (optional, requires download)

The H-1B dataset is the stronger signal when the role is commonly filed.
BLS OES is the reliable fallback for everything else.

H-1B data setup (optional but recommended):
  Download the annual disclosure file from:
    https://www.dol.gov/agencies/eta/foreign-labor/performance
  Point config oss.h1b_lca_csv_path at the downloaded CSV.
  On first use a SQLite cache is built automatically.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import requests

from utils.config import get
from utils.logging import get_logger

logger = get_logger(__name__)

_BLS_API = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
_TIMEOUT = 10

# SOC code mapping: normalized title keywords → (SOC code, BLS series suffix, label)
# SOC → BLS national mean annual wage series: OEU00000000000000{soc_nodash}04
_SOC_MAP: list[tuple[list[str], str, str]] = [
    (["operations manager", "operations director", "vp operations", "vp of operations",
      "director of operations", "sr director operations"],
     "110210", "General & Operations Managers"),

    (["supply chain", "logistics", "distribution", "transportation", "warehouse",
      "network optimization", "supply chain director", "supply chain manager"],
     "113071", "Transportation/Storage/Distribution Managers"),

    (["analytics", "data", "business intelligence", "bi director", "director of analytics",
      "data science", "data engineering"],
     "152051", "Data Scientists"),

    (["information systems", "it director", "technology director", "digital transformation",
      "cio", "vp technology", "director of technology"],
     "113021", "Computer & Info Systems Managers"),

    (["consulting", "management analyst", "principal consultant", "strategy",
      "management consulting"],
     "131111", "Management Analysts"),

    (["procurement", "purchasing", "sourcing", "category management"],
     "113061", "Purchasing Managers"),

    (["financial", "finance director", "vp finance", "cfo", "controller"],
     "113031", "Financial Managers"),
]


def fetch(company: str, role: str, location: str = "") -> dict:
    """
    Return salary estimate dict compatible with salary_intel.fetch() output.
    Fields: estimated_range, confidence, source_note, raw_text
    """
    soc_code, soc_label = _match_soc(role)

    bls_result = _fetch_bls(soc_code, soc_label) if soc_code else {}
    h1b_result = _fetch_h1b(company, role)

    # Combine: prefer H-1B when available (company-specific, more precise)
    if h1b_result.get("estimated_range"):
        conf = "high" if h1b_result.get("count", 0) >= 3 else "medium"
        note = (
            f"DOL H-1B LCA filings for {company}: "
            f"{h1b_result.get('count', '?')} matching records. "
            f"BLS {soc_label} national median: {bls_result.get('median', 'N/A')}."
        )
        return {
            "estimated_range": h1b_result["estimated_range"],
            "confidence": conf,
            "source_note": note,
            "raw_text": note,
        }

    if bls_result.get("median"):
        note = (
            f"BLS OES national data for {soc_label} (SOC {soc_code[:2]}-{soc_code[2:]}): "
            f"median {bls_result['median']}, mean {bls_result.get('mean', 'N/A')}. "
            f"Director-level comp typically runs 20-40% above BLS median due to scope premium."
        )
        # Rough Director-level uplift on BLS median
        estimated = _bls_to_director_range(bls_result.get("median_raw", 0))
        return {
            "estimated_range": estimated or bls_result["median"],
            "confidence": "low",
            "source_note": note,
            "raw_text": note,
        }

    return {
        "estimated_range": "",
        "confidence": "none",
        "source_note": "OSS salary check: no matching BLS or H-1B data found for this role.",
        "raw_text": "",
    }


def _match_soc(role: str) -> tuple[str, str]:
    role_lower = role.lower()
    for keywords, soc, label in _SOC_MAP:
        if any(kw in role_lower for kw in keywords):
            return soc, label
    # Default: General & Operations Managers
    return "110210", "General & Operations Managers"


def _fetch_bls(soc_code: str, label: str) -> dict:
    # BLS v1 series ID: OEU + 00 (national) + 0000000 (all areas) + 000000 (all industries)
    # + 6-digit occupation + 04 (mean annual wage) or 03 (median annual wage)
    series_mean   = f"OEU0000000000000{soc_code}04"
    series_median = f"OEU0000000000000{soc_code}03"

    try:
        payload = {
            "seriesid": [series_mean, series_median],
            "startyear": "2023",
            "endyear": "2025",
        }
        resp = requests.post(_BLS_API, json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        result = {}
        for series in data.get("Results", {}).get("series", []):
            sid = series.get("seriesID", "")
            latest = series.get("data", [{}])[0]
            val_str = latest.get("value", "").replace(",", "")
            try:
                val = int(float(val_str))
            except (ValueError, TypeError):
                continue

            if sid.endswith("04"):
                result["mean"] = f"${val:,}"
                result["mean_raw"] = val
            elif sid.endswith("03"):
                result["median"] = f"${val:,}"
                result["median_raw"] = val

        logger.info("BLS OES result for %s: %s", label, result)
        return result

    except Exception as exc:
        logger.debug("BLS API fetch failed for %s: %s", soc_code, exc)
        return {}


def _bls_to_director_range(median_raw: int) -> str:
    if not median_raw:
        return ""
    low = int(median_raw * 1.20)
    high = int(median_raw * 1.50)
    # Round to nearest 5K
    low = round(low / 5000) * 5000
    high = round(high / 5000) * 5000
    return f"${low:,} - ${high:,}"


def _fetch_h1b(company: str, role: str) -> dict:
    csv_path = get("oss.h1b_lca_csv_path", "")
    if not csv_path:
        return {}

    csv_file = Path(csv_path).expanduser()
    if not csv_file.exists():
        logger.debug("H-1B LCA CSV not found at %s", csv_file)
        return {}

    db_path = csv_file.parent / (csv_file.stem + "_cache.sqlite")

    try:
        con = sqlite3.connect(str(db_path))
        if not _h1b_table_exists(con):
            logger.info("Building H-1B LCA SQLite cache from %s (one-time)…", csv_file)
            _build_h1b_cache(con, csv_file)

        company_q = f"%{company.lower()}%"
        role_parts = re.sub(r"\b(of|and|the|for|at|in|a|an)\b", "", role.lower()).split()
        role_clause = " AND ".join(f"LOWER(job_title) LIKE ?" for _ in role_parts)
        params = [company_q] + [f"%{p}%" for p in role_parts if p]

        cur = con.execute(
            f"""
            SELECT wage_offered, COUNT(*) as n
            FROM lca
            WHERE LOWER(employer_name) LIKE ?
              AND {role_clause if role_clause else '1=1'}
              AND wage_offered > 50000
            GROUP BY wage_offered
            ORDER BY n DESC
            LIMIT 20
            """,
            params,
        )
        rows = cur.fetchall()
        con.close()

        if not rows:
            return {}

        wages = [r[0] for r in rows]
        low = int(min(wages))
        high = int(max(wages))
        median = sorted(wages)[len(wages) // 2]
        count = len(wages)

        return {
            "estimated_range": f"${low:,} - ${high:,} (median ${median:,})",
            "count": count,
        }

    except Exception as exc:
        logger.warning("H-1B LCA lookup failed: %s", exc)
        return {}


def _h1b_table_exists(con: sqlite3.Connection) -> bool:
    cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lca'")
    return cur.fetchone() is not None


def _build_h1b_cache(con: sqlite3.Connection, csv_file: Path) -> None:
    import csv

    con.execute("""
        CREATE TABLE IF NOT EXISTS lca (
            employer_name TEXT,
            job_title TEXT,
            wage_offered REAL,
            worksite_city TEXT,
            worksite_state TEXT,
            decision_date TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_employer ON lca (LOWER(employer_name))")

    # Column names vary by year — try common variants
    COL_EMPLOYER = ("EMPLOYER_NAME", "EMPLOYER NAME", "LEGALNAME")
    COL_TITLE    = ("JOB_TITLE", "SOC_TITLE", "JOB TITLE", "POSITION_TITLE")
    COL_WAGE     = ("WAGE_RATE_OF_PAY_FROM", "WAGE_OFFERED_FROM", "WAGE_RATE_FROM", "PREVAILING_WAGE")
    COL_CITY     = ("WORKSITE_CITY", "EMPLOYER_CITY")
    COL_STATE    = ("WORKSITE_STATE", "EMPLOYER_STATE")
    COL_DATE     = ("DECISION_DATE", "CASE_RECEIVED_DATE", "RECEIVED_DATE")

    def pick(header: list[str], candidates: tuple[str, ...]) -> str | None:
        upper = [h.upper().strip() for h in header]
        for c in candidates:
            if c in upper:
                return header[upper.index(c)]
        return None

    rows_inserted = 0
    with open(csv_file, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        col_emp   = pick(header, COL_EMPLOYER)
        col_title = pick(header, COL_TITLE)
        col_wage  = pick(header, COL_WAGE)
        col_city  = pick(header, COL_CITY)
        col_state = pick(header, COL_STATE)
        col_date  = pick(header, COL_DATE)

        if not all([col_emp, col_title, col_wage]):
            logger.warning("H-1B CSV: could not identify required columns in %s", csv_file.name)
            return

        batch = []
        for row in reader:
            try:
                wage_raw = str(row.get(col_wage, "") or "").replace(",", "").replace("$", "").strip()
                wage = float(wage_raw) if wage_raw else 0.0
                # Annualize hourly wages (H-1B sometimes files hourly)
                if 0 < wage < 500:
                    wage *= 2080
                if wage < 30000:
                    continue
                batch.append((
                    str(row.get(col_emp, "") or "")[:200],
                    str(row.get(col_title, "") or "")[:200],
                    wage,
                    str(row.get(col_city or "", "") or "")[:100],
                    str(row.get(col_state or "", "") or "")[:50],
                    str(row.get(col_date or "", "") or "")[:20],
                ))
                if len(batch) >= 5000:
                    con.executemany("INSERT INTO lca VALUES (?,?,?,?,?,?)", batch)
                    rows_inserted += len(batch)
                    batch = []
            except (ValueError, KeyError):
                continue

        if batch:
            con.executemany("INSERT INTO lca VALUES (?,?,?,?,?,?)", batch)
            rows_inserted += len(batch)

    con.commit()
    logger.info("H-1B LCA cache built: %d rows from %s", rows_inserted, csv_file.name)
