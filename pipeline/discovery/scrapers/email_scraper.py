"""Gmail IMAP scraper for job alert emails.

Reads from a dedicated Gmail label (default: "FORGE/Alerts") and extracts
job listings from LinkedIn, Indeed, and Google for Jobs alert emails.
Marks messages as read after extraction so they aren't re-processed.

Required env vars:
  GMAIL_APP_PASSWORD — same app password used by digest.py

Gmail setup (one-time):
  1. Create label "FORGE/Alerts" in Gmail (or match whatever alert_email_label is set to in config)
  2. Set up LinkedIn/Indeed/Google alert emails to arrive in that label
  3. Either filter by sender or set up auto-label via Gmail filters

Returns normalized job dicts compatible with normalizer.normalize().
"""

from __future__ import annotations

import email
import imaplib
import os
import re
from email.header import decode_header
from html.parser import HTMLParser

from utils.config import get
from utils.logging import get_logger

logger = get_logger(__name__)

_GMAIL_IMAP = "imap.gmail.com"

# Job alert sender patterns for routing to the right parser
_LINKEDIN_SENDERS = ("jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com")
_INDEED_SENDERS   = ("alert@indeed.com", "jobalerts@indeed.com")
_GOOGLE_SENDERS   = ("jobs-noreply@google.com",)


# ── Public API ─────────────────────────────────────────────────────────────────

def scrape() -> list[dict]:
    """Connect to Gmail, read unread alert emails, return extracted jobs."""
    app_password = os.getenv("GMAIL_APP_PASSWORD", "")
    if not app_password:
        logger.warning("GMAIL_APP_PASSWORD not set — email scraper skipped")
        return []

    label = get("discovery.alert_email_label", "FORGE/Alerts")
    gmail_user = get("discovery.digest_from", get("person.email", ""))
    if not gmail_user:
        logger.warning("No Gmail address configured — set person.email or discovery.digest_from")
        return []

    try:
        mail = imaplib.IMAP4_SSL(_GMAIL_IMAP)
        mail.login(gmail_user, app_password)
    except Exception as exc:
        logger.error("Gmail IMAP login failed: %s", exc)
        return []

    jobs: list[dict] = []
    try:
        # IMAP folders use "/" as separator; Gmail labels map to IMAP folders
        # Quoted to handle labels with spaces/slashes
        status, _ = mail.select(f'"{label}"')
        if status != "OK":
            logger.warning("Gmail label '%s' not found — create it and point alerts there", label)
            return []

        # Only process unread messages so we don't re-scrape already-seen alerts
        status, message_ids = mail.search(None, "UNSEEN")
        if status != "OK" or not message_ids[0]:
            logger.info("No unread messages in %s", label)
            return []

        ids = message_ids[0].split()
        logger.info("Found %d unread alert emails in %s", len(ids), label)

        for msg_id in ids:
            try:
                jobs.extend(_process_message(mail, msg_id))
            except Exception as exc:
                logger.warning("Failed to process message %s: %s", msg_id, exc)

    finally:
        try:
            mail.close()
            mail.logout()
        except Exception:
            pass

    logger.info("Email scraper: %d jobs extracted from %d messages", len(jobs), len(ids) if message_ids[0] else 0)
    return jobs


# ── Message processing ─────────────────────────────────────────────────────────

def _process_message(mail: imaplib.IMAP4_SSL, msg_id: bytes) -> list[dict]:
    """Fetch, parse, and mark as read a single alert email."""
    status, data = mail.fetch(msg_id, "(RFC822)")
    if status != "OK":
        return []

    raw_email = data[0][1]
    msg = email.message_from_bytes(raw_email)

    sender = _decode_header_str(msg.get("From", "")).lower()
    html_body = _extract_html(msg)

    if not html_body:
        return []

    jobs: list[dict] = []

    if any(s in sender for s in _LINKEDIN_SENDERS):
        jobs = _parse_linkedin_alert(html_body)
    elif any(s in sender for s in _INDEED_SENDERS):
        jobs = _parse_indeed_alert(html_body)
    elif any(s in sender for s in _GOOGLE_SENDERS):
        jobs = _parse_google_alert(html_body)
    else:
        # Unknown sender — attempt generic extraction
        logger.debug("Unknown alert sender: %s — attempting generic parse", sender)
        jobs = _parse_generic_alert(html_body, sender)

    # Mark as read (removes \Seen flag absence — standard IMAP flag)
    mail.store(msg_id, "+FLAGS", "\\Seen")

    return jobs


