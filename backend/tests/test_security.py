"""SEC-01, SEC-03, SEC-04: agent keys, password hashes and dashboard tokens (unit, no database)."""

import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core import security
from app.core.security import (
    TOKEN_LIFETIME,
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    generate_agent_key,
    hash_agent_key,
    hash_password,
    parse_agent_key,
    verify_password,
)

SECRET = "s" * 32
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
USER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


# --- Agent keys ---------------------------------------------------------------------------


def test_agent_key_format() -> None:
    new = generate_agent_key()
    # "bp_" + token_urlsafe(32): 32 random bytes are 43 URL-safe base64 characters.
    assert re.fullmatch(r"bp_[A-Za-z0-9_-]{43}", new.key)
    assert new.key_prefix == new.key[:8]
    assert len(new.key_prefix) == 8


def test_agent_key_hash_is_sha256_of_the_full_key() -> None:
    new = generate_agent_key()
    assert new.key_hash == hashlib.sha256(new.key.encode()).hexdigest()
    assert hash_agent_key(new.key) == new.key_hash


def test_agent_keys_differ() -> None:
    assert len({generate_agent_key().key for _ in range(50)}) == 50


def test_new_agent_key_repr_hides_the_key() -> None:
    new = generate_agent_key()
    assert new.key not in repr(new)


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "bp_",
        "sk_" + "a" * 43,
        "BP_" + "a" * 43,
        "bp_" + "a" * 200,
        "bp_" + "a" * 42 + " ",
        "bp_" + "a" * 42 + "!",
        "eyJhbGciOiJIUzI1NiJ9.e30.sig",
    ],
)
def test_malformed_agent_keys_are_rejected_before_lookup(candidate: str) -> None:
    assert parse_agent_key(candidate) is None


def test_well_formed_agent_key_parses() -> None:
    new = generate_agent_key()
    assert parse_agent_key(new.key) == new.key


# --- Passwords ----------------------------------------------------------------------------


def test_password_hash_is_argon2id_and_verifies() -> None:
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("$argon2id$")
    ok, _ = verify_password("correct horse battery", hashed)
    assert ok


def test_wrong_password_does_not_verify() -> None:
    hashed = hash_password("correct horse battery")
    ok, new_hash = verify_password("wrong horse battery", hashed)
    assert not ok
    assert new_hash is None


def test_unknown_user_still_runs_a_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    """Timing must not reveal whether an email exists: no hash still costs one argon2 verify."""
    calls: list[str] = []
    real = security._password_hash.verify_and_update

    def spy(password: str, hashed: str) -> tuple[bool, str | None]:
        calls.append(hashed)
        return real(password, hashed)

    monkeypatch.setattr(security._password_hash, "verify_and_update", spy)
    ok, new_hash = verify_password("anything at all", None)
    assert (ok, new_hash) == (False, None)
    assert len(calls) == 1
    assert calls[0].startswith("$argon2id$")


def test_unrecognised_stored_hash_fails_closed() -> None:
    ok, new_hash = verify_password("anything at all", "not-a-hash")
    assert (ok, new_hash) == (False, None)


# --- Tokens -------------------------------------------------------------------------------


def make_token(now: datetime = NOW, token_version: int = 3) -> str:
    token, _ = create_access_token(USER_ID, token_version, SECRET, now=now)
    return token


def test_token_round_trip() -> None:
    token, expires_at = create_access_token(USER_ID, 3, SECRET, now=NOW)
    claims = decode_access_token(token, SECRET, now=NOW + timedelta(hours=1))
    assert claims.user_id == USER_ID
    assert claims.token_version == 3
    assert expires_at == NOW + timedelta(hours=8)


def test_token_lives_exactly_eight_hours() -> None:
    assert timedelta(hours=8) == TOKEN_LIFETIME
    payload = jwt.decode(make_token(), options={"verify_signature": False})
    assert payload["exp"] - payload["iat"] == 8 * 3600
    assert payload["iss"] == "blastproof"
    assert payload["aud"] == "blastproof-dashboard"
    assert payload["sub"] == str(USER_ID)
    assert payload["tv"] == 3


def test_token_is_valid_just_before_expiry_and_not_at_it() -> None:
    token = make_token()
    decode_access_token(token, SECRET, now=NOW + TOKEN_LIFETIME - timedelta(seconds=1))
    with pytest.raises(InvalidTokenError, match="expired"):
        decode_access_token(token, SECRET, now=NOW + TOKEN_LIFETIME)


def test_token_signed_with_another_secret_is_rejected() -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token(make_token(), "t" * 32, now=NOW)


def _sign(payload: dict, algorithm: str = "HS256", key: str = SECRET) -> str:
    return jwt.encode(payload, key, algorithm=algorithm)


def _claims(**overrides: object) -> dict:
    claims: dict = {
        "sub": str(USER_ID),
        "tv": 0,
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + TOKEN_LIFETIME).timestamp()),
        "iss": "blastproof",
        "aud": "blastproof-dashboard",
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def test_alg_none_is_rejected() -> None:
    token = jwt.encode(_claims(), None, algorithm="none")  # type: ignore[arg-type]
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, SECRET, now=NOW)


def test_other_hmac_algorithm_is_rejected() -> None:
    token = _sign(_claims(), algorithm="HS512", key=SECRET * 2)
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, SECRET * 2, now=NOW)


@pytest.mark.parametrize("missing", ["sub", "tv", "iat", "exp", "iss", "aud"])
def test_token_missing_a_claim_is_rejected(missing: str) -> None:
    token = _sign(_claims(**{missing: None}))
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, SECRET, now=NOW)


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "someone-else"},
        {"aud": "another-app"},
        {"sub": "not-a-uuid"},
        {"tv": "3"},
        {"tv": True},
    ],
)
def test_token_with_a_wrong_claim_is_rejected(overrides: dict) -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token(_sign(_claims(**overrides)), SECRET, now=NOW)


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", generate_agent_key().key])
def test_garbage_and_agent_keys_are_not_tokens(garbage: str) -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token(garbage, SECRET, now=NOW)


def test_short_secret_is_refused() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        create_access_token(USER_ID, 0, "short", now=NOW)
