"""Build and send the daily discovery email digest via Gmail SMTP."""

from __future__ import annotations

import os
import smtplib
import ssl
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from utils.config import get
from utils.logging import get_logger

logger = get_logger(__name__)


def send(new_jobs: list[dict], tracker_summary: dict) -> bool:
    """Build and send the digest email with CSV attachment. Returns True on success."""
    to_addr   = get("discovery.digest_to",   get("person.email", ""))
    from_addr = get("discovery.digest_from", get("person.email", ""))
    app_password = os.getenv("GMAIL_APP_PASSWORD", "")

    if not app_password:
        logger.warning("GMAIL_APP_PASSWORD not set — digest email not sent")
        return False

    subject = _subject(new_jobs)
    html    = _build_html(new_jobs, tracker_summary)

    # Outer mixed container so we can attach a file
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = f"PACE Discovery <{from_addr}>"
    msg["To"]      = to_addr

    # HTML body wrapped in an alternative part
    body = MIMEMultipart("alternative")
    body.attach(MIMEText(html, "html"))
    msg.attach(body)

    # CSV attachment — full tracker file
    csv_path = Path(get("discovery.tracker_csv", "")).expanduser()
    if csv_path.exists():
        with open(csv_path, "rb") as f:
            csv_bytes = f.read()
        attachment = MIMEBase("text", "csv")
        attachment.set_payload(csv_bytes)
        encoders.encode_base64(attachment)
        attachment.add_header(
            "Content-Disposition", "attachment",
            filename="opportunity_tracker.csv",
        )
        msg.attach(attachment)

    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.login(from_addr, app_password)
            server.sendmail(from_addr, to_addr, msg.as_string())
        logger.info("Digest email sent to %s (%d new jobs)", to_addr, len(new_jobs))
        return True
    except Exception as exc:
        logger.error("Failed to send digest email: %s", exc)
        return False


def _subject(new_jobs: list[dict]) -> str:
    today = datetime.now().strftime("%b %d")
    if not new_jobs:
        return f"PACE Discovery — No new opportunities | {today}"
    return f"PACE Discovery — {len(new_jobs)} new opportunit{'y' if len(new_jobs) == 1 else 'ies'} | {today}"