def _decode_header_str(header: str) -> str:
    parts = decode_header(header)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _extract_html(msg: email.message.Message) -> str:
    """Extract HTML body from a (possibly multipart) email message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    else:
        if msg.get_content_type() == "text/html":
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
    return ""


# ── LinkedIn alert parser ──────────────────────────────────────────────────────

def _parse_linkedin_alert(html: str) -> list[dict]:
    """
    LinkedIn job alert emails contain structured job cards. Each card includes:
      - Job title (as link text)
      - Company name
      - Location
      - A jobs.linkedin.com URL

    Example anchor pattern:
      <a href="https://www.linkedin.com/comm/jobs/view/12345...">Director of Operations</a>
    """
    jobs: list[dict] = []

    # Extract job view links — these carry the job ID
    url_pattern = re.compile(
        r'href="(https://www\.linkedin\.com/(?:comm/)?jobs/view/(\d+)[^"]*)"',
        re.IGNORECASE,
    )
    # After each URL, LinkedIn typically puts title, company, location in sibling elements
    # Parse the full HTML into text segments around each job URL

    # Strategy: extract all text nodes and URLs in document order, then group them
    parser = _LinkedInAlertParser()
    parser.feed(html)

    for card in parser.cards:
        if card.get("url") and card.get("title"):
            jobs.append({
                "title":    card.get("title", ""),
                "company":  card.get("company", ""),
                "location": card.get("location", ""),
                "url":      _clean_tracking_url(card["url"]),
                "source":   "linkedin",
            })

    if not jobs:
        # Fallback: regex-only extraction of URLs and adjacent text
        for m in url_pattern.finditer(html):
            url   = m.group(1)
            title = _nearest_text_before(html, m.start(), 200)
            if title:
                jobs.append({
                    "title":    title,
                    "company":  "",
                    "location": "",
                    "url":      _clean_tracking_url(url),
                    "source":   "linkedin",
                })

    logger.debug("LinkedIn alert: %d jobs parsed", len(jobs))
    return jobs


class _LinkedInAlertParser(HTMLParser):
    """State-machine HTML parser for LinkedIn job alert emails."""

    def __init__(self):
        super().__init__()
        self.cards: list[dict] = []
        self._current: dict | None = None
        self._capture: str | None = None  # "title" | "company" | "location"
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href", "")

        # LinkedIn job view links signal the start of a new card
        if tag == "a" and re.search(r"linkedin\.com/(?:comm/)?jobs/view/\d+", href or ""):
            if self._current:
                self._flush()
            self._current = {"url": href, "title": "", "company": "", "location": ""}
            self._capture = "title"
            self._depth = 0
            return

        # LinkedIn wraps company and location in specific element types after the title anchor
        if self._current:
            if tag in ("span", "p", "div") and not attrs_dict.get("href"):
                if self._current["title"] and not self._current["company"]:
                    self._capture = "company"
                elif self._current["company"] and not self._current["location"]:
                    self._capture = "location"

    def handle_endtag(self, tag):
        if self._capture and tag == "a":
            self._capture = None

    def handle_data(self, data):
        text = data.strip()
        if not text or not self._current or not self._capture:
            return
        field = self._capture
        if not self._current.get(field):
            self._current[field] = text
            if field == "location":
                self._flush()

    def _flush(self):
        if self._current and self._current.get("title"):
            self.cards.append(self._current)
        self._current = None
        self._capture = None


# ── Indeed alert parser ────────────────────────────────────────────────────────

def _parse_indeed_alert(html: str) -> list[dict]:
    """
    Indeed alert emails contain job cards with:
      - Title in an <a> with href containing indeed.com/viewjob or /rc/clk
      - Company and location in adjacent text
    """
    jobs: list[dict] = []

    parser = _IndeedAlertParser()
    parser.feed(html)

    for card in parser.cards:
        if card.get("url") and card.get("title"):
            jobs.append({
                "title":    card.get("title", ""),
                "company":  card.get("company", ""),
                "location": card.get("location", ""),
                "url":      _clean_tracking_url(card["url"]),
                "source":   "indeed",
            })

    logger.debug("Indeed alert: %d jobs parsed", len(jobs))
    return jobs


class _IndeedAlertParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards: list[dict] = []
        self._current: dict | None = None
        self._capture: str | None = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href", "")

        if tag == "a" and re.search(r"indeed\.com/.*(viewjob|rc/clk|clk)", href or ""):
            if self._current:
                self._flush()
            self._current = {"url": href, "title": "", "company": "", "location": ""}
            self._capture = "title"

    def handle_endtag(self, tag):
        if tag == "a" and self._capture == "title":
            self._capture = None

    def handle_data(self, data):
        text = data.strip()
        if not text or not self._current:
            return
        if self._capture == "title" and not self._current["title"]:
            self._current["title"] = text
        elif self._current["title"] and not self._current["company"] and len(text) > 1:
            self._current["company"] = text
            self._capture = "location"
        elif self._capture == "location" and not self._current["location"] and len(text) > 1:
            self._current["location"] = text
            self._flush()

    def _flush(self):
        if self._current and self._current.get("title"):
            self.cards.append(self._current)
        self._current = None
        self._capture = None


# ── Google for Jobs alert parser ───────────────────────────────────────────────

def _parse_google_alert(html: str) -> list[dict]:
    """
    Google job alert emails include job cards with title, company, location,
    and a Google Jobs redirect URL that eventually resolves to the source posting.
    """
    jobs: list[dict] = []

    # Google alerts use a table structure; job URLs contain '/jobs/view/'
    url_pattern = re.compile(
        r'href="(https://www\.google\.com/search\?[^"]*jobs[^"]*)"',
        re.IGNORECASE,
    )

    parser = _GoogleAlertParser()
    parser.feed(html)

    for card in parser.cards:
        if card.get("title"):
            jobs.append({
                "title":    card.get("title", ""),
                "company":  card.get("company", ""),
                "location": card.get("location", ""),
                "url":      card.get("url", ""),
                "source":   "serpapi",  # treat as same source bucket
            })

    logger.debug("Google alert: %d jobs parsed", len(jobs))
    return jobs


class _GoogleAlertParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards: list[dict] = []
        self._current: dict | None = None
        self._capture: str | None = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href", "")

        if tag == "a" and "google.com" in (href or "") and "jobs" in (href or "").lower():
            if self._current:
                self._flush()
            self._current = {"url": href, "title": "", "company": "", "location": ""}
            self._capture = "title"

    def handle_endtag(self, tag):
        if tag == "a" and self._capture == "title":
            self._capture = None

    def handle_data(self, data):
        text = data.strip()
        if not text or not self._current:
            return
        if self._capture == "title" and not self._current["title"]:
            self._current["title"] = text
        elif self._current["title"] and not self._current["company"] and len(text) > 2:
            self._current["company"] = text
        elif self._current["company"] and not self._current["location"] and len(text) > 2:
            self._current["location"] = text
            self._flush()

    def _flush(self):
        if self._current and self._current.get("title"):
            self.cards.append(self._current)
        self._current = None
        self._capture = None


# ── Generic fallback parser ────────────────────────────────────────────────────

def _parse_generic_alert(html: str, sender: str) -> list[dict]:
    """Last-resort parser for unrecognized alert senders — extracts any job-URL anchors."""
    jobs: list[dict] = []
    job_url_pattern = re.compile(
        r'href="(https?://[^"]*(?:jobs|careers|viewjob|job-view)[^"]*)"[^>]*>([^<]{5,120})<',
        re.IGNORECASE,
    )
    for m in job_url_pattern.finditer(html):
        url   = m.group(1)
        title = m.group(2).strip()
        if title and not re.search(r"(unsubscribe|privacy|help|feedback)", title, re.IGNORECASE):
            jobs.append({
                "title":    title,
                "company":  "",
                "location": "",
                "url":      url,
                "source":   "email",
            })
    return jobs


# ── Helpers ────────────────────────────────────────────────────────────────────

def _clean_tracking_url(url: str) -> str:
    """Strip common email tracking redirects to get the canonical job URL."""
    # LinkedIn tracking redirect: /comm/jobs/view/JOBID?...
    m = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", url)
    if m:
        return f"https://www.linkedin.com/jobs/view/{m.group(1)}"

    # Indeed redirect: often /rc/clk?jk=JOBID
    m = re.search(r"jk=([a-f0-9]{16})", url)
    if m:
        return f"https://www.indeed.com/viewjob?jk={m.group(1)}"

    return url


def _nearest_text_before(html: str, pos: int, window: int) -> str:
    """Fallback: extract the last meaningful text content before position `pos`."""
    chunk = html[max(0, pos - window):pos]
    # Strip tags
    text = re.sub(r"<[^>]+>", " ", chunk)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Take the last reasonable segment
    parts = [p.strip() for p in text.split("  ") if p.strip() and len(p.strip()) > 3]
    return parts[-1] if parts else ""
