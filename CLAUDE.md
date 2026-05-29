# FORGE — Session Context for AI Collaborators

Read this before doing anything.

---

## What FORGE Is

FORGE (Fit-scored Opportunity Research, Generation & Evaluation) is an open-source job application pipeline. Drop a JD PDF and it produces a tailored resume, cover letter, people intel, keyword gap report, and exec summary. It also runs job discovery, tracks applications, and drafts follow-ups.

FORGE is a genericized, public version of a private pipeline called PACE. All personal data was stripped — everything user-specific now lives in `config/config.yaml`.

GitHub: https://github.com/JGFalle/forge

---

## Two Modes — Both Fully Supported

**Cloud mode** (best quality): Anthropic Claude for generation, Perplexity for research, SerpApi for discovery.

**OSS mode** (zero cost): Groq / Gemini / Ollama for generation, ddgs + feedparser + Wayback CDX for research, python-jobspy + direct ATS APIs for discovery. Auto-detected when paid API keys are absent — no code changes needed.

---

## Architecture Overview

```
run.py                          ← single entry point
config/config.yaml              ← ALL user-specific data lives here
pipeline/
  ingest/                       ← PDF extraction, JD parsing
  assessment/                   ← fit scorer (STRONG_FIT / STRETCH / HARD_PASS)
  research/                     ← tailoring JSON gen, people intel, viability, salary
    viability_checker.py        ← ghost job check (Perplexity or → viability_oss.py)
    salary_intel.py             ← salary data (Perplexity or → salary_oss.py)
    intel_generator.py          ← people intel (Claude or → people_oss.py)
    viability_oss.py            ← OSS: ddgs + feedparser + Wayback CDX
    salary_oss.py               ← OSS: BLS OES API + DOL H-1B LCA SQLite
    people_oss.py               ← OSS: edgartools + ddgs + Wikipedia + OSS LLM
  tailoring/
    summary_council.py          ← 3-model review (Perplexity or → council_oss.py)
    council_oss.py              ← OSS: Groq + Gemini + Ollama panel
    docx_modifier.py            ← applies tailoring JSON to base resume DOCX
    json_validator.py           ← validates tailoring JSON schema (dynamic from config)
    ats_checker.py              ← ATS compatibility scoring (no API needed)
  cover_letter/                 ← DOCX + plain text generation
  people_intel/                 ← markdown → PDF rendering, outreach extraction
  output/                       ← exec summary PDF, folder structure
  tracker/                      ← application tracker, dashboard, follow-ups
  discovery/
    runner.py                   ← orchestrates scrapers (routes cloud vs OSS)
    scrapers/
      serpapi_scraper.py        ← cloud: Google/Indeed/LinkedIn via SerpApi
      workday_scraper.py        ← Workday CXS API (no key needed)
      email_scraper.py          ← Gmail alerts scraper
      jobspy_scraper.py         ← OSS: python-jobspy (Indeed, ZipRecruiter, Google)
      ats_scraper.py            ← OSS: Greenhouse/Lever/Ashby direct APIs (no key)
  interview/                    ← interview prep generator
  linkedin/                     ← LinkedIn profile optimizer
utils/
  config.py                     ← YAML loader with dot-path access
  oss_llm.py                    ← unified OSS LLM client (Groq/Gemini/Ollama/Mistral)
  grammar.py, text.py, logging.py, progress.py
```

---

## Key Design Decisions

**Config-driven personalization.** No personal data in source files. `career_history`, `identity`, `key_achievements`, `target_companies`, `user_skills` all live in `config/config.yaml`. Prompts build dynamically from config at call time.

**Graceful degradation.** When a paid API key is absent, the relevant module auto-falls back to its OSS counterpart. The caller sees the same return shape either way.

**DOCX anchor text.** `docx_modifier.py` finds resume sections by searching for `anchor_text` strings defined in `career_history` config. Users must ensure their DOCX contains those strings verbatim.

**Dynamic schema validation.** `json_validator.py` builds the `role_identifier` enum from `career_history[].id` in config — not hardcoded.

**OSS LLM routing.** `utils/oss_llm.py` tries providers in order: Groq → Gemini → Ollama → Mistral. Council uses `generate_multi()` which calls all configured voices in parallel.

---

## Config Sections (config/config.yaml)

| Section | Purpose |
|---|---|
| `person` | Name, email, phone, LinkedIn, resume filename, career_start_year |
| `career_history` | List of roles with id, anchor_text, modifiable, max_bullets |
| `identity` | primary, secondary, avoid_leading_with, target_levels |
| `key_achievements` | 2-4 proof points used in all prompts |
| `defensibility_notes` | Rules to prevent overclaiming |
| `target_companies` | tier1/tier2/tier3 lists for fit scoring and discovery |
| `comp_floors` | target_floor, hard_filter_floor, sr_target, vp_target |
| `user_skills` | For Workday skills recommendations |
| `discovery` | target_titles, search_locations, alert_email_label, workday_tenants |
| `tailoring` | summary_max_chars, cover_letter_words_max, council_enabled, review_gate |
| `oss` | preferred_llm, council_voices, h1b_lca_csv_path, ats_boards, hunter_api_key |
| `gdrive` | enabled, mount_base, applications_folder |
| `pipeline` | drop_timeout_seconds, gdrive_sync, open_output_folder |

---

## OSS Mode Setup (zero cost)

Minimum: `GROQ_API_KEY` in `.env` (free at console.groq.com, no credit card).

Full OSS install: `pip install -e ".[oss]"`

For discovery without SerpApi: configure `oss.ats_boards` in config for direct Greenhouse/Lever/Ashby scraping. No rate limits.

For H-1B salary data: download DOL LCA CSV from dol.gov/agencies/eta/foreign-labor/performance, set `oss.h1b_lca_csv_path` in config. SQLite cache builds on first run.

---

## Important Files

- `assets/README.md` — how to structure the base resume DOCX (anchor text guide)
- `.env.template` — all API keys documented with free tier links
- `generate_docs.py` — run to regenerate the PDF change documentation

---

## Project Rules

- One module does one thing. If a function does two things, split it.
- All user config lives in `config/config.yaml`. No hardcoded personal data in source files.
- Log at INFO by default. DEBUG behind a flag. No print() in pipeline modules.
- Tests in `tests/`. Match module structure.
- Run `ruff check .` before committing.
- OSS fallbacks match the return shape of their cloud counterpart exactly.
