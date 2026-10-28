"""SEC-14, SEC-15, ARCH-17: the shared redactor behind the logs and the traces."""

import os
import time
from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.services import redaction
from app.services.redaction import (
    known_secrets_from,
    redact,
    redact_event_dict,
    scrub_secrets,
    set_known_secrets,
)

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
AGENT_KEY = "bp_" + "A1b2C3d4E5" * 4 + "abc"  # 43 characters after the prefix


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    set_known_secrets([])
    yield
    set_known_secrets([])


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("connect postgresql://app:hunter2hunter2@ep-x.neon.tech/db", "hunter2hunter2"),
        ("connect postgresql+asyncpg://app:p%40ss-word@localhost:5432/x", "p%40ss-word"),
        ("see https://user:tokenvalue@example.com/path", "tokenvalue"),
        (f"key {AGENT_KEY} used", AGENT_KEY),
        (f"token {JWT}", JWT),
        ("Authorization: Bearer abcDEF123456", "abcDEF123456"),
        ("GOOGLE_API_KEY=AIzaSyDummyValue123", "AIzaSyDummyValue123"),
        ('{"api_key": "sk-live-1234567890"}', "sk-live-1234567890"),
        ("password=swordfish99", "swordfish99"),
        ("secret: topsecretvalue", "topsecretvalue"),
    ],
)
def test_secret_patterns_are_scrubbed(text: str, gone: str) -> None:
    out = scrub_secrets(text)
    assert gone not in out
    assert "[redacted]" in out
    assert gone not in redact(text)


def test_url_credentials_keep_the_scheme_and_host() -> None:
    assert (
        scrub_secrets("postgresql://u:pw123456@host.example/db")
        == "postgresql://[redacted]@host.example/db"
    )


def test_redact_also_masks_email_and_phone() -> None:
    out = redact("mail ana@example.com or call +1 (415) 555-0132")
    assert "ana@example.com" not in out
    assert "555-0132" not in out
    assert "[email]" in out
    assert "[phone]" in out


@pytest.mark.parametrize(
    "text",
    [
        "action 3f2b8c1e-5a4d-4e7f-9b6a-1c2d3e4f5a6b done",
        "at 2026-10-28T12:30:45.123456Z",
        "app/workflow/graph.py:610 in run",
        "POST /api/actions/{action_id}/approve",
        "affected 48,213 rows in orders",
        "stage_done shadow",
        "the token budget was reached",
    ],
)
def test_ordinary_text_is_unchanged(text: str) -> None:
    assert redact(text) == text


def test_known_value_is_replaced_wherever_it_appears() -> None:
    set_known_secrets(["a-long-random-secret-value"])
    out = redact("x=a-long-random-secret-value; again a-long-random-secret-value.")
    assert "a-long-random-secret-value" not in out
    assert out.count("[redacted]") == 2


def test_short_values_are_never_registered() -> None:
    set_known_secrets(["company", "short", "elevenchars"])
    assert redact("the company is short of elevenchars") == "the company is short of elevenchars"


def test_set_known_secrets_replaces_the_registry() -> None:
    set_known_secrets(["first-secret-value-1"])
    set_known_secrets(["second-secret-value2"])
    assert redact("first-secret-value-1") == "first-secret-value-1"
    assert redact("second-secret-value2") == "[redacted]"


def test_event_dict_walks_nested_values_and_keeps_numbers() -> None:
    event = {
        "event": "hello",
        "duration_ms": 12,
        "ratio": 0.5,
        "ok": True,
        "none": None,
        "detail": "mail ana@example.com",
        "nested": {"dsn": "postgresql://u:pw123456@h/db", "items": ["bob@example.com", 7]},
    }
    out = redact_event_dict(None, "info", event)
    assert out["duration_ms"] == 12
    assert out["ratio"] == 0.5
    assert out["ok"] is True
    assert out["none"] is None
    assert out["detail"] == "mail [email]"
    assert out["nested"]["dsn"] == "postgresql://[redacted]@h/db"
    assert out["nested"]["items"] == ["[email]", 7]


def test_event_dict_depth_is_capped() -> None:
    deep: dict = {"v": "ana@example.com"}
    for _ in range(40):
        deep = {"n": deep}
    out = redact_event_dict(None, "info", {"event": "x", "deep": deep})
    # Past the cap the subtree is not walked, but it is not left in clear either.
    assert "ana@example.com" not in str(out)


def test_long_input_does_not_backtrack() -> None:
    text = "a" * 20_000 + "://" + "b" * 20_000 + ":" * 5_000 + "@" * 50
    started = time.perf_counter()
    redact(text)
    redact("Bearer " + "x" * 20_000)
    redact("password=" + "=" * 20_000)
    assert time.perf_counter() - started < 2


def test_known_secrets_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.endswith(("_API_KEY", "_TOKEN", "_SECRET")) or "PASSWORD" in name:
            monkeypatch.delenv(name)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-env-key-value-123")
    monkeypatch.setenv("PATH_LIKE_NAME", "not-a-secret-because-of-its-name")
    settings = Settings(
        _env_file=None,
        jwt_secret=SecretStr("j" * 40),
        neon_api_key=SecretStr("neon-api-key-value-1"),
        blastproof_database_url=SecretStr("postgresql+asyncpg://app:db-password-value@h/db"),
        company_db_reader_url=SecretStr("postgresql://reader:reader-pw-value-1@h/db"),
        summary_model_options={"api_key": "summary-key-value-1", "thinking_level": "low"},
        judge_model_options={"token": "judge-token-value-1"},
        neon_project_id="project-id-not-secret",
    )
    found = set(known_secrets_from(settings))
    assert {
        "j" * 40,
        "neon-api-key-value-1",
        "db-password-value",
        "reader-pw-value-1",
        "summary-key-value-1",
        "judge-token-value-1",
        "gsk-env-key-value-123",
    } <= found
    assert "project-id-not-secret" not in found
    assert "low" not in found
    assert "not-a-secret-because-of-its-name" not in found


def test_registry_is_what_both_sinks_read() -> None:
    set_known_secrets(["registry-secret-value"])
    out = redact_event_dict(None, "info", {"event": "registry-secret-value"})
    assert out["event"] == "[redacted]"
    assert redaction.redact("registry-secret-value") == "[redacted]"


@pytest.mark.parametrize("number", ["0.002025", "0.0000123456", "12345678.25", "-0.00312345"])
def test_a_plain_decimal_is_not_a_phone_number(number: str) -> None:
    # Costs are logged as decimal strings (`cost_usd`); seven digits must not read as a phone.
    assert redact(number) == number
    assert redact_event_dict(None, "info", {"cost_usd": number})["cost_usd"] == number


@pytest.mark.parametrize("text", ["4155550132", "555.123.4567", "call 0.002025 or +1 415 555 0132"])
def test_phone_numbers_next_to_decimals_are_still_masked(text: str) -> None:
    assert "0132" not in redact(text) and "4567" not in redact(text)
