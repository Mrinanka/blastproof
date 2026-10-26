"""A disposable copy of the company database for a shadow run (backend plan step 10).

Two implementations of `NeonClient`:
- `NeonApiClient` creates a Neon branch of the company project (copy-on-write, so it is cheap) and
  deletes it afterwards. The branch gets `expires_at` as a backstop if a delete ever fails.
- `FakeNeonClient` clones the local `company_main` with `CREATE DATABASE ... TEMPLATE`. Tests, CI
  and the `local` profile use it; it never talks to Neon.

Neither logs or prints a password or the API key; errors carry a reason code only.
"""

import asyncio
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit
from uuid import UUID

import asyncpg
import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.core.clock import Clock
from app.services import company_db
from app.services.company_schema import quote_ident, quote_literal

NEON_API_URL = "https://console.neon.tech/api/v2"
SHADOW_BRANCH_PREFIX = "shadow-"
SHADOW_DATABASE_PREFIX = "shadow_"
BRANCH_LIFETIME = timedelta(minutes=15)
SWEEP_LOCK_KEY = 0x424C5357  # "BLSW": one startup sweep at a time across backend instances

GOOD_OPERATION_STATES = frozenset({"finished", "skipped"})
BAD_OPERATION_STATES = frozenset({"failed", "error", "cancelled"})
RETRIED_STATUSES = frozenset({423, 503})

logger = structlog.get_logger()


