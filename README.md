# FORGE
### Fit-scored Opportunity Research, Generation & Evaluation

Precision job application pipeline. Drop a JD PDF. Get a tailored resume, cover letter, and hiring manager intel in about 2 minutes.

Built for senior professionals who'd rather send 10 excellent applications than 100 mediocre ones.

---

## What it does

Drop a job description PDF. The pipeline runs:

1. **Parses the JD** — company, role, location, salary, ATS system, key requirements
2. **Checks if the job is real** — Perplexity searches for ghost job signals before wasting your time
3. **Scores the fit** — Claude evaluates the role against your target profile across 4 dimensions: identity alignment, scope/level, comp, and company tier. Returns STRONG FIT / STRETCH / HARD PASS
4. **Generates a tailoring JSON** — Claude Sonnet writes role-specific resume modifications and a cover letter, then a 3-model council reviews and patches the output
5. **Review gate** — you can inspect and edit the JSON before anything renders
6. **Tailors your resume** — applies the JSON to your base DOCX, creates a named copy, runs ATS compatibility check
7. **Generates the cover letter** — DOCX plus plain text for ATS paste boxes, word count enforced
8. **People intel** — Claude researches the company and hiring team, writes LinkedIn outreach messages ready to copy-paste
9. **Keyword gap report** — shows which JD terms are missing from your materials
10. **Executive summary PDF** — comp data, JD overview, Workday skills, resume changes
11. **Google Drive sync** — organizes everything into company-specific folders (optional)

---

## Requirements

- Python 3.11+
- Anthropic API key (required)
- Perplexity API key (strongly recommended — unlocks ghost job detection, salary intel, and council review)
- Your resume as a `.docx` file

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/yourusername/forge.git
cd forge
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

### 2. Set up your API keys

```bash
cp .env.template .env
```

Open `.env` and fill in your keys. At minimum you need `ANTHROPIC_API_KEY`. See `.env.template` for what each key unlocks.

### 3. Configure your profile

Open `config/config.yaml`. This is the main file — everything about you and your search lives here.

Work through each section top to bottom:

**`person`** — your name, email, phone, LinkedIn, resume filename

**`career_history`** — your roles, most-recent first. The `id` field becomes the identifier in the tailoring JSON. The `anchor_text` must match text in your DOCX so the script can find the right section to modify. See `assets/README.md` for details.

**`identity`** — how you want to be positioned. `primary` is what Claude leads with in every headline and bullet. `avoid_leading_with` is what gets buried.

**`key_achievements`** — 2-4 specific proof points with dollar amounts and scale. These anchor the resume summary and fit scoring. Be specific.

**`target_companies`** — tiered list of your target companies. Tier 1 = dream operators, Tier 2 = tech-adjacent, Tier 3 = consulting bridge. Used by the fit assessor and discovery scorer.

**`comp_floors`** — your compensation floors. `hard_filter_floor` triggers an auto-HARD PASS if a JD explicitly posts below that number.

**`user_skills`** — your skill set, lowercase, Workday-compatible terms. Used for Workday skills recommendations in the exec summary.

**`discovery`** — target titles, search cities, and email alert config if you want the discovery pipeline.

### 4. Add your base resume

Place your resume DOCX in `assets/` named `base_resume.docx`.

Read `assets/README.md` for how to structure your DOCX so FORGE can find the right sections to modify. The short version: the `anchor_text` values in your config must match distinctive strings in your DOCX section headers.

### 5. Run it

```bash
python run.py path/to/job_description.pdf
```

Outputs go to `outputs/YYYY-MM-DD_company_role/`.

---

## Commands

### Main pipeline

```bash
# Full pipeline from a JD PDF
python run.py path/to/jd.pdf

# Override parsed company/role if the parser gets it wrong
python run.py path/to/jd.pdf --company "Acme Corp" --role "Director of Operations"

# Add insider context (overrides JD framing — useful when the title undersells the role)
python run.py path/to/jd.pdf --context "OVERRIDE: This role actually owns the full P&L..."
```

### Re-run specific stages

