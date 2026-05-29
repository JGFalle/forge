"""Open-source people intel generation.

Gathers structured data from free sources, then uses an OSS LLM to write
the same markdown format as intel_generator.py so the rest of the pipeline
(pdf_renderer, outreach_extractor) works without changes.

Data sources (all free, no paid API):
  - edgartools  — SEC filings: named executives in DEF 14A, 8-K transcripts
  - ddgs        — LinkedIn profile discovery via search
  - Wikipedia-API — company background and recent history
  - linkedin-api  — optional, requires a LinkedIn account credential
  - Hunter.io    — optional, 25 free email lookups/month

The OSS LLM (Groq/Gemini/Ollama) writes the final markdown from the
gathered data using the same prompt format as the cloud version.
"""

from __future__ import annotations

import re
from pathlib import Path

from utils.config import get
from utils.logging import get_logger

logger = get_logger(__name__)


def generate(
    jd_text: str,
    company: str,
    role: str,
    slug: str,
    output_dir: Path,
) -> Path | None:
    """Gather OSS intel and write a people intel markdown. Returns saved path."""
    data = _gather(company, role)

    md = _render_markdown(company, role, data, jd_text)

    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{slug}_people_intel.md"
    out.write_text(md, encoding="utf-8")
    logger.info("OSS people intel saved: %s", out)
    return out


# ── Data gatherers ─────────────────────────────────────────────────────────────

def _gather(company: str, role: str) -> dict:
    data: dict = {
        "executives": [],
        "linkedin_profiles": [],
        "company_summary": "",
        "recent_news": [],
        "email_format": "",
        "strategy_context": "",
    }

    data["executives"]       = _sec_executives(company)
    data["linkedin_profiles"] = _ddgs_linkedin(company, role)
    data["company_summary"]  = _wikipedia_summary(company)
    data["recent_news"]      = _ddgs_news(company)
    data["email_format"]     = _hunter_email_format(company)
    data["strategy_context"] = _sec_earnings_context(company)

    return data


def _sec_executives(company: str) -> list[dict]:
    """Pull named executives from SEC DEF 14A / 8-K filings via edgartools."""
    execs = []
    try:
        from edgar import Company as EdgarCompany
        ec = EdgarCompany(company)

        # Try proxy statement for named officers
        filings = ec.get_filings(form="DEF 14A")
        if filings:
            latest = filings[0]
            doc = latest.obj()
            text = str(doc)[:8000]
            # Extract names near title keywords
            patterns = [
                r"([A-Z][a-z]+ [A-Z][a-z]+(?:\s[A-Z][a-z]+)?)[,\s]+"
                r"(?:Chief|Vice President|SVP|EVP|Senior Vice President|"
                r"Director|Head of|VP)[^,\n]{0,80}"
            ]
            seen = set()
            for pat in patterns:
                for m in re.finditer(pat, text):
                    name = m.group(1).strip()
                    context = m.group(0).strip()
                    if name not in seen and len(name.split()) >= 2:
                        seen.add(name)
                        execs.append({"name": name, "context": context[:120], "source": "SEC DEF 14A"})
                        if len(execs) >= 6:
                            break

    except ImportError:
        logger.debug("edgartools not installed")
    except Exception as exc:
        logger.debug("SEC exec lookup failed for %s: %s", company, exc)

    return execs


def _sec_earnings_context(company: str) -> str:
    """Extract supply chain / operations language from recent 8-K earnings calls."""
    try:
        from edgar import Company as EdgarCompany
        ec = EdgarCompany(company)
        filings = ec.get_filings(form="8-K")
        if not filings:
            return ""

        latest = filings[0]
        doc = latest.obj()
        text = str(doc)[:6000]

        # Look for supply chain / operations / strategy language
        kw_pattern = re.compile(
            r".{0,200}(supply chain|operations|logistics|automation|digital|"
            r"transformation|analytics|AI|technology|efficiency|headcount|"
            r"restructur|optimization).{0,200}",
            re.IGNORECASE,
        )
        hits = []
        for m in kw_pattern.finditer(text):
            snippet = m.group(0).replace("\n", " ").strip()
            if len(snippet) > 60 and snippet not in hits:
                hits.append(snippet[:250])
            if len(hits) >= 3:
                break

        return " | ".join(hits) if hits else ""

    except ImportError:
        return ""
    except Exception as exc:
        logger.debug("SEC earnings context failed for %s: %s", company, exc)
        return ""