class NeonError(Exception):
    """The copy could not be made or removed. `reason_code` is safe to log and to show an agent."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class NeonBusyError(NeonError):
    """Neon refused another branch (the project's branch cap)."""


@dataclass(frozen=True)
class ShadowTarget:
    """A usable copy of the company database."""

    branch_id: str  # a Neon branch id, or the shadow_* database name
    # host, port, user, password, database, ssl: never logged or shown.
    connect_kwargs: dict[str, Any] = field(repr=False)


class NeonClient(Protocol):
    async def create_shadow(self, action_id: UUID) -> ShadowTarget:
        """A copy ready to connect to."""
        ...

    async def delete_shadow(self, target: ShadowTarget) -> None:
        """Remove the copy. Removing one that is already gone is not an error."""
        ...

    async def sweep(self, older_than: timedelta) -> int:
        """Delete leftover shadow copies older than `older_than`; return how many."""
        ...

    async def ping(self) -> None:
        """Raise if the copy service cannot be reached (for /ready)."""
        ...

    async def aclose(self) -> None: ...


def _connect_kwargs(url: str, *, database: str | None = None, host: str | None = None) -> dict:
    """asyncpg arguments for the writer login in `url`, optionally on another host or database."""
    parts = urlsplit(url)
    return {
        "host": host or parts.hostname,
        "port": parts.port or 5432,
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "database": database or parts.path.lstrip("/"),
    }


# --- Neon -------------------------------------------------------------------------------------


class NeonApiClient:
    def __init__(
        self,
        *,
        api_key: str,
        project_id: str,
        parent_branch_id: str,
        writer_url: str,
        clock: Clock,
        transport: httpx.AsyncBaseTransport | None = None,
        poll_interval: float = 0.5,
        operation_timeout: float = 30.0,
        retry_delays: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0),
        connect_timeout: float = 10.0,
        orphan_cleanup_timeout: float = 10.0,
        connect: Callable[..., Awaitable[asyncpg.Connection]] = asyncpg.connect,
    ) -> None:
        self._project_id = project_id
        self._parent_branch_id = parent_branch_id
        self._writer_url = writer_url
        self._clock = clock
        self._poll_interval = poll_interval
        self._operation_timeout = operation_timeout
        self._retry_delays = retry_delays
        self._connect_timeout = connect_timeout
        self._orphan_cleanup_timeout = orphan_cleanup_timeout
        self._connect = connect
        self._http = httpx.AsyncClient(
            base_url=NEON_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=httpx.Timeout(15.0),
            transport=transport,
        )

    def __repr__(self) -> str:
        return "NeonApiClient()"  # the API key and the writer password stay out of every repr

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self, method: str, path: str, *, retry: bool = True, **kwargs: Any
    ) -> httpx.Response:
        """One API call. 423 and 503 are safe to repeat; anything else non-2xx raises."""
        delays = iter(self._retry_delays if retry else ())
        while True:
            try:
                response = await self._http.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise NeonError(f"neon_{type(exc).__name__}") from None
            if response.status_code in RETRIED_STATUSES:
                delay = next(delays, None)
                if delay is not None:
                    await asyncio.sleep(delay)
                    continue
            return response

    @staticmethod
    def _fail(response: httpx.Response) -> NeonError:
        return NeonError(f"neon_{response.status_code}")

    async def ping(self) -> None:
        response = await self._request("GET", f"/projects/{self._project_id}", retry=False)
        if response.status_code != 200:
            raise self._fail(response)

    async def project_name(self) -> str:
        response = await self._request("GET", f"/projects/{self._project_id}", retry=False)
        if response.status_code != 200:
            raise self._fail(response)
        return str(response.json()["project"]["name"])

    async def create_shadow(self, action_id: UUID) -> ShadowTarget:
        name = f"{SHADOW_BRANCH_PREFIX}{action_id}"
        expires_at = (self._clock.now() + BRANCH_LIFETIME).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {
            "branch": {"parent_id": self._parent_branch_id, "name": name, "expires_at": expires_at},
            "endpoints": [{"type": "read_write"}],
        }
        # Until the answer is read, Neon may have made the branch without us learning its ID: a
        # timeout, a cancel (the shadow run's own timeout), a 5xx or an unreadable 2xx. Then the
        # branch is found by its name and deleted, so it does not hold one of the 10 slots.
        try:
            response = await self._request(
                "POST", f"/projects/{self._project_id}/branches", json=body
            )
        except BaseException:
            await self._delete_by_name_quietly(name)
            raise
        if response.status_code == 422 and "BRANCHES_LIMIT_EXCEEDED" in response.text:
            raise NeonBusyError("neon_branches_limit")
        if response.status_code not in (200, 201):
            if response.status_code >= 500:
                await self._delete_by_name_quietly(name)
            raise self._fail(response)
        try:
            created = response.json()
            branch_id = str(created["branch"]["id"])
            host = str(created["endpoints"][0]["host"])
            operation_ids = [str(op["id"]) for op in created.get("operations", [])]
        except (ValueError, KeyError, IndexError, TypeError):
            await self._delete_by_name_quietly(name)
            raise NeonError("neon_bad_response") from None
        target = ShadowTarget(
            branch_id=branch_id,
            connect_kwargs=_connect_kwargs(self._writer_url, host=host) | {"ssl": "require"},
        )
        try:
            await self._wait_for_operations(operation_ids)
            await self._wait_until_connectable(target)
        except BaseException:
            # The branch exists and counts against the cap: remove it before the error leaves.
            await self._delete_quietly(target)
            raise
        return target

    async def _wait_for_operations(self, operation_ids: list[str]) -> None:
        try:
            async with asyncio.timeout(self._operation_timeout):
                for operation_id in operation_ids:
                    while True:
                        response = await self._request(
                            "GET", f"/projects/{self._project_id}/operations/{operation_id}"
                        )
                        if response.status_code != 200:
                            raise self._fail(response)
                        try:
                            status = response.json()["operation"]["status"]
                        except (ValueError, KeyError, TypeError):
                            raise NeonError("neon_bad_response") from None
                        if status in GOOD_OPERATION_STATES:
                            break
                        if status in BAD_OPERATION_STATES:
                            raise NeonError("neon_operation_failed")
                        await asyncio.sleep(self._poll_interval)
        except TimeoutError:
            raise NeonError("neon_operation_timeout") from None

    async def _wait_until_connectable(self, target: ShadowTarget) -> None:
        """The compute needs about 2 s after its operations finish (spike): retry the connect."""
        deadline = asyncio.get_running_loop().time() + self._connect_timeout
        while True:
            try:
                conn = await self._connect(**target.connect_kwargs, timeout=5)
            except Exception:
                if asyncio.get_running_loop().time() >= deadline:
                    raise NeonError("neon_connect_failed") from None
                await asyncio.sleep(self._poll_interval)
                continue
            try:
                await conn.fetchval("SELECT 1")
            finally:
                await conn.close()
            return

    async def _delete_quietly(self, target: ShadowTarget) -> None:
        try:
            await asyncio.shield(self.delete_shadow(target))
        except Exception as exc:
            logger.error("shadow_delete_failed", reason_code=getattr(exc, "reason_code", None))

    async def _delete_by_name_quietly(self, name: str) -> None:
        """Best effort and never raises an Exception: `expires_at` and the sweep stay the backstops.

        A branch whose create is still running inside Neon may not be listed yet; those wait for
        the backstops too.
        """
        try:
            await asyncio.shield(self._delete_by_name(name))
        except Exception as exc:
            reason_code = getattr(exc, "reason_code", type(exc).__name__)
            logger.error("shadow_delete_failed", reason_code=reason_code)

    async def _delete_by_name(self, name: str) -> None:
        async with asyncio.timeout(self._orphan_cleanup_timeout):
            response = await self._request(
                "GET", f"/projects/{self._project_id}/branches", params={"search": name}
            )
            if response.status_code != 200:
                raise self._fail(response)
            for branch in response.json().get("branches", []):
                # `search` matches partial names and IDs: only this exact, non-default name is ours.
                if branch.get("name") == name and not branch.get("default"):
                    await self.delete_shadow(
                        ShadowTarget(branch_id=str(branch["id"]), connect_kwargs={})
                    )

    async def delete_shadow(self, target: ShadowTarget) -> None:
        response = await self._request(
            "DELETE", f"/projects/{self._project_id}/branches/{target.branch_id}"
        )
        if response.status_code not in (200, 204):  # 204: already gone
            raise self._fail(response)

    async def sweep(self, older_than: timedelta) -> int:
        cutoff = self._clock.now() - older_than
        deleted = 0
        cursor: str | None = None
        while True:
            params: dict[str, str | int] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            response = await self._request(
                "GET", f"/projects/{self._project_id}/branches", params=params
            )
            if response.status_code != 200:
                raise self._fail(response)
            payload = response.json()
            for branch in payload.get("branches", []):
                if (
                    str(branch.get("name", "")).startswith(SHADOW_BRANCH_PREFIX)
                    and not branch.get("default")
                    and datetime.fromisoformat(branch["created_at"]) < cutoff
                ):
                    await self.delete_shadow(
                        ShadowTarget(branch_id=branch["id"], connect_kwargs={})
                    )
                    deleted += 1
            cursor = (payload.get("pagination") or {}).get("next")
            if not cursor:
                return deleted