```bash
# Re-run council review on an existing application folder
python run.py --council outputs/2026-01-15_acme_director_ops/

# Regenerate resume only (uses existing tailoring JSON)
python run.py --regen-resume outputs/2026-01-15_acme_director_ops/

# Regenerate cover letter only
python run.py --regen-cover outputs/2026-01-15_acme_director_ops/

# Regenerate people intel only
python run.py --regen-intel outputs/2026-01-15_acme_director_ops/
```

### Tracker and follow-ups

```bash
# Open the application pipeline dashboard (HTML)
python run.py --tracker

# Draft a follow-up message for an application that's gone quiet
python run.py --draft-followup "Acme Corp" "Director of Operations"

# Generate a salary negotiation brief when you get an offer
python run.py --negotiate "Acme Corp" "Director of Operations"
python run.py --negotiate "Acme Corp" "Director of Operations" --offer-amount "$185K"
```

### Discovery

```bash
# Run job discovery scrapers and send a weekly digest email
python run.py --discover

# Gmail alerts only — lightweight, good for cron or launchd
python run.py --email-check

# Mark applications older than 30 days with no response as ghosted
python run.py --cleanup
```

### Other

```bash
# Generate interview prep brief
python run.py --prep "Acme Corp" "Director of Operations"

# LinkedIn profile optimization report (from a LinkedIn data export ZIP)
python run.py --linkedin-optimize ~/Downloads/LinkedIn_export.zip
```

---

## Turning off features you don't need

Edit `config/config.yaml`:

```yaml
tailoring:
  council_enabled: false    # skip the 3-model council review
  review_gate: false         # skip the manual JSON review step (fully automated)

pipeline:
  gdrive_sync: false         # don't sync to Google Drive
```

---

## Google Drive sync (optional)

If you want outputs automatically organized in GDrive:

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable the Drive API
3. Create OAuth 2.0 credentials (Desktop app type), download the JSON
4. Save it as `config/credentials.json`
5. Run the pipeline once — it'll open a browser for authorization
6. Set `gdrive.enabled: true` and `pipeline.gdrive_sync: true` in config

First-time auth creates `config/token.json`. Both files are gitignored.

---

## Discovery pipeline (optional)

The discovery pipeline scrapes job boards and your Gmail alerts to surface new roles.

Setup:
1. Set up job alert emails from LinkedIn/Indeed/Google to land in a Gmail label (default: `FORGE/Alerts`)
2. Set `GMAIL_APP_PASSWORD` in `.env`
3. Set `SERPAPI_API_KEY` in `.env` for board scraping
4. Configure `discovery.target_titles` and `discovery.search_locations` in config
5. Run `python run.py --discover`

For automated email checks, set up a cron job:

```bash
# Every 15 minutes
*/15 * * * * cd /path/to/forge && .venv/bin/python run.py --email-check
```

---

## Open-Source Mode (no paid APIs)

FORGE detects which API keys are present and automatically routes to the best available backend. You can run the full pipeline with zero paid APIs.

### How it works

| API key absent | FORGE does instead |
|---|---|
| No `ANTHROPIC_API_KEY` | Uses Groq / Gemini / Ollama for tailoring, cover letter, fit scoring |
| No `PERPLEXITY_API_KEY` | Ghost job check via `ddgs` + Google News RSS + Wayback CDX; salary intel via BLS OES + H-1B LCA data; council review via Groq + Gemini + Ollama panel |
| No `SERPAPI_API_KEY` | Job discovery via `python-jobspy` (Indeed, ZipRecruiter, Google) + direct ATS APIs (Greenhouse, Lever, Ashby) |

The switch is automatic — just leave the key unset.

### OSS Mode Quick Start

**Minimum (one free key):**
```bash
# Get a free Groq API key at console.groq.com (no credit card)
echo "GROQ_API_KEY=gsk_..." >> .env
pip install -e ".[oss]"
python run.py path/to/jd.pdf
```

**Full OSS (zero ongoing cost, offline capable):**
```bash
# Install Ollama from ollama.com, then pull a model
ollama pull llama3.1:8b
ollama pull mistral:7b

# Get free Groq and Gemini keys for council diversity
echo "GROQ_API_KEY=gsk_..." >> .env
echo "GOOGLE_API_KEY=AIza..." >> .env

pip install -e ".[oss]"
python run.py path/to/jd.pdf
```

