"""Agent keys, password hashes and dashboard tokens (SEC-01, SEC-03, SEC-04). No database here.

Agent keys are `bp_` + 32 random bytes; only their SHA-256 hash and first 8 characters are stored.
Passwords are hashed with argon2id. Dashboard tokens are HS256 JWTs that live 8 hours and carry
the user's `token_version`, so bumping it revokes every token issued before.
"""

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

AGENT_KEY_PREFIX = "bp_"
KEY_PREFIX_LENGTH = 8
# token_urlsafe(32) gives 43 characters; anything else cannot be one of our keys.
_AGENT_KEY_PATTERN = re.compile(r"bp_[A-Za-z0-9_-]{43}")

TOKEN_LIFETIME = timedelta(hours=8)
TOKEN_ALGORITHM = "HS256"
TOKEN_ISSUER = "blastproof"
TOKEN_AUDIENCE = "blastproof-dashboard"
# HS256 needs a key at least as long as its digest (RFC 7518 §3.2).
MIN_SECRET_BYTES = 32
_REQUIRED_CLAIMS = ["sub", "tv", "iat", "exp", "iss", "aud"]
# Tolerates small clock differences for "issued in the future" only; expiry is exact.
_IAT_LEEWAY = timedelta(seconds=30)

_password_hash = PasswordHash.recommended()


@dataclass(frozen=True)
class NewAgentKey:
    key: str = field(repr=False)  # shown to the user once, never stored or logged
    key_hash: str
    key_prefix: str


def generate_agent_key() -> NewAgentKey:
    key = AGENT_KEY_PREFIX + secrets.token_urlsafe(32)
    return NewAgentKey(key=key, key_hash=hash_agent_key(key), key_prefix=key[:KEY_PREFIX_LENGTH])


def hash_agent_key(key: str) -> str:
    # A plain SHA-256 is enough: the key is 256 random bits, so there is nothing to brute-force.
    return hashlib.sha256(key.encode()).hexdigest()


def parse_agent_key(candidate: str) -> str | None:
    """The key if it has the shape of one of ours, else None (junk never reaches the database)."""
    return candidate if _AGENT_KEY_PATTERN.fullmatch(candidate) else None


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


@cache
def _dummy_hash() -> str:
    return _password_hash.hash(secrets.token_urlsafe(16))


def verify_password(password: str, hashed: str | None) -> tuple[bool, str | None]:
    """(matches, new hash if the stored one uses outdated settings).

    With no stored hash (unknown email) it still verifies against a dummy hash, so the response
    time does not reveal which emails have accounts. CPU-bound: call it in a worker thread.
    """
    if hashed is None:
        _password_hash.verify_and_update(password, _dummy_hash())
        return False, None
    try:
        return _password_hash.verify_and_update(password, hashed)
    except UnknownHashError:
        return False, None


class InvalidTokenError(Exception):
    """Any problem with a dashboard token. The message is for logs only, never for the client."""


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    token_version: int


def _check_secret(secret: str) -> None:
    if len(secret.encode()) < MIN_SECRET_BYTES:
        raise ValueError(f"JWT_SECRET must be at least {MIN_SECRET_BYTES} bytes")


def create_access_token(
    user_id: uuid.UUID, token_version: int, secret: str, *, now: datetime | None = None
) -> tuple[str, datetime]:
    _check_secret(secret)
    issued = (now or datetime.now(UTC)).replace(microsecond=0)
    expires_at = issued + TOKEN_LIFETIME
    payload = {
        "sub": str(user_id),
        "tv": token_version,
        "iat": int(issued.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
    }
    return jwt.encode(payload, secret, algorithm=TOKEN_ALGORITHM), expires_at


def decode_access_token(token: str, secret: str, *, now: datetime | None = None) -> TokenClaims:
    """The token's claims, or InvalidTokenError. Only HS256, our issuer and audience are accepted.

    Time checks are done here rather than by PyJWT, so tests can pass a fixed `now`.
    """
    _check_secret(secret)
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[TOKEN_ALGORITHM],
            audience=TOKEN_AUDIENCE,
            issuer=TOKEN_ISSUER,
            options={
                "require": _REQUIRED_CLAIMS,
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
                "enforce_minimum_key_length": True,
            },
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(type(exc).__name__) from exc

    now_ts = (now or datetime.now(UTC)).timestamp()
    exp, iat, tv = payload["exp"], payload["iat"], payload["tv"]
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (exp, iat, tv)):
        raise InvalidTokenError("malformed claims")
    if exp <= now_ts:
        raise InvalidTokenError("expired")
    if iat > now_ts + _IAT_LEEWAY.total_seconds():
        raise InvalidTokenError("issued in the future")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidTokenError("malformed subject") from exc
    return TokenClaims(user_id=user_id, token_version=tv)
