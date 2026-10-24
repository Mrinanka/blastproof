"""Where seed.py and rebase_dates.py connect, and the guard in front of Neon.

Two targets:
- local: the Compose company-db, through its superuser (FAKE_NEON_ADMIN_URL, dev default below).
- neon: a Neon project's main branch, through its owner role (SEED_NEON_OWNER_URL, direct
  endpoint). The scripts print the target and stop unless --confirm names its host exactly,
  because loading or rebasing rewrites that branch (CLAUDE.md "Safety while building", D-054).

Settings come from the environment, then backend/.env. SEED_ENV_FILE overrides the .env path
(empty means none), so tests never read the developer's .env.
Messages never contain a password: scrub() removes it from anything printed.
"""

import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ENV = Path(__file__).resolve().parent.parent / "backend" / ".env"
LOCAL_ADMIN_DEFAULT = "postgresql://company:company@localhost:5433/postgres"


def _env_file() -> Path | None:
    override = os.environ.get("SEED_ENV_FILE")
    if override is None:
        return BACKEND_ENV
    return Path(override) if override else None


class SeedSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    fake_neon_admin_url: str = LOCAL_ADMIN_DEFAULT
    seed_neon_owner_url: str = ""
    company_db_reader_url: str = ""
    company_db_writer_url: str = ""


def load_settings() -> SeedSettings:
    return SeedSettings(_env_file=_env_file())  # type: ignore[call-arg]


class TargetError(Exception):
    """A problem the user must fix; printed without a traceback."""


def host_of(url: str) -> str:
    return urlsplit(url).hostname or ""


def describe(url: str) -> str:
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.hostname}{port}{parts.path or '/'} as {parts.username}"


def password_of(url: str) -> str:
    return unquote(urlsplit(url).password or "")


def user_of(url: str) -> str:
    return unquote(urlsplit(url).username or "")


def with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{database}"))


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def scrub(text: str, *urls: str) -> str:
    for url in urls:
        secret = password_of(url) if url else ""
        if secret:
            text = text.replace(secret, "***")
    return text


def require_confirmation(url: str, confirm: str | None, action: str) -> None:
    """Stop unless --confirm names the target's host exactly."""
    host = host_of(url)
    print(f"Target: {describe(url)}")
    if confirm != host:
        raise TargetError(
            f"This will {action}. Nothing was changed. If this is the right database, re-run "
            f"with --confirm {host}"
        )


def neon_owner_url(settings: SeedSettings) -> str:
    if not settings.seed_neon_owner_url:
        raise TargetError("SEED_NEON_OWNER_URL is not set (backend/.env or the environment).")
    if "-pooler" in host_of(settings.seed_neon_owner_url):
        raise TargetError("SEED_NEON_OWNER_URL must use the direct endpoint, not the -pooler one.")
    return settings.seed_neon_owner_url


def fail(message: str, *urls: str) -> None:
    print(f"error: {scrub(message, *urls)}", file=sys.stderr)
    raise SystemExit(1)