**Discovery without SerpApi:**
```bash
# JobSpy scrapes Indeed, ZipRecruiter, Google Jobs — no key needed
# Add ATS boards for direct company career page access
pip install python-jobspy

# Configure target ATS boards in config.yaml under oss.ats_boards
python run.py --discover
```

### OSS Pros and Cons

#### Ghost Job Viability Check

| | Cloud (Perplexity) | OSS (ddgs + feedparser + Wayback CDX) |
|---|---|---|
| **Quality** | Excellent — live web reasoning | Good — raw signals without synthesis |
| **What you get** | AI-structured risk verdict with sourced evidence | Layoff headlines + posting age check |
| **What you miss** | Nothing significant | No narrative summary of signals |
| **Cost** | ~$0.01/check | Free |
| **Reliability** | High | Good — depends on news availability |
| **Gotcha** | Requires PERPLEXITY_API_KEY | `ddgs` can 429 at high volume; add sleep |

#### Salary Intelligence

| | Cloud (Perplexity) | OSS (BLS OES + H-1B LCA) |
|---|---|---|
| **Quality** | Good — live Glassdoor/Levels.fyi data | Good for filed roles; weak where H-1B absent |
| **What you get** | Real-time market range with source citations | BLS national median + actual employer filings |
| **What you miss** | Nothing for most roles | Non-H-1B roles (domestic-only hires) get BLS only |
| **Best for** | Any role at any company | Supply chain / ops at large employers who file H-1B |
| **Setup** | Just the API key | Optional: download DOL H-1B CSV (~200MB, one-time) |
| **Gotcha** | Glassdoor/Levels.fyi bot-protected in 2026 | BLS median skews low vs. F500 Director comp |

To get the most out of OSS salary intel, download the H-1B LCA disclosure file:
1. Go to: https://www.dol.gov/agencies/eta/foreign-labor/performance
2. Download the most recent annual H-1B LCA disclosure data (Excel)
3. Save to CSV and set `oss.h1b_lca_csv_path` in `config/config.yaml`
4. On first run, FORGE builds a SQLite cache automatically (~30 seconds)

#### People Intel (Hiring Manager Research)

| | Cloud (Perplexity + Claude) | OSS (edgartools + ddgs + Wikipedia + OSS LLM) |
|---|---|---|
| **Quality** | Excellent — synthesized research + outreach messages | Fair-Good — structured data, LLM writes messages |
| **What you get** | Named contacts, earnings call quotes, ready-to-send outreach | SEC executive names, LinkedIn URLs, company context, LLM-written outreach |
| **What you miss** | Nothing | Live web synthesis; mid-level hiring manager names harder to find |
| **Best for** | Any company | F500 public companies (rich SEC data) |
| **Gotcha** | Costs API credits per application | `linkedin-api` has ban risk at high volume; SEC data covers VP+ only |

**Optional enhancers for OSS people intel:**
- **Hunter.io free tier** (25 lookups/month) — add `HUNTER_API_KEY` to `.env` and set `oss.hunter_api_key` in config to find email formats
- **linkedin-api** — `pip install linkedin-api` — requires a LinkedIn account credential, works at low volume (1-2 lookups/day)

#### Model Council

| | Cloud (Perplexity sonar panel) | OSS (Groq + Gemini + Ollama) |
|---|---|---|
| **Quality** | Excellent | Excellent — genuine model family diversity |
| **What you get** | 3 Perplexity models + aggregator | Llama 70B + Gemini Flash + Mistral 7B + aggregator |
| **Model diversity** | Same provider, different sizes | Different architectures (Meta / Google / Mistral) |
| **Speed** | Fast | Fast (Groq is 300-500 tok/s) |
| **Cost** | ~$0.02/council run | Free — Groq (1K req/day) + Gemini (250 req/day) + Ollama (unlimited) |
| **Gotcha** | Requires PERPLEXITY_API_KEY | Need at least one provider configured |

This is the strongest OSS replacement — genuinely different model families provide better diversity than same-provider size variants.

#### Discovery Scraping

