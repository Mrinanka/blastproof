"""Text heuristics for personal data in SQL, shared by `sql_display` and later steps (9 to 12).

These work on plain text, so they apply to SQL that would not even tokenize. Over-masking is the
safe direction: a date or an ID that looks like a phone number may be masked.
"""

import re
from typing import Final

EMAIL_PLACEHOLDER: Final = "[email]"
PHONE_PLACEHOLDER: Final = "[phone]"
MASKED_PLACEHOLDER: Final = "[masked]"

# An address is found from its "@" outward, in bounded steps. A single regex over the whole text
# backtracks cubically on a long run of letters (a 20 KB literal took minutes). The local part
# needs a letter or digit, so a LIKE pattern such as '%@example.com' stays readable.
_LOCAL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._%+-")
_MAX_LOCAL = 64
_DOMAIN_RE = re.compile(r"[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,8}")

# Column names that hold personal data or secrets, matched at the start of a word so that
# `classname` does not match `ssn`.
SENSITIVE_NAME_RE = re.compile(r"(?<![a-z])(?:email|phone|ssn|address|token|password|secret)", re.I)

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_ISO_DATETIME = (
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?)?"
)
_PHONE = r"(?<!\w)\+?\(?\d[\d\s().-]{5,40}\d(?!\w)"
# One scan, three alternatives: UUIDs and ISO dates are matched first so they are left alone.
# Only a match of the `phone` group is a phone number (and needs 7 to 15 digits).
PHONE_RE = re.compile(f"(?P<uuid>{_UUID})|(?P<iso>{_ISO_DATETIME})|(?P<phone>{_PHONE})")


def _is_phone(match: re.Match[str]) -> bool:
    return match.group("phone") is not None and 7 <= sum(c.isdigit() for c in match[0]) <= 15


def _email_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    floor = 0
    at = text.find("@")
    while at != -1:
        start = at
        while start > floor and at - start < _MAX_LOCAL and text[start - 1] in _LOCAL_CHARS:
            start -= 1
        domain = _DOMAIN_RE.match(text, at + 1)
        if domain and any(c.isalnum() and c.isascii() for c in text[start:at]):
            spans.append((start, domain.end()))
            floor = domain.end()
        at = text.find("@", at + 1)
    return spans


def has_email(text: str) -> bool:
    return bool(_email_spans(text))


def has_phone(text: str) -> bool:
    return any(_is_phone(m) for m in PHONE_RE.finditer(text))


def looks_like_pii(text: str) -> bool:
    return has_email(text) or has_phone(text)


def pii_placeholder(text: str) -> str | None:
    """`[email]` or `[phone]` when a whole literal should be replaced, else None."""
    if has_email(text):
        return EMAIL_PLACEHOLDER
    if has_phone(text):
        return PHONE_PLACEHOLDER
    return None


def mask_text(text: str) -> str:
    """Replace every email address and phone number inside `text`."""
    pieces: list[str] = []
    position = 0
    for start, end in _email_spans(text):
        pieces += [text[position:start], EMAIL_PLACEHOLDER]
        position = end
    text = "".join(pieces) + text[position:]
    return PHONE_RE.sub(lambda m: PHONE_PLACEHOLDER if _is_phone(m) else m[0], text)
