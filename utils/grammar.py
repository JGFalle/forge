"""LanguageTool grammar and punctuation check.

Free public API at https://api.languagetool.org: no key required for basic use.
Set LANGUAGETOOL_API_KEY in .env for premium access and higher rate limits.

Design:
- Auto-fix: mechanical punctuation/spacing errors only (high confidence, no meaning change)
- Flag: everything else - grammar, style, word choice - shown in reports, not silently applied
- Skip: rules that conflict with resume conventions (ALL-CAPS acronyms, bullet verb starts)
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.parse
import urllib.request
from typing import NamedTuple

from utils.logging import get_logger

logger = get_logger(__name__)

LT_ENDPOINT = "https://api.languagetool.org/v2/check"
LT_TIMEOUT = 12

# Only these categories get auto-applied, mechanical errors with no meaning risk
_AUTO_FIX_CATEGORIES = {"PUNCTUATION", "TYPOGRAPHY", "TYPOS"}

# Rule IDs to never auto-apply even in allowed categories
_SKIP_RULES = {
    "UPPERCASE_SENTENCE_START",   # resume bullets intentionally start with verbs
    "COMMA_COMPOUND_SENTENCE",    # style choice
    "EN_QUOTES",                  # formatting choice
    "EN_UNPAIRED_BRACKETS",       # parenthetical style varies
    "WORD_CONTAINS_UNDERSCORE",   # technical names like RIOS, AGV
}


class GrammarIssue(NamedTuple):
    offset: int
    length: int
    original: str
    message: str
    replacements: list[str]
    rule_id: str
    category: str
    auto_fixable: bool


def check(text: str, language: str = "en-US") -> list[GrammarIssue]:
    """Call LanguageTool and return structured issues. Empty list on failure."""
    if not text.strip():
        return []

    params: dict = {"text": text, "language": language}
    api_key = os.getenv("LANGUAGETOOL_API_KEY", "")
    if api_key:
        params["apiKey"] = api_key

    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ssl_ctx = ssl.create_default_context()

    try:
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(
            LT_ENDPOINT, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=LT_TIMEOUT, context=ssl_ctx) as resp:
            raw = json.loads(resp.read())
    except Exception as exc:
        logger.warning("LanguageTool API unavailable: %s", exc)
        return []

    issues: list[GrammarIssue] = []
    for m in raw.get("matches", []):
        rule_id = m.get("rule", {}).get("id", "")
        category = m.get("rule", {}).get("category", {}).get("id", "")
        replacements = [r["value"] for r in m.get("replacements", [])]
        offset = m["offset"]
        length = m.get("length", m.get("errorLength", 0))
        original = text[offset: offset + length]

        auto_fixable = (
            category in _AUTO_FIX_CATEGORIES
            and rule_id not in _SKIP_RULES
            and len(replacements) == 1  # only fix when there's exactly one clear answer
        )

        issues.append(GrammarIssue(
            offset=offset,
            length=length,
            original=original,
            message=m.get("message", ""),
            replacements=replacements,
            rule_id=rule_id,
            category=category,
            auto_fixable=auto_fixable,
        ))

    logger.debug("LanguageTool: %d issue(s) found in %d chars", len(issues), len(text))
    return issues


def apply_fixes(text: str, issues: list[GrammarIssue]) -> tuple[str, list[str]]:
    """Apply auto-fixable corrections in reverse order. Returns (fixed_text, change_log)."""
    fixable = [i for i in issues if i.auto_fixable]
    if not fixable:
        return text, []

    changes: list[str] = []
    for issue in sorted(fixable, key=lambda i: i.offset, reverse=True):
        replacement = issue.replacements[0]
        text = text[: issue.offset] + replacement + text[issue.offset + issue.length :]
        changes.append(f"'{issue.original}' → '{replacement}'  ({issue.message})")

    return text, changes


def check_and_fix(text: str) -> tuple[str, list[GrammarIssue], list[str]]:
    """Full pass: check, auto-fix mechanical errors, return (fixed_text, all_issues, change_log)."""
    issues = check(text)
    fixed, changes = apply_fixes(text, issues)
    return fixed, issues, changes


def check_json_fields(data: dict) -> dict[str, list[GrammarIssue]]:
    """Run grammar check on the key text fields in a tailoring JSON.

    Returns a dict of field_label -> issues. Fields with no issues are omitted.
    """
    results: dict[str, list[GrammarIssue]] = {}

    def _check_field(label: str, text: str) -> None:
        if not text:
            return
        issues = check(text)
        flagged = [i for i in issues if not i.auto_fixable]
        if flagged:
            results[label] = flagged

    _check_field("summary", data.get("summary", ""))
    _check_field("cover_letter", data.get("cover_letter", ""))

    for mod in data.get("experience_modifications", []):
        role_id = mod.get("role_identifier", "?")
        _check_field(f"{role_id} lead_in", mod.get("replacement_lead_in", ""))
        for i, bullet in enumerate(mod.get("replacement_bullets", []), 1):
            _check_field(f"{role_id} bullet {i}", bullet)

    return results


def format_issues(field_issues: dict[str, list[GrammarIssue]]) -> str:
    """Format grammar issues for terminal or report display."""
    if not field_issues:
        return "No grammar issues found."
    lines: list[str] = []
    for field, issues in field_issues.items():
        lines.append(f"\n  [{field}]")
        for issue in issues:
            hint = f" → {issue.replacements[0]!r}" if issue.replacements else ""
            lines.append(f"    ! {issue.message}{hint}")
            lines.append(f"      \"{issue.original}\"")
    return "\n".join(lines)