| | Cloud (SerpApi) | OSS (python-jobspy + ATS direct APIs) |
|---|---|---|
| **Quality** | Good-Excellent | Good |
| **Boards covered** | Indeed, LinkedIn, Google, Glassdoor | Indeed, ZipRecruiter, Google (jobspy) + Greenhouse/Lever/Ashby (ATS direct) |
| **LinkedIn** | Reliable | Rate-limited; proxies needed for high volume |
| **Glassdoor** | Fair | Poor — heavily bot-protected |
| **ATS direct** | Not applicable | Excellent for Greenhouse/Lever/Ashby companies — zero rate limits |
| **Cost** | Paid per search | Free |
| **Gotcha** | $50/month for meaningful volume | JobSpy can hit LinkedIn rate limits; add proxy config for high volume |

Direct ATS APIs (Greenhouse, Lever, Ashby) are the hidden gem here — no rate limits, no bot protection, and many tech companies use them. Configure `oss.ats_boards` in `config/config.yaml` for your specific target companies.

### What stays the same in OSS mode

These features don't touch any paid API — they work identically regardless of mode:

- JD parsing and text extraction (pdfplumber)
- Resume DOCX modification (python-docx)
- ATS compatibility scoring
- Keyword gap analysis
- Cover letter word count and em-dash validation
- Application tracker
- Google Drive sync
- Pipeline dashboard

### OSS mode quality summary

| Feature | OSS quality vs. cloud |
|---|---|
| Ghost job check | ~80% — signals without synthesis |
| Salary intel | ~75% — good for H-1B-heavy roles, weaker for domestic-only |
| People intel | ~55% — structured data good, narrative synthesis limited |
| Model council | ~90% — different architecture families, genuinely comparable |
| Discovery scraping | ~85% — slight LinkedIn gap vs. SerpApi |
| Resume/cover letter generation | ~80% — quality gap narrows with Llama 70B or Gemini |

---

## Output folder structure

```
outputs/
└── 2026-01-15_acme_corp_director_of_operations/
    ├── jd/                  original JD PDF
    ├── research/            fit assessment, salary intel, gap report, council review PDF
    ├── tailoring_json/      the Claude-generated tailoring JSON (key artifact — edit this)
    ├── resume/              tailored resume DOCX
    ├── cover_letter/        cover letter DOCX + plain text version
    ├── people_intel/        hiring manager intel PDF + outreach messages
    └── run.log              full pipeline log for this application
```

---

## Troubleshooting

**"No 'cover_letter' field in tailoring JSON"**
The generation failed or returned malformed output. Check the JSON file in `tailoring_json/` and re-run with `--regen-cover`.

**DOCX modification isn't finding my sections**
Check that `anchor_text` in your config exactly matches text in your DOCX. The search is case-insensitive. See `assets/README.md`.

**ATS score is low**
The ATS report in `research/` shows what's causing the deduction. Common culprits: tables in the resume body, text boxes, images.

**Discovery returns no results**
Check that your `discovery.target_titles` match real job board titles exactly, and that `SERPAPI_API_KEY` is set.

**Google Drive auth loop**
Delete `config/token.json` and re-run — it'll trigger a fresh OAuth flow.

**Council review errors**
The council needs Perplexity. Check `PERPLEXITY_API_KEY` in `.env`. You can disable it with `tailoring.council_enabled: false` in config.

---

## Tech stack

- Claude Sonnet (Anthropic) — tailoring JSON, cover letter, people intel, fit assessment
- Perplexity sonar-pro — ghost job detection, salary research, council review
- pdfplumber — JD text extraction
- python-docx — resume DOCX manipulation
- ReportLab — PDF generation (people intel, exec summary)
- SerpApi — job board scraping (optional)
- Google Drive API — output sync (optional)

---

## The philosophy

The job market has a trust problem on both sides. Candidates spray AI-generated applications everywhere. Companies post ghost jobs. Everyone's defensive.

The edge is being the obvious non-game-player. Every feature here enforces quality over volume:

- Fit assessor runs before API credits are spent — no materials generated for hard passes
- Ghost job detection runs before the fit assessor
- The 3-model council catches fabrication and AI-tell language before it hits your resume
- The review gate lets you see and edit the tailoring JSON before anything renders
- 150-word cover letter max — signals confidence, not desperation

---

## License

MIT
