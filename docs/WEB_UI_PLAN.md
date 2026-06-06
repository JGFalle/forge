# FORGE Web UI: Plan (no-terminal version for non-technical users)

Goal: let someone who never opens a terminal drop a JD PDF in a browser and get
back the tailored resume, cover letter, people intel, and exec summary. "Doesn't
have to be fancy."

---

## Why this is now cheap to build

The PACE→FORGE port (Phase 3) extracts the entire pipeline into a single callable:

```python
from pipeline.core import process_jd, ProcessOptions, AutoDecisions, ProcessPrompts
result = process_jd(jd_pdf, options=ProcessOptions(...))   # -> ProcessResult
```

Every interactive decision is already abstracted:
- `ProcessPrompts` - callbacks (terminal supplies these today)
- `AutoDecisions` - fixed answers for non-interactive runs (bulk uses these)

A web app supplies decisions the same way bulk does. **The terminal and the web
become two thin front-ends over one core.** No pipeline logic gets duplicated.

---

## Recommended approach: two tiers

### Tier 1: Streamlit MVP (ship first, ~1 day)

Best fit for "for my friends, not fancy." Zero frontend code, built-in file
uploader, runs locally with one command. One file: `web/app.py`.

Flow:
1. **Config screen** - form-edit the per-user fields in `config.yaml` (name,
   email, phone, LinkedIn, career history, target companies, comp floors). Saved
   to a per-user config so each friend has their own. Resume DOCX uploaded once.
2. **Drop a JD** - file uploader for the JD PDF (+ optional company/role override,
   optional "why I'm applying" context box).
3. **Run** - call `process_jd(...)` with `interactive=False` and sensible
   `AutoDecisions` (proceed on ghost unless HIGH, skip HARD_PASS unless overridden,
   exec summary as a checkbox). Show a progress log via a status container.
4. **Results** - render fit verdict + ATS score, then download buttons for the
   resume DOCX, cover letter DOCX/TXT, people-intel PDF, exec summary PDF.
5. **Tracker tab** - render `application_tracker.json` as a table (reuse the HTML
   renderer / csv_sync data), with a "draft follow-up" button per row.

Run command (the only thing a friend types, once): `streamlit run web/app.py`,
or wrap it in a double-click `.command`/`.bat` launcher so there's *no* terminal.

Limitations: single-user-at-a-time, long runs block the tab (acceptable for a
personal tool; mitigate with `st.status` + threads if needed).

### Tier 2: FastAPI + minimal HTML/HTMX (if it needs to be hosted/shared)

When friends should use it without installing anything (you host it):
- `FastAPI` backend exposing `POST /apply` (upload JD) → enqueues a job.
- Background worker (start with FastAPI `BackgroundTasks`; graduate to RQ/Celery
  + Redis if concurrent users) runs `process_jd` and writes deliverables to a
  per-job folder.
- Job status endpoint + a tiny HTMX page that polls and reveals download links.
- Per-user accounts → each user's `config.yaml` + base resume stored server-side.

This is a real multi-user product; only build it if Tier 1 proves the demand.

---

## Cross-cutting concerns (apply to both tiers)

- **Secrets:** API keys move from `.env` to per-user settings (or the host's env
  in Tier 2). OSS mode means a friend with no keys still gets full output, so
  onboarding is just "upload resume, drop JD."
- **Per-user config isolation:** today `config/config.yaml` is global. Web needs
  one config per user. Refactor `utils/config.py` to accept an override path or
  in-memory dict (small change; keeps the dot-path `get()` API).
- **File outputs:** the pipeline already writes a self-contained app folder. Web
  just zips that folder or links each file, no new output logic needed.
- **GDrive/bulk:** leave Drive sync and `--bulk` as power-user/terminal features
  initially; the web MVP targets the single-JD happy path.
- **Packaging for non-terminal launch:** ship a one-click launcher (macOS
  `.command`, Windows `.bat`) that activates the venv and starts Streamlit, so
  the end user never sees a prompt.

---

## Suggested build order (after the PACE port lands)

1. Refactor `utils/config.py` to support a per-user config source (path or dict).
2. `web/app.py` Streamlit MVP: config form → JD upload → run → downloads.
3. Tracker tab (read-only table + follow-up button).
4. One-click launcher scripts + a short "for friends" setup README.
5. (Optional, later) FastAPI + worker + per-user accounts for hosting.

Dependencies to add (Tier 1): `streamlit`. Tier 2 adds `fastapi`, `uvicorn`,
optionally `redis`+`rq`. Keep them in a `[web]` extra in `pyproject.toml` so the
CLI install stays lean.
