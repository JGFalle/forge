# Your Base Resume — Setup Instructions

This folder holds your base resume: `base_resume.docx`

FORGE modifies a copy of this file for each application. The original is never touched.

---

## Step 1: Place your resume here

Name the file **exactly** `base_resume.docx` and drop it in this folder.

If you want a different filename, update `paths.base_resume` in `config/config.yaml`.

---

## Step 2: Make it FORGE-compatible

FORGE finds sections in your DOCX by searching for **anchor text** — a distinctive string
that appears in the section header of each job role.

You define these in `config/config.yaml` under `career_history[].anchor_text`.

**Rule:** whatever text you put in `anchor_text` must appear verbatim in your DOCX.

### Example

If your resume has this in a section header:

```
Director of Operations | Acme Corp
```

And your config has:

```yaml
career_history:
  - id: "acme_director"
    anchor_text: "Director of Operations"
```

FORGE will find that section and replace the lead-in text and bullets below it.

### What FORGE modifies in your DOCX

For each role where `modifiable: true`, FORGE replaces:
- The lead-in paragraph (1-2 sentences immediately below the role header)
- The bullet points (up to `max_bullets` bullets)

Everything else — dates, company names, formatting — stays exactly as-is.

For roles where `modifiable: false`, nothing is touched.

---

## Step 3: Make sure these sections exist in your DOCX

FORGE also modifies these top-level sections by searching for specific text:

| Section     | What FORGE searches for                     |
|-------------|---------------------------------------------|
| Headline    | Any text containing "STRATEGIC" or "LEADER" |
| Summary     | A paragraph starting below the headline     |
| Competencies | The competency table (4 rows)              |
| Tech Skills | The technical skills section                |

If your resume uses a different structure, you may need to adjust
`DocxModifier._apply_headline()` and `_apply_summary()` in
`pipeline/tailoring/docx_modifier.py` to match your layout.

---

## Resume formatting recommendations

FORGE uses a plain DOCX parser. For best ATS compatibility:

- Use a **single-column layout** (no text boxes, no tables in the main content area)
- Body text should be in **paragraph runs**, not tables or content controls
- Avoid headers/footers for critical content — ATS parsers often skip them
- No images or embedded objects in the resume body

The ATS checker will flag any problematic elements after each run.

---

## Google Drive alternative

If you want FORGE to pull your resume directly from Google Drive instead of this folder:

1. Find your resume's Google Doc ID from the URL
2. Set `google_docs.base_resume` in `config/config.yaml`
3. Complete the Google OAuth setup (see README.md)

The Drive client will download a fresh copy before each run.