# --- Local clone ------------------------------------------------------------------------------


class FakeNeonClient:
    """`CREATE DATABASE shadow_<hex> TEMPLATE company_main`, for tests, CI and the local profile."""

    def __init__(
        self,
        *,
        admin_url: str,
        writer_url: str,
        reader_user: str = "blastproof_reader",
        source_db: str = "company_main",
        clock: Clock,
    ) -> None:
        self._admin_url = admin_url
        self._writer_url = writer_url
        self._reader_user = reader_user
        self._source_db = source_db
        self._clock = clock

    def __repr__(self) -> str:
        return "FakeNeonClient()"

    async def aclose(self) -> None:
        return None

    def _admin(self) -> Any:
        return company_db.open_connection(dsn=company_db.with_database(self._admin_url, "postgres"))

    async def ping(self) -> None:
        async with self._admin() as conn:
            await conn.fetchval("SELECT 1")

    async def create_shadow(self, action_id: UUID) -> ShadowTarget:
        name = f"{SHADOW_DATABASE_PREFIX}{secrets.token_hex(8)}"
        writer_user = unquote(urlsplit(self._writer_url).username or "")
        created = self._clock.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        target = ShadowTarget(
            branch_id=name, connect_kwargs=_connect_kwargs(self._writer_url, database=name)
        )
        db = quote_ident(name)
        try:
            async with (
                company_db.advisory_lock(self._admin_url, shared=False),
                self._admin() as conn,
            ):
                # Nothing may be connected to the template while it is copied.
                await conn.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    " WHERE datname = $1 AND pid <> pg_backend_pid()",
                    self._source_db,
                )
                await conn.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
                await conn.execute(f"CREATE DATABASE {db} TEMPLATE {quote_ident(self._source_db)}")
                # CREATE DATABASE does not copy database-level rights (see seed.py).
                await conn.execute(f"REVOKE ALL ON DATABASE {db} FROM PUBLIC")
                await conn.execute(
                    f"GRANT CONNECT ON DATABASE {db} TO {quote_ident(self._reader_user)}"
                )
                await conn.execute(
                    f"GRANT CONNECT, TEMPORARY ON DATABASE {db} TO {quote_ident(writer_user)}"
                )
                await conn.execute(
                    f"COMMENT ON DATABASE {db} IS {quote_literal(f'bp-shadow {created}')}"
                )
        except BaseException:
            await self._drop_quietly(name)
            raise
        return target

    async def _drop_quietly(self, name: str) -> None:
        try:
            await asyncio.shield(self._drop(name))
        except Exception as exc:
            logger.error("shadow_delete_failed", reason_code=type(exc).__name__)

    async def _drop(self, name: str) -> None:
        async with self._admin() as conn:
            await conn.execute(f"DROP DATABASE IF EXISTS {quote_ident(name)} WITH (FORCE)")

    async def delete_shadow(self, target: ShadowTarget) -> None:
        await self._drop(target.branch_id)

    async def sweep(self, older_than: timedelta) -> int:
        cutoff = self._clock.now() - older_than
        # Holding the clone lock means no clone is between CREATE DATABASE and its comment.
        async with company_db.advisory_lock(self._admin_url, shared=False), self._admin() as conn:
            rows = await conn.fetch(
                "SELECT datname::text AS name, shobj_description(oid, 'pg_database') AS comment"
                " FROM pg_database WHERE datname LIKE 'shadow\\_%'"
            )
            stale = [row["name"] for row in rows if _is_stale(row["comment"], cutoff)]
            for name in stale:
                await conn.execute(f"DROP DATABASE IF EXISTS {quote_ident(name)} WITH (FORCE)")
        return len(stale)