def _build_html(new_jobs: list[dict], summary: dict) -> str:
    today  = datetime.now().strftime("%A, %B %d, %Y")
    tbd    = summary.get("TBD",    0)
    queued = summary.get("Queued", 0)
    applied = summary.get("Applied", 0)

    if new_jobs:
        rows_html = ""
        for job in new_jobs:
            url    = job.get("url", "")
            link   = f'<a href="{url}" style="color:#3b82f6">View JD</a>' if url else "—"
            salary = job.get("salary_range", "") or "—"
            score  = job.get("match_score", "")
            dscore = job.get("deep_score", "")
            source_badge = _source_badge(job.get("source", ""))
            score_badge  = _score_badge(score) if score != "" else "—"
            deep_badge   = _score_badge(dscore) if dscore != "" else '<span style="color:#334155;font-size:11px">—</span>'
            rows_html += f"""
            <tr>
              <td style="padding:10px 12px;border-bottom:1px solid #1e293b;font-weight:600;color:#e2e8f0">{job.get('company','')}</td>
              <td style="padding:10px 12px;border-bottom:1px solid #1e293b;color:#e2e8f0">{job.get('title','')}</td>
              <td style="padding:10px 12px;border-bottom:1px solid #1e293b;color:#94a3b8">{job.get('location','')}</td>
              <td style="padding:10px 12px;border-bottom:1px solid #1e293b;color:#94a3b8">{salary}</td>
              <td style="padding:10px 12px;border-bottom:1px solid #1e293b;text-align:center">{score_badge}</td>
              <td style="padding:10px 12px;border-bottom:1px solid #1e293b;text-align:center">{deep_badge}</td>
              <td style="padding:10px 12px;border-bottom:1px solid #1e293b">{source_badge}</td>
              <td style="padding:10px 12px;border-bottom:1px solid #1e293b">{link}</td>
            </tr>"""

        jobs_section = f"""
        <h2 style="color:#3b82f6;font-size:15px;margin:24px 0 12px">New Opportunities ({len(new_jobs)})</h2>
        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:#1e293b;border-radius:8px;overflow:hidden">
          <thead>
            <tr style="background:#0f172a">
              <th style="padding:10px 12px;text-align:left;color:#64748b;font-size:11px;text-transform:uppercase">Company</th>
              <th style="padding:10px 12px;text-align:left;color:#64748b;font-size:11px;text-transform:uppercase">Title</th>
              <th style="padding:10px 12px;text-align:left;color:#64748b;font-size:11px;text-transform:uppercase">Location</th>
              <th style="padding:10px 12px;text-align:left;color:#64748b;font-size:11px;text-transform:uppercase">Salary</th>
              <th style="padding:10px 12px;text-align:center;color:#64748b;font-size:11px;text-transform:uppercase">Score</th>
              <th style="padding:10px 12px;text-align:center;color:#64748b;font-size:11px;text-transform:uppercase">Deep</th>
              <th style="padding:10px 12px;text-align:left;color:#64748b;font-size:11px;text-transform:uppercase">Source</th>
              <th style="padding:10px 12px;text-align:left;color:#64748b;font-size:11px;text-transform:uppercase">Link</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>"""
    else:
        jobs_section = '<p style="color:#64748b;font-style:italic">No new opportunities found today. Check back tomorrow.</p>'

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <div style="max-width:900px;margin:0 auto;padding:32px 24px">

    <div style="border-bottom:1px solid #1e293b;padding-bottom:20px;margin-bottom:24px">
      <div style="font-size:20px;font-weight:700;color:#fff">PACE Discovery Digest</div>
      <div style="color:#64748b;font-size:13px;margin-top:4px">{today}</div>
    </div>

    <div style="display:flex;gap:16px;margin-bottom:24px">
      {_stat_box(len(new_jobs), "New Today",           "#3b82f6")}
      {_stat_box(tbd,           "Awaiting Review",     "#f59e0b")}
      {_stat_box(queued,        "Queued for Pipeline", "#8b5cf6")}
      {_stat_box(applied,       "Applied",             "#22c55e")}
    </div>

    {jobs_section}

    <div style="margin-top:32px;padding:16px;background:#1e293b;border-radius:8px;border-left:3px solid #3b82f6">
      <div style="color:#94a3b8;font-size:12px">
        Full tracker attached as CSV — sort by <strong style="color:#e2e8f0">deep_score</strong> when available, otherwise <strong style="color:#e2e8f0">match_score</strong>.
        <strong style="color:#e2e8f0">Score</strong> = metadata signal (0-100, instant).
        <strong style="color:#e2e8f0">Deep</strong> = Claude Haiku resume-vs-JD fit (0-100, runs for Score ≥ 40 with full description).
        To queue a role: open tracker in Google Sheets, change status to <strong style="color:#8b5cf6">Queued</strong>,
        then run <code style="background:#0f172a;padding:2px 6px;border-radius:4px;color:#e2e8f0">pace</code> with the JD PDF.
      </div>
    </div>

  </div>
</body>
</html>"""


def _stat_box(count: int, label: str, color: str) -> str:
    return f"""
    <div style="flex:1;background:#1e293b;border-radius:8px;padding:16px;text-align:center;border-top:3px solid {color}">
      <div style="font-size:28px;font-weight:700;color:{color}">{count}</div>
      <div style="font-size:11px;color:#64748b;margin-top:4px;text-transform:uppercase">{label}</div>
    </div>"""


def _score_badge(score) -> str:
    try:
        n = int(score)
    except (ValueError, TypeError):
        return "—"
    if n >= 70:
        bg, fg = "#14532d", "#86efac"
    elif n >= 50:
        bg, fg = "#422006", "#fbbf24"
    elif n >= 35:
        bg, fg = "#431407", "#fb923c"
    else:
        bg, fg = "#1e293b", "#64748b"
    return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:10px;font-size:12px;font-weight:700">{n}</span>'


def _source_badge(source: str) -> str:
    colors = {
        "indeed":   ("#1e3a5f", "#93c5fd"),
        "linkedin": ("#1e3a5f", "#60a5fa"),
        "workday":  ("#14532d", "#86efac"),
        "serpapi":  ("#3b1f00", "#fbbf24"),
    }
    bg, fg = colors.get(source.lower(), ("#1e293b", "#94a3b8"))
    return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600">{source}</span>'
