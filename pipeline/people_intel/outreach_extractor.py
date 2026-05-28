"""Extract LinkedIn outreach messages from people intel markdown.

The people intel prompt instructs Claude to write copy-paste LinkedIn messages
(290-300 chars, ending with the user's name) inside the Key Contacts sections.
This module pulls those messages out so they're immediately copy-paste ready.
"""

from __future__ import annotations

import re
from pathlib import Path

from utils.logging import get_logger

logger = get_logger(__name__)

_MIN_CHARS = 200
_MAX_CHARS = 340
_TARGET_MIN = 290
_TARGET_MAX = 300


def _sender_name() -> str:
    from utils.config import get
    return get("person.name", "")


def extract(intel_md: str) -> list[dict]:
    """
    Parse people intel markdown and return a list of outreach message dicts.

    Each dict:
      name         — contact name (best-effort from surrounding markdown)
      title        — contact title (best-effort)
      message      — the raw message text
      char_count   — len(message)
      valid        — True if 290-300 chars (within target window)
      warning      — non-empty string if outside target range
    """
    messages = []
    seen = set()
    sender = _sender_name()

    # Strategy 1: find paragraph blocks that end with the sender's name
    paragraphs = re.split(r"\n{2,}", intel_md)
    for para in paragraphs:
        para_clean = para.strip()
        if sender and not para_clean.endswith(sender):
            continue
        elif not sender and not re.search(r"[A-Z][a-z]+ [A-Z][a-z]+$", para_clean):
            continue
        char_count = len(para_clean)
        if char_count < _MIN_CHARS or char_count > _MAX_CHARS:
            continue
        if para_clean in seen:
            continue

        para_clean = _trim_to_limit(para_clean, sender)
        seen.add(para_clean)
        char_count = len(para_clean)

        contact_name, title = _find_contact_context(intel_md, para.strip())

        warning = ""
        if char_count < _TARGET_MIN:
            warning = f"{char_count} chars — below 290-char target (too short)"
        elif char_count > _TARGET_MAX:
            warning = f"{char_count} chars — above 300-char target (trim before sending)"

        messages.append({
            "name": contact_name,
            "title": title,
            "message": para_clean,
            "char_count": char_count,
            "valid": _TARGET_MIN <= char_count <= _TARGET_MAX,
            "warning": warning,
        })

    # Strategy 2: quoted message blocks
    if sender:
        quote_matches = re.finditer(
            r'"([^"]{' + str(_MIN_CHARS) + r',' + str(_MAX_CHARS) + r'}' + re.escape(sender) + r')"',
            intel_md,
        )
        for m in quote_matches:
            msg = m.group(1).strip()
            if msg in seen:
                continue
            seen.add(msg)
            char_count = len(msg)
            contact_name, title = _find_contact_context(intel_md, msg)
            warning = ""
            if char_count < _TARGET_MIN:
                warning = f"{char_count} chars — below 290-char target"
            elif char_count > _TARGET_MAX:
                warning = f"{char_count} chars — above 300-char target"
            messages.append({
                "name": contact_name,
                "title": title,
                "message": msg,
                "char_count": char_count,
                "valid": _TARGET_MIN <= char_count <= _TARGET_MAX,
                "warning": warning,
            })

    logger.info("Outreach extractor: found %d message(s)", len(messages))
    return messages


def _trim_to_limit(msg: str, suffix: str, limit: int = _TARGET_MAX) -> str:
    if len(msg) <= limit or not suffix or not msg.endswith(suffix):
        return msg
    body = msg[:-len(suffix)].rstrip()
    body_target = limit - len(suffix) - 1
    for i in range(min(body_target, len(body) - 1), max(body_target - 60, 0), -1):
        if body[i] in (" ", ",", ".", "!", "?"):
            trimmed = body[:i].rstrip(" ,") + " "
            return trimmed + suffix
    return body[:body_target].rstrip() + " " + suffix


def _find_contact_context(full_text: str, message: str) -> tuple[str, str]:
    pos = full_text.find(message)
    if pos == -1:
        return "", ""

    context = full_text[max(0, pos - 600):pos]
    lines = [l.strip() for l in context.split("\n") if l.strip()]

    name = ""
    title = ""

    for line in reversed(lines[-10:]):
        bold_match = re.search(r"\*\*([^*]+)\*\*", line)
        if bold_match:
            candidate = bold_match.group(1).strip()
            if len(candidate.split()) <= 5 and not any(
                kw in candidate.lower() for kw in (
                    "contact", "outreach", "priority", "intel",
                    "why", "matter", "matters", "she", "they", "her", "him",
                    "message", "note", "send", "reach",
                )
            ):
                name = candidate
                break
        header_match = re.match(r"#{1,4}\s+(.+)", line)
        if header_match:
            candidate = header_match.group(1).strip()
            if len(candidate.split()) <= 5:
                name = candidate
                break

    if name:
        name_pos = full_text.rfind(name, 0, pos)
        if name_pos != -1:
            after = full_text[name_pos + len(name):name_pos + len(name) + 200]
            title_lines = [l.strip() for l in after.split("\n") if l.strip()]
            for tl in title_lines[:3]:
                tl_clean = re.sub(r"[\*#\[\]]", "", tl).strip()
                if 3 < len(tl_clean) < 80 and not tl_clean.startswith("http"):
                    title = tl_clean
                    break

    return name, title


def save(messages: list[dict], output_dir: Path, slug: str) -> Path | None:
    if not messages:
        logger.info("No outreach messages found — skipping save")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"outreach_messages_{slug}.txt"

    lines = [
        f"OUTREACH MESSAGES — {slug}",
        f"{len(messages)} message(s) extracted from people intel",
        "=" * 60,
        "",
    ]
    for i, msg in enumerate(messages, 1):
        status = "READY" if msg["valid"] else ("SHORT" if msg["char_count"] < _TARGET_MIN else "TRIM")
        lines += [
            f"[{i}] {msg['name'] or 'Contact'}"
            + (f" — {msg['title']}" if msg["title"] else ""),
            f"    {msg['char_count']} chars  [{status}]"
            + (f"  ⚠  {msg['warning']}" if msg["warning"] else ""),
            "",
            msg["message"],
            "",
            "-" * 60,
            "",
        ]
    out.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Outreach messages saved: %s (%d messages)", out, len(messages))
    return out


def display(messages: list[dict]) -> None:
    if not messages:
        return
    print(f"\n  OUTREACH MESSAGES ({len(messages)} extracted)")
    print(f"  {'─' * 56}")
    for i, msg in enumerate(messages, 1):
        status = "READY" if msg["valid"] else "CHECK"
        name_str = msg["name"] or f"Contact {i}"
        print(f"  [{status}] {name_str[:35]}  {msg['char_count']} chars")
        if msg["warning"]:
            print(f"         {msg['warning']}")
    print()