def _is_stale(comment: str | None, cutoff: datetime) -> bool:
    """A copy with no readable creation time counts as stale."""
    if not comment or not comment.startswith("bp-shadow "):
        return True
    try:
        return datetime.fromisoformat(comment.removeprefix("bp-shadow ")) < cutoff
    except ValueError:
        return True


# --- Choosing a client ------------------------------------------------------------------------


def neon_client_for(profile: str, settings: Settings, clock: Clock) -> NeonClient | None:
    """The client for a connection profile, or None when its settings are not filled in.

    `local` always clones locally. `neon` uses the Neon API, or local clones when
    NEON_CLIENT=fake (CI). Building a client opens no connection.
    """
    writer = (
        settings.local_company_writer_url if profile == "local" else settings.company_db_writer_url
    )
    if writer is None:
        return None
    if profile == "neon" and settings.neon_client == "api":
        if (
            settings.neon_api_key is None
            or settings.neon_project_id is None
            or settings.neon_parent_branch_id is None
        ):
            return None
        return NeonApiClient(
            api_key=settings.neon_api_key.get_secret_value(),
            project_id=settings.neon_project_id,
            parent_branch_id=settings.neon_parent_branch_id,
            writer_url=writer.get_secret_value(),
            clock=clock,
        )
    if settings.fake_neon_admin_url is None:
        return None
    reader = (
        settings.local_company_reader_url if profile == "local" else settings.company_db_reader_url
    )
    reader_user = unquote(urlsplit(reader.get_secret_value()).username or "") if reader else ""
    return FakeNeonClient(
        admin_url=settings.fake_neon_admin_url.get_secret_value(),
        writer_url=writer.get_secret_value(),
        reader_user=reader_user or "blastproof_reader",
        clock=clock,
    )


def build_neon_clients(settings: Settings, clock: Clock) -> dict[str, NeonClient]:
    clients: dict[str, NeonClient] = {}
    for profile in ("neon", "local"):
        client = neon_client_for(profile, settings, clock)
        if client is not None:
            clients[profile] = client
    return clients


async def sweep_stale_shadows(
    clients: Mapping[str, NeonClient], engine: AsyncEngine | None, *, older_than: timedelta
) -> None:
    """Delete shadow copies a crashed run left behind (BE-58). Never raises.

    Runs as a background task after startup, so a Neon outage never stops the app from starting.
    One backend instance at a time: the others find the lock taken and skip.
    """
    if engine is None or not clients:
        return
    try:
        async with engine.connect() as conn:
            got = (
                await conn.execute(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": SWEEP_LOCK_KEY}
                )
            ).scalar_one()
            if not got:
                return
            try:
                for profile, client in clients.items():
                    deleted = await client.sweep(older_than)
                    logger.info("shadow_sweep_done", profile=profile, deleted=deleted)
            finally:
                await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SWEEP_LOCK_KEY})
    except Exception as exc:
        logger.error("shadow_sweep_failed", reason_code=type(exc).__name__)