def _ddgs_linkedin(company: str, role: str) -> list[dict]:
    """Find LinkedIn profiles of people at the company in relevant roles."""
    profiles = []
    try:
        from ddgs import DDGS
        role_kws = " OR ".join([
            f'"Director {_role_function(role)}"',
            f'"VP {_role_function(role)}"',
            f'"Head of {_role_function(role)}"',
        ])
        query = f'"{company}" ({role_kws}) site:linkedin.com/in/'

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=8))

        for r in results:
            url  = r.get("href", "")
            title = r.get("title", "")
            body  = r.get("body", "")
            if "linkedin.com/in/" not in url:
                continue
            name = title.split(" - ")[0].strip() if " - " in title else title[:40].strip()
            role_hint = title.split(" - ")[1].strip() if title.count(" - ") >= 1 else body[:80]
            profiles.append({
                "name": name,
                "linkedin_url": url,
                "role_hint": role_hint[:100],
                "source": "DDGS/LinkedIn",
            })
            if len(profiles) >= 5:
                break

    except ImportError:
        logger.debug("ddgs not installed — skipping LinkedIn discovery")
    except Exception as exc:
        logger.debug("DDGS LinkedIn search failed: %s", exc)

    return profiles


def _role_function(role: str) -> str:
    """Extract the functional keyword from a role title for targeted search."""
    kw_map = {
        "supply chain": "Supply Chain",
        "operations": "Operations",
        "logistics": "Logistics",
        "analytics": "Analytics",
        "digital": "Digital Transformation",
        "network": "Network Operations",
        "procurement": "Procurement",
        "transformation": "Transformation",
        "data": "Data",
        "strategy": "Strategy",
    }
    rl = role.lower()
    for k, v in kw_map.items():
        if k in rl:
            return v
    return "Operations"


def _wikipedia_summary(company: str) -> str:
    """Pull company summary from Wikipedia."""
    try:
        import wikipediaapi
        wiki = wikipediaapi.Wikipedia(
            user_agent="FORGE-Pipeline/1.0",
            language="en",
        )
        page = wiki.page(company)
        if page.exists():
            return page.summary[:800]
        # Try with "Inc." or "Corporation" suffix
        for suffix in (" Inc.", " Corporation", " Company", " LLC"):
            page = wiki.page(company + suffix)
            if page.exists():
                return page.summary[:800]
    except ImportError:
        logger.debug("Wikipedia-API not installed")
    except Exception as exc:
        logger.debug("Wikipedia lookup failed for %s: %s", company, exc)
    return ""


def _ddgs_news(company: str) -> list[str]:
    """Recent news headlines about the company (last 30 days)."""
    headlines = []
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            for r in ddgs.news(f'"{company}"', max_results=5, timelimit="m"):
                headlines.append(f"{r.get('title','')[:100]} ({r.get('date','')[:10]})")
                if len(headlines) >= 4:
                    break
    except Exception:
        pass
    return headlines


def _hunter_email_format(company: str) -> str:
    """Detect company email format via Hunter.io free tier (25 lookups/month)."""
    api_key = get("oss.hunter_api_key", "")
    if not api_key:
        return ""
    try:
        import requests
        domain = _guess_domain(company)
        resp = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "api_key": api_key, "limit": 5},
            timeout=8,
        )
        data = resp.json().get("data", {})
        fmt = data.get("pattern", "")
        if fmt:
            return f"Email pattern at {domain}: {fmt}@{domain}"
    except Exception as exc:
        logger.debug("Hunter.io lookup failed: %s", exc)
    return ""


def _guess_domain(company: str) -> str:
    clean = re.sub(r"\b(Inc|LLC|Corp|Co|Ltd|Group|Holdings|Solutions)\.?\b", "", company, flags=re.I)
    clean = re.sub(r"[^\w]", "", clean).lower()
    return f"{clean}.com"


# ── Markdown renderer ──────────────────────────────────────────────────────────

