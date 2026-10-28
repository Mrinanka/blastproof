"""One redactor for every sink that is not a call-site allowlist: logs and LangSmith traces.

The allowlist stays the primary rule (D-110): call sites log and send only fields chosen for the
purpose, and mask what they carry. This is the last layer behind them, for what a slip or a third-
party library (uvicorn, httpx, langsmith) writes. It replaces:

- secrets by shape: URL credentials, agent keys, JWTs, `Bearer` tokens and `password=`-style pairs;
- secrets by value: every secret the process holds (`known_secrets_from`), wherever it appears;
- personal data: emails and phone numbers, with the same heuristics as `mask_text`.

Every pattern is bounded or anchored so a long input cannot make it backtrack (D-123).
"""

import os
import re
from collections.abc import Iterable, Mapping
from typing import Any, Final
from urllib.parse import unquote, urlsplit

from pydantic import SecretStr

from app.services.masking import mask_text

REDACTED: Final = "[redacted]"
# A short value (a local password such as `company`) would blank ordinary words (D-080).
MIN_SECRET_LENGTH: Final = 12
# Nested values past this depth are not walked; they are stringified and redacted whole.
MAX_DEPTH: Final = 8

_URL_CREDENTIALS = re.compile(r"(?<=://)[^\s/@:]{1,128}:[^\s/@]{1,256}@")
_AGENT_KEY = re.compile(r"bp_[A-Za-z0-9_-]{43}")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{6,}")
# `name=value`, `name: value` and `"name": "value"`, where the name ends in a secret word. The
# lookbehind keeps the start to the beginning of a word, so a long run of word characters is linear.
_NAMED_VALUE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])([A-Za-z0-9_]*(?:password|passwd|token|secret|api[_-]?key))"
    r"""(["']?\s*[:=]\s*["']?)([^\s"',;&]{4,})"""
)
# A whole value that is one decimal number (a logged `cost_usd`) is not a phone number, although it
# can have seven digits. Dotted phone numbers (`555.123.4567`) have more than one dot.
_PLAIN_DECIMAL = re.compile(r"-?\d{1,20}\.\d{1,20}")
_SECRET_ENV_NAME = re.compile(r"(?i)(?:_API_KEY|_TOKEN|_SECRET)$|PASSWORD")
_SECRET_OPTION_KEY = re.compile(r"(?i)api[_-]?key|token|secret|password")

# Longest first, so a value that contains another is replaced whole.
_known: tuple[str, ...] = ()


def set_known_secrets(values: Iterable[str]) -> None:
    """Replace the process-wide registry. Values under `MIN_SECRET_LENGTH` are not registered."""
    global _known
    kept = {v for v in values if len(v) >= MIN_SECRET_LENGTH}
    _known = tuple(sorted(kept, key=len, reverse=True))


def scrub_secrets(text: str) -> str:
    for value in _known:
        text = text.replace(value, REDACTED)
    text = _URL_CREDENTIALS.sub(f"{REDACTED}@", text)
    text = _AGENT_KEY.sub(REDACTED, text)
    text = _JWT.sub(REDACTED, text)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    return _NAMED_VALUE.sub(lambda m: f"{m[1]}{m[2]}{REDACTED}", text)


def redact(text: str) -> str:
    """Secrets first (a key could look like a phone number), then emails and phone numbers."""
    if _PLAIN_DECIMAL.fullmatch(text):
        return text
    return mask_text(scrub_secrets(text))


def _url_passwords(url: str) -> list[str]:
    try:
        password = urlsplit(url).password
    except ValueError:
        return []
    return [password, unquote(password)] if password else []


def known_secrets_from(settings: Any) -> list[str]:
    """Every secret value the process holds: the `SecretStr` settings (and the password inside
    each URL), key-like entries of the model options, and secret-named environment variables."""
    found: list[str] = []
    for name in type(settings).model_fields:
        value = getattr(settings, name, None)
        if isinstance(value, SecretStr):
            secret = value.get_secret_value()
            found += [secret, *_url_passwords(secret)]
        elif isinstance(value, Mapping) and name.endswith("_model_options"):
            found += [
                v for k, v in value.items() if isinstance(v, str) and _SECRET_OPTION_KEY.search(k)
            ]
    found += [v for k, v in os.environ.items() if _SECRET_ENV_NAME.search(k)]
    return [v for v in found if len(v) >= MIN_SECRET_LENGTH]


def _walk(value: Any, depth: int) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    if depth >= MAX_DEPTH:
        return redact(str(value))
    if isinstance(value, Mapping):
        return {k: _walk(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_walk(v, depth + 1) for v in value]
    return value


def redact_event_dict(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """The structlog processor: keys are kept, string values redacted, numbers left alone."""
    return {key: _walk(value, 0) for key, value in event_dict.items()}