def _render_markdown(company: str, role: str, data: dict, jd_text: str) -> str:
    """Use an OSS LLM to write the people intel markdown from gathered data."""
    from utils.config import get as cfg
    from utils.oss_llm import available_provider, generate as llm_generate

    name = cfg("person.name", "")
    linkedin = cfg("person.linkedin", "")
    education = cfg("person.education", "")
    achievements = cfg("key_achievements", [])
    background = cfg("identity.primary", "") + (
        ". " + achievements[0] if achievements else ""
    )

    context_block = _build_context_block(company, data)

    prompt = f"""
You are writing a people intelligence document for a job application.
Format your output as markdown with these exact sections.

TARGET ROLE: {role} at {company}

CANDIDATE: {name} | {linkedin} | {education}
BACKGROUND: {background}

RESEARCHED DATA (use this to populate the sections below):
{context_block}

JD EXCERPT (for strategic context):
{jd_text[:2000]}

Write a complete people intelligence markdown with these sections:

# {company} — People Intelligence

## Business Unit & Strategic Intelligence
[2-3 paragraphs: what initiative or pressure is this role sitting inside,
 how leadership talks about this function publicly, what success looks like year one.
 Use the SEC/earnings data above if available. Be specific, not generic.]

## Target Role Context
[Why the role may be open, recent changes, what this tells us]

## Key Contacts to Pursue (Priority 1: Direct Outreach)
[For each person found in the data, use this format:]

### Full Name
- **Title:** [title]
- **LinkedIn:** [linkedin url or best guess]
- **Why they matter:** [1 sentence]

[Write a LinkedIn outreach message, 290-300 characters, ending with "{name}"]

## Priority 2: Warm-Path Contacts
[Additional contacts worth connecting with]

## Outreach Plan
[Send order and timing recommendations. Wed 8-10am ET is peak for LinkedIn.]

Keep all outreach messages peer-to-peer, not sycophantic.
No em dashes. Under 300 characters for LinkedIn messages.
""".strip()

    try:
        provider = available_provider()
        if not provider:
            logger.warning("No OSS LLM available — returning raw data markdown")
            return _fallback_markdown(company, role, data, name)
        response = llm_generate(prompt, max_tokens=3000)
        return response
    except Exception as exc:
        logger.warning("OSS LLM people intel failed: %s — using raw data", exc)
        return _fallback_markdown(company, role, data, name)


def _build_context_block(company: str, data: dict) -> str:
    parts = []

    if data["company_summary"]:
        parts.append(f"COMPANY (Wikipedia):\n{data['company_summary'][:500]}")

    if data["executives"]:
        execs_str = "\n".join(
            f"  - {e['name']}: {e['context'][:80]}" for e in data["executives"]
        )
        parts.append(f"EXECUTIVES (SEC filings):\n{execs_str}")

    if data["linkedin_profiles"]:
        li_str = "\n".join(
            f"  - {p['name']} | {p['role_hint'][:60]} | {p['linkedin_url']}"
            for p in data["linkedin_profiles"]
        )
        parts.append(f"LINKEDIN PROFILES FOUND:\n{li_str}")

    if data["strategy_context"]:
        parts.append(f"STRATEGY/EARNINGS LANGUAGE:\n{data['strategy_context'][:600]}")

    if data["recent_news"]:
        news_str = "\n".join(f"  - {h}" for h in data["recent_news"])
        parts.append(f"RECENT NEWS:\n{news_str}")

    if data["email_format"]:
        parts.append(f"EMAIL FORMAT: {data['email_format']}")

    return "\n\n".join(parts) if parts else f"No structured data found for {company}."


def _fallback_markdown(company: str, role: str, data: dict, name: str) -> str:
    """Plain markdown when no LLM is available — structured data only, no synthesis."""
    lines = [f"# {company} — People Intelligence (OSS Data)", ""]

    lines += ["## Business Unit & Strategic Intelligence", ""]
    if data["company_summary"]:
        lines.append(data["company_summary"][:600])
    if data["strategy_context"]:
        lines += ["", "**Earnings/Filing Language:**", data["strategy_context"][:400]]
    lines.append("")

    if data["recent_news"]:
        lines += ["## Recent News", ""]
        for h in data["recent_news"]:
            lines.append(f"- {h}")
        lines.append("")

    if data["executives"]:
        lines += ["## Key Contacts — SEC Filings", ""]
        for e in data["executives"]:
            lines.append(f"### {e['name']}")
            lines.append(f"- **Context:** {e['context']}")
            lines.append("")

    if data["linkedin_profiles"]:
        lines += ["## LinkedIn Profiles Found", ""]
        for p in data["linkedin_profiles"]:
            lines.append(f"### {p['name']}")
            lines.append(f"- **Role:** {p['role_hint']}")
            lines.append(f"- **LinkedIn:** {p['linkedin_url']}")
            lines.append("")

    if data["email_format"]:
        lines += ["## Email Format", f"- {data['email_format']}", ""]

    lines += [
        "## Note",
        "This intel was generated from open-source data (SEC, Wikipedia, search).",
        "No OSS LLM was available to synthesize outreach messages.",
        "Install Groq, Gemini, or Ollama and re-run for full message generation.",
    ]

    return "\n".join(lines)
