"""THROWAWAY SPIKE: how Neon branching behaves for step 10 (NeonApiClient and shadow runs).

Not production code. No imports from backend/app or demo_company. Dev project only:
it refuses to run unless the Neon project is named `blastproof-company-dev`, never writes to the
parent branch, and deletes only branches it created itself (names `spike-*`).

Reads NEON_API_KEY, NEON_PROJECT_ID, NEON_PARENT_BRANCH_ID, COMPANY_DB_WRITER_URL (and optionally
COMPANY_DB_READER_URL) from the environment, then backend/.env. Nothing secret is printed.

Run from the repo root:
  uv run --project backend python spikes/neon_branching.py probe
  uv run --project backend python spikes/neon_branching.py latency --runs 20
  uv run --project backend python spikes/neon_branching.py cleanup

Docs: https://neon.com/docs/manage/branches , https://api-docs.neon.tech/reference/createprojectbranch ,
https://neon.com/docs/manage/operations , https://neon.com/docs/guides/branch-expiration ,
https://neon.com/docs/connect/connection-pooling , https://neon.com/docs/reference/api/key-concepts
"""

import argparse
import asyncio
import os
import re
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import asyncpg
import httpx

REPO = Path(__file__).resolve().parents[1]
API = "https://console.neon.tech/api/v2"
DEV_PROJECT_NAME = "blastproof-company-dev"
TERMINAL_OK = {"finished", "skipped"}
TERMINAL_BAD = {"failed", "error", "cancelled"}
SERVER_SETTINGS = {"TimeZone": "UTC", "extra_float_digits": "1"}

SECRETS: set[str] = set()
CREATED: dict[str, str] = {}  # branch id -> name, for every branch this run made and still owns


# ---------------------------------------------------------------------------------------------
# Environment and output
# ---------------------------------------------------------------------------------------------


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = REPO / "backend" / ".env"
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            env[key.strip()] = value
    env.update(
        {k: v for k, v in os.environ.items() if k in env or k.startswith(("NEON_", "COMPANY_"))}
    )
    return env


HOST_RE = re.compile(r"ep-[a-z0-9-]+(?:\.[a-z0-9-]+)*\.neon\.tech")


def redact(text: object) -> str:
    out = str(text)
    for secret in SECRETS:
        if secret:
            out = out.replace(secret, "<secret>")
    return HOST_RE.sub("ep-xxxx…", out)


def out(line: str = "") -> None:
    print(redact(line), flush=True)


def report(q: str, status: str, text: str) -> None:
    out(f"[{q}] {status:<4} {text}")


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


# ---------------------------------------------------------------------------------------------
# Neon REST client
# ---------------------------------------------------------------------------------------------


class NeonSpike:
    def __init__(self, api_key: str, project_id: str) -> None:
        self.project = project_id
        self.http = httpx.AsyncClient(
            base_url=API,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=30,
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def request(
        self, method: str, path: str, *, retry: bool = True, **kwargs: Any
    ) -> httpx.Response:
        """One call; on 423 (project locked by another operation) retry 0.1 s doubling, 5 tries."""
        delay = 0.1
        for attempt in range(5):
            resp = await self.http.request(method, path, **kwargs)
            if resp.status_code != 423 or not retry or attempt == 4:
                return resp
            await asyncio.sleep(delay)
            delay *= 2
        return resp  # pragma: no cover

    async def get_project(self) -> dict[str, Any]:
        resp = await self.request("GET", f"/projects/{self.project}")
        resp.raise_for_status()
        return resp.json()["project"]

    async def list_branches(self) -> list[dict[str, Any]]:
        resp = await self.request("GET", f"/projects/{self.project}/branches")
        resp.raise_for_status()
        return resp.json()["branches"]

    async def get_branch(self, branch_id: str) -> httpx.Response:
        return await self.request("GET", f"/projects/{self.project}/branches/{branch_id}")

    async def create_branch(
        self,
        name: str,
        parent_id: str,
        *,
        endpoint: bool = True,
        expires_at: str | None = None,
        retry: bool = True,
    ) -> httpx.Response:
        branch: dict[str, Any] = {"parent_id": parent_id, "name": name}
        if expires_at:
            branch["expires_at"] = expires_at
        body: dict[str, Any] = {"branch": branch}
        if endpoint:
            body["endpoints"] = [{"type": "read_write"}]
        resp = await self.request(
            "POST", f"/projects/{self.project}/branches", json=body, retry=retry
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            CREATED[data["branch"]["id"]] = name
        return resp

    async def delete_branch(self, branch_id: str, *, retry: bool = True) -> httpx.Response:
        resp = await self.request(
            "DELETE", f"/projects/{self.project}/branches/{branch_id}", retry=retry
        )
        if resp.status_code in (200, 404):
            CREATED.pop(branch_id, None)
        return resp

    async def operation(self, op_id: str) -> dict[str, Any]:
        resp = await self.request("GET", f"/projects/{self.project}/operations/{op_id}")
        resp.raise_for_status()
        return resp.json()["operation"]

    async def wait_operations(
        self, ops: list[dict[str, Any]], poll: float = 0.5, cap: float = 120
    ) -> dict[str, Any]:
        """Poll each operation to a terminal state. Returns timings and status transitions."""
        start = time.monotonic()
        info: dict[str, Any] = {"ops": {}, "ok": True}
        pending = {op["id"]: op for op in ops}
        seen: dict[str, list[str]] = {op["id"]: [op.get("status", "?")] for op in ops}
        finished_at: dict[str, float] = {}
        while pending:
            if time.monotonic() - start > cap:
                info["ok"] = False
                info["timeout"] = True
                break
            for op_id in list(pending):
                op = await self.operation(op_id)
                status = op["status"]
                if seen[op_id][-1] != status:
                    seen[op_id].append(status)
                if status in TERMINAL_OK or status in TERMINAL_BAD:
                    finished_at[op_id] = time.monotonic() - start
                    if status in TERMINAL_BAD:
                        info["ok"] = False
                    del pending[op_id]
            if pending:
                await asyncio.sleep(poll)
        for op in ops:
            info["ops"][op["id"]] = {
                "action": op.get("action"),
                "transitions": seen[op["id"]],
                "finished_after_s": round(finished_at.get(op["id"], -1), 2),
            }
        info["total_s"] = round(time.monotonic() - start, 2)
        return info

    async def endpoint_host(self, create_json: dict[str, Any]) -> str:
        endpoints = create_json.get("endpoints") or []
        if endpoints and endpoints[0].get("host"):
            return endpoints[0]["host"]
        resp = await self.request(
            "GET", f"/projects/{self.project}/branches/{create_json['branch']['id']}/endpoints"
        )
        resp.raise_for_status()
        return resp.json()["endpoints"][0]["host"]


# ---------------------------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------------------------


class Dsn:
    """User, password and database taken from a company URL; the host comes from the branch."""

    def __init__(self, url: str) -> None:
        parts = urlparse(url)
        self.user = unquote(parts.username or "")
        self.password = unquote(parts.password or "")
        self.database = (parts.path or "/neondb").lstrip("/") or "neondb"
        SECRETS.add(self.password)

    def kwargs(self, host: str, *, pooled: bool = False) -> dict[str, Any]:
        if pooled:
            first, _, rest = host.partition(".")
            host = f"{first}-pooler.{rest}"
        return {
            "host": host,
            "port": 5432,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "ssl": "require",
            "timeout": 60,
        }


async def connect(
    dsn: Dsn, host: str, *, pooled: bool = False, settings: bool = True, **extra: Any
) -> asyncpg.Connection:
    kwargs = dsn.kwargs(host, pooled=pooled)
    if settings:
        kwargs["server_settings"] = dict(SERVER_SETTINGS)
    kwargs.update(extra)
    return await asyncpg.connect(**kwargs)


async def first_query(dsn: Dsn, host: str, attempts: int = 30) -> tuple[asyncpg.Connection, int]:
    """Connect (retrying while the compute wakes) and run SELECT 1. Returns (conn, attempts)."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            conn = await connect(dsn, host)
            await conn.fetchval("SELECT 1")
            return conn, attempt
        except Exception as exc:  # noqa: BLE001 - spike: record and retry
            last = exc
            await asyncio.sleep(0.5)
    raise RuntimeError(f"no connection after {attempts} attempts: {type(last).__name__}: {last}")


# ---------------------------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------------------------


async def guard(neon: NeonSpike, parent_id: str) -> dict[str, Any]:
    project = await neon.get_project()
    if project.get("name") != DEV_PROJECT_NAME:
        raise SystemExit(
            f"REFUSING: project name is {project.get('name')!r}, not {DEV_PROJECT_NAME!r}"
        )
    return project


async def safe_delete(neon: NeonSpike, branch_id: str, parent_id: str, default_id: str) -> None:
    """Delete only a branch this run created and that is not the parent or the default."""
    name = CREATED.get(branch_id)
    if branch_id in (parent_id, default_id) or not name or not name.startswith("spike-"):
        out(f"  (refusing to delete {branch_id}: not a branch this run created)")
        return
    resp = await neon.delete_branch(branch_id)
    if resp.status_code not in (200, 404):
        out(f"  (delete {branch_id} returned {resp.status_code}: {resp.text[:200]})")


async def delete_all_created(neon: NeonSpike, parent_id: str, default_id: str) -> None:
    for branch_id in list(CREATED):
        try:
            await safe_delete(neon, branch_id, parent_id, default_id)
        except Exception as exc:  # noqa: BLE001
            out(f"  (cleanup of {branch_id} failed: {type(exc).__name__}: {exc})")


# ---------------------------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------------------------


def keys_of(data: Any, depth: int = 1) -> Any:
    if isinstance(data, dict):
        return {k: keys_of(v, depth - 1) if depth > 0 else "…" for k, v in data.items()}
    if isinstance(data, list):
        return [keys_of(data[0], depth)] if data else []
    return type(data).__name__


def error_shape(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return f"{resp.status_code} non-JSON body {resp.text[:120]!r}"
    return f"{resp.status_code} keys={sorted(body) if isinstance(body, dict) else type(body).__name__} body={str(body)[:240]}"


async def probe(env: dict[str, str]) -> None:
    neon = NeonSpike(env["NEON_API_KEY"], env["NEON_PROJECT_ID"])
    parent = env["NEON_PARENT_BRANCH_ID"]
    writer = Dsn(env["COMPANY_DB_WRITER_URL"])
    reader = Dsn(env["COMPANY_DB_READER_URL"]) if env.get("COMPANY_DB_READER_URL") else None
    default_id = ""
    try:
        project = await guard(neon, parent)
        branches = await neon.list_branches()
        default_id = next((b["id"] for b in branches if b.get("default") or b.get("primary")), "")
        out(f"Project {project['name']}: {len(branches)} existing branches before the probe")
        for b in branches:
            out(
                f"  existing branch {b['id']} name={b['name']} default={b.get('default')} "
                f"current_state={b.get('current_state')}"
            )

        # Q1 -------------------------------------------------------------------------------
        report(
            "Q1",
            "INFO",
            f"project pg_version={project.get('pg_version')} region_id={project.get('region_id')} "
            f"plan keys: {[k for k in project if 'quota' in k or 'plan' in k or 'compute' in k]}",
        )

        # Q2 + Q3 --------------------------------------------------------------------------
        name = f"spike-{uuid.uuid4().hex[:8]}"
        t0 = time.monotonic()
        resp = await neon.create_branch(name, parent)
        t_post = time.monotonic() - t0
        ok = resp.status_code == 201
        report(
            "Q2",
            "PASS" if ok else "FAIL",
            f"POST create-branch -> {resp.status_code} in {t_post:.2f}s",
        )
        if not ok:
            out(f"  body: {resp.text[:400]}")
            return
        created = resp.json()
        branch_id = created["branch"]["id"]
        out(f"  response top-level keys: {sorted(created)}")
        out(f"  branch keys: {sorted(created['branch'])}")
        out(
            f"  branch.current_state={created['branch'].get('current_state')}, "
            f"parent_id matches={created['branch'].get('parent_id') == parent}"
        )
        out(
            f"  endpoint keys: {sorted(created['endpoints'][0]) if created.get('endpoints') else None}"
        )
        out(
            f"  endpoint type={created['endpoints'][0].get('type')} "
            f"current_state={created['endpoints'][0].get('current_state')} "
            f"pooler_enabled={created['endpoints'][0].get('pooler_enabled')} "
            f"autoscaling min/max={created['endpoints'][0].get('autoscaling_limit_min_cu')}/"
            f"{created['endpoints'][0].get('autoscaling_limit_max_cu')} "
            f"suspend_timeout_seconds={created['endpoints'][0].get('suspend_timeout_seconds')}"
        )
        out(
            f"  operations at 201: {[(o['action'], o['status']) for o in created.get('operations', [])]}"
        )
        host = await neon.endpoint_host(created)

        # Q4 (part 1): is connection_uris there?
        uris = created.get("connection_uris")
        report(
            "Q4",
            "INFO",
            f"connection_uris present={uris is not None}"
            + (f" count={len(uris)}" if uris else " (parent has several roles/databases)"),
        )

        # Q3: connect BEFORE the operations finish
        t_conn0 = time.monotonic()
        conn, attempts = await first_query(writer, host)
        t_conn = time.monotonic() - t_conn0
        statuses = [(await neon.operation(o["id"]))["status"] for o in created["operations"]]
        report(
            "Q3",
            "INFO",
            f"first SELECT 1 as writer worked {t_conn:.2f}s after POST returned "
            f"(attempts={attempts}); operation statuses at that moment: {statuses}",
        )
        wait = await neon.wait_operations(created["operations"])
        report("Q3", "PASS" if wait["ok"] else "FAIL", f"operations to terminal: {wait}")

        # Q4 (part 2): the writer on the new endpoint
        row = await conn.fetchrow(
            "SELECT current_user AS u, current_database() AS d, version() AS v, "
            "current_setting('server_version') AS sv, current_setting('TimeZone') AS tz, "
            "has_database_privilege(current_user, current_database(), 'TEMPORARY') AS tmp"
        )
        out(
            f"  current_user={row['u']} database={row['d']} server_version={row['sv']} "
            f"TimeZone={row['tz']} TEMPORARY privilege={row['tmp']}"
        )
        report(
            "Q1",
            "INFO" if row["sv"].startswith("18") else "FAIL",
            f"branch SHOW server_version={row['sv']}",
        )
        try:
            await conn.execute("CREATE TEMP TABLE spike_tmp (a int)")
            await conn.execute("INSERT INTO spike_tmp VALUES (1)")
            report(
                "Q4",
                "PASS",
                "writer on the branch (direct endpoint, our own DSN) can CREATE TEMP TABLE",
            )
        except Exception as exc:  # noqa: BLE001
            report("Q4", "FAIL", f"CREATE TEMP TABLE: {type(exc).__name__}: {exc}")

        # Q5 -------------------------------------------------------------------------------
        users = await conn.fetchval("SELECT count(*) FROM users")
        report(
            "Q5",
            "PASS" if users == 50_000 else "FAIL",
            f"branch has users count={users} (main: 50,000)",
        )
        if reader:
            rconn = await connect(reader, host)
            try:
                await rconn.execute("INSERT INTO feature_flags DEFAULT VALUES")
                report("Q5", "FAIL", "reader could INSERT on the branch")
            except asyncpg.InsufficientPrivilegeError as exc:
                report("Q5", "PASS", f"blastproof_reader INSERT refused on the branch: {exc}")
            except Exception as exc:  # noqa: BLE001
                report("Q5", "INFO", f"reader INSERT failed with {type(exc).__name__}: {exc}")
            finally:
                await rconn.close()
        else:
            report("Q5", "INFO", "COMPANY_DB_READER_URL not set; reader check skipped")
        await conn.close()

        # Q6 -------------------------------------------------------------------------------
        for label, pooled in (("direct", False), ("pooled", True)):
            await q6(writer, host, label, pooled)

        # Q8: delete, second delete, list --------------------------------------------------
        t_d = time.monotonic()
        d1 = await neon.delete_branch(branch_id)
        d1_s = time.monotonic() - t_d
        ops = d1.json().get("operations", []) if d1.status_code == 200 else []
        report(
            "Q8",
            "PASS" if d1.status_code == 200 else "FAIL",
            f"DELETE -> {d1.status_code} in {d1_s:.2f}s, operations {[(o['action'], o['status']) for o in ops]}",
        )
        if ops:
            out(f"  delete operations: {await neon.wait_operations(ops)}")
        d2 = await neon.delete_branch(branch_id)
        report("Q8", "INFO", f"second DELETE -> {error_shape(d2)}")
        g = await neon.get_branch(branch_id)
        report("Q8", "INFO", f"GET deleted branch -> {error_shape(g)}")
        listed = await neon.list_branches()
        names = [b["name"] for b in listed]
        out(f"  list_branches fields: {sorted(listed[0])}")
        report(
            "Q8",
            "PASS" if name not in names else "FAIL",
            f"deleted branch gone from the listing: {name not in names}",
        )
        # Q11: connecting to the deleted branch's host
        try:
            c = await connect(writer, host)
            await c.fetchval("SELECT 1")
            await c.close()
            report("Q11", "INFO", "connect to the deleted branch's host still worked")
        except Exception as exc:  # noqa: BLE001
            report(
                "Q11", "INFO", f"connect to a deleted branch's host -> {type(exc).__name__}: {exc}"
            )

        # Q7: expires_at --------------------------------------------------------------------
        soon = (datetime.now(UTC) + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r7 = await neon.create_branch(
            f"spike-exp-{uuid.uuid4().hex[:6]}", parent, endpoint=False, expires_at=soon
        )
        if r7.status_code == 201:
            b7 = r7.json()["branch"]
            report(
                "Q7",
                "PASS",
                f"expires_at accepted on create (201); sent {soon}, read back "
                f"{b7.get('expires_at')!r}",
            )
            again = await neon.get_branch(b7["id"])
            out(f"  GET branch expires_at={again.json()['branch'].get('expires_at')!r}")
            await safe_delete(neon, b7["id"], parent, default_id)
        else:
            report("Q7", "FAIL", f"expires_at on create refused: {error_shape(r7)}")
        past = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r7b = await neon.create_branch(
            f"spike-exp-{uuid.uuid4().hex[:6]}", parent, endpoint=False, expires_at=past
        )
        report("Q7", "INFO", f"expires_at in the past -> {error_shape(r7b)}")
        if r7b.status_code == 201:
            await safe_delete(neon, r7b.json()["branch"]["id"], parent, default_id)
        report("Q7", "INFO", "auto-deletion timing not measured (needs a 15+ minute wait)")

        # Q9: concurrency and caps ------------------------------------------------------------
        a, b = await asyncio.gather(
            neon.create_branch(f"spike-par-{uuid.uuid4().hex[:6]}", parent, retry=False),
            neon.create_branch(f"spike-par-{uuid.uuid4().hex[:6]}", parent, retry=False),
        )
        report(
            "Q9",
            "INFO",
            f"two simultaneous creates without retry -> {a.status_code}, {b.status_code}",
        )
        for r in (a, b):
            if r.status_code != 201:
                out(f"  non-201 body: {error_shape(r)}")
        for r in (a, b):
            if r.status_code == 201:
                await neon.wait_operations(r.json()["operations"])
        running = [e for r in (a, b) if r.status_code == 201 for e in r.json().get("endpoints", [])]
        out(
            f"  endpoints created by those two: {[(e.get('type'), e.get('current_state')) for e in running]}"
        )
        for r in (a, b):
            if r.status_code == 201:
                await safe_delete(neon, r.json()["branch"]["id"], parent, default_id)

        existing = len(await neon.list_branches())
        out(f"  branches before the cap test: {existing}")
        cap_error: httpx.Response | None = None
        made = 0
        for i in range(14):
            r = await neon.create_branch(
                f"spike-cap-{i}-{uuid.uuid4().hex[:4]}", parent, endpoint=False
            )
            if r.status_code != 201:
                cap_error = r
                break
            made += 1
        if cap_error is not None:
            report(
                "Q9",
                "INFO",
                f"branch cap hit after creating {made} more (total {existing + made}): "
                f"{error_shape(cap_error)}",
            )
        else:
            report(
                "Q9", "INFO", f"no cap reached after {made} more branches (total {existing + made})"
            )
        await delete_all_created(neon, parent, default_id)

        # Q11: failure shapes ----------------------------------------------------------------
        bad_key = NeonSpike("not-a-real-key", env["NEON_PROJECT_ID"])
        try:
            r = await bad_key.request("GET", f"/projects/{env['NEON_PROJECT_ID']}")
            report("Q11", "INFO", f"wrong API key -> {error_shape(r)}")
        finally:
            await bad_key.aclose()
        r = await neon.create_branch(
            f"spike-bad-{uuid.uuid4().hex[:6]}", "br-does-not-exist-123456", endpoint=False
        )
        report("Q11", "INFO", f"bad parent_id -> {error_shape(r)}")
        if r.status_code == 201:
            await delete_all_created(neon, parent, default_id)
        r = await neon.request("GET", "/projects/does-not-exist-123456")
        report("Q11", "INFO", f"unknown project -> {error_shape(r)}")
    finally:
        await delete_all_created(neon, parent, default_id)
        left = [b["name"] for b in await neon.list_branches() if b["name"].startswith("spike-")]
        out(f"Leftover spike-* branches after the probe: {left}")
        await neon.aclose()


async def q6(writer: Dsn, host: str, label: str, pooled: bool) -> None:
    q = "Q6"
    try:
        conn = await connect(writer, host, pooled=pooled, settings=False)
    except Exception as exc:  # noqa: BLE001
        report(q, "INFO", f"{label}: connect failed: {type(exc).__name__}: {exc}")
        return
    try:
        # prepared statements: parameterised fetch x20 with the default statement cache
        fails = 0
        err = ""
        for i in range(20):
            try:
                assert await conn.fetchval("SELECT $1::int + 1", i) == i + 1
            except Exception as exc:  # noqa: BLE001
                fails += 1
                err = f"{type(exc).__name__}: {exc}"
        report(
            q,
            "PASS" if fails == 0 else "FAIL",
            f"{label}: 20 parameterised fetches, {fails} failed {err}",
        )
        # SET TimeZone persistence across separate statements
        await conn.execute("SET TimeZone = 'America/New_York'")
        seen = [await conn.fetchval("SHOW TimeZone") for _ in range(10)]
        kept = sum(1 for z in seen if z == "America/New_York")
        report(
            q,
            "PASS" if kept == 10 else "FAIL",
            f"{label}: SET TimeZone kept for {kept}/10 later statements",
        )
        # temp table across statements
        try:
            await conn.execute("CREATE TEMP TABLE q6_tmp (a int)")
            ok = 0
            for _ in range(10):
                try:
                    await conn.fetchval("SELECT count(*) FROM q6_tmp")
                    ok += 1
                except asyncpg.UndefinedTableError:
                    pass
            report(
                q,
                "PASS" if ok == 10 else "FAIL",
                f"{label}: temp table visible in {ok}/10 later statements",
            )
        except Exception as exc:  # noqa: BLE001
            report(q, "FAIL", f"{label}: CREATE TEMP TABLE: {type(exc).__name__}: {exc}")
    finally:
        await conn.close()
    if pooled:
        try:
            c2 = await connect(writer, host, pooled=True, settings=True)
            report(
                q,
                "INFO",
                "pooled: connecting with server_settings (TimeZone, extra_float_digits) worked",
            )
            await c2.close()
        except Exception as exc:  # noqa: BLE001
            report(
                q,
                "INFO",
                f"pooled: connecting with server_settings failed: {type(exc).__name__}: {exc}",
            )


# ---------------------------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------------------------

HASH_TABLES = [
    "organizations", "users", "subscriptions", "payments", "invoices", "products",
    "orders", "order_items", "support_tickets", "feature_flags", "customer_notes",
]  # fmt: skip
DIFF_TABLES = ["users", "orders", "order_items", "invoices"]
INCIDENT_DELETE = "DELETE FROM users WHERE last_login_at < TIMESTAMPTZ '2026-04-01 00:00:00+00'"


async def shadow_workload(conn: asyncpg.Connection) -> dict[str, float]:
    """Shaped like a shadow run: hash every table, run the delete in a transaction, diff, roll back."""
    t = time.monotonic()
    for table in HASH_TABLES:
        await conn.execute(
            f"CREATE TEMP TABLE h_{table} AS SELECT md5(x::text) AS h FROM {table} x"
        )
    hashed = time.monotonic() - t
    t = time.monotonic()
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(INCIDENT_DELETE)
        for table in DIFF_TABLES:
            await conn.execute(
                f"CREATE TEMP TABLE a_{table} AS SELECT md5(x::text) AS h FROM {table} x"
            )
            await conn.fetchval(
                f"SELECT count(*) FROM (SELECT h FROM h_{table} EXCEPT SELECT h FROM a_{table}) d"
            )
    finally:
        await tx.rollback()
    return {"hash_s": hashed, "delete_diff_s": time.monotonic() - t}


async def latency(env: dict[str, str], runs: int) -> None:
    neon = NeonSpike(env["NEON_API_KEY"], env["NEON_PROJECT_ID"])
    parent = env["NEON_PARENT_BRANCH_ID"]
    writer = Dsn(env["COMPANY_DB_WRITER_URL"])
    default_id = ""
    rows: list[dict[str, float]] = []
    try:
        await guard(neon, parent)
        branches = await neon.list_branches()
        default_id = next((b["id"] for b in branches if b.get("default") or b.get("primary")), "")
        out(
            f"Latency: {runs} sequential runs; t0=POST sent, t1=201, t2=ops finished, "
            f"t3=first SELECT 1 as writer, t4=delete returned (seconds)"
        )
        for i in range(runs):
            name = f"spike-lat-{uuid.uuid4().hex[:8]}"
            row: dict[str, float] = {}
            branch_id = ""
            try:
                t0 = time.monotonic()
                resp = await neon.create_branch(name, parent)
                row["t1"] = time.monotonic() - t0
                if resp.status_code != 201:
                    out(f"run {i + 1}: create failed {error_shape(resp)}")
                    continue
                data = resp.json()
                branch_id = data["branch"]["id"]
                host = await neon.endpoint_host(data)
                wait = await neon.wait_operations(data["operations"], poll=0.25)
                row["t2"] = time.monotonic() - t0
                conn, _ = await first_query(writer, host)
                row["t3"] = time.monotonic() - t0
                if i < 5:
                    try:
                        row.update(await shadow_workload(conn))
                    except Exception as exc:  # noqa: BLE001
                        out(f"run {i + 1}: workload failed {type(exc).__name__}: {exc}")
                await conn.close()
                t4s = time.monotonic()
                await neon.delete_branch(branch_id)
                row["t4"] = time.monotonic() - t4s
                rows.append(row)
                extra = (
                    f" hash={row['hash_s']:.2f} delete+diff={row['delete_diff_s']:.2f}"
                    if "hash_s" in row
                    else ""
                )
                out(
                    f"run {i + 1:>2}: t1={row['t1']:.2f} t2={row['t2']:.2f} t3={row['t3']:.2f} "
                    f"delete={row['t4']:.2f} ops_ok={wait['ok']}{extra}"
                )
            finally:
                if branch_id in CREATED:
                    await safe_delete(neon, branch_id, parent, default_id)
        out()
        for key, label in (
            ("t1", "t1 POST returned"),
            ("t2", "t2 operations finished"),
            ("t3", "t3 first query (HEADLINE)"),
            ("t4", "delete call"),
        ):
            vals = [r[key] for r in rows if key in r]
            if vals:
                out(
                    f"{label:<28} n={len(vals)} p50={statistics.median(vals):.2f} "
                    f"p95={pct(vals, 0.95):.2f} max={max(vals):.2f}"
                )
        for key, label in (
            ("hash_s", "workload: hash 11 tables"),
            ("delete_diff_s", "workload: delete+diff+rollback"),
        ):
            vals = [r[key] for r in rows if key in r]
            if vals:
                out(
                    f"{label:<28} n={len(vals)} p50={statistics.median(vals):.2f} max={max(vals):.2f}"
                )
        pre = [r["t3"] + r["hash_s"] + r["delete_diff_s"] for r in rows if "hash_s" in r]
        if pre:
            out(
                f"{'preview total (t3+workload)':<28} n={len(pre)} p50={statistics.median(pre):.2f} max={max(pre):.2f}"
            )
        out(
            "(timings are from this dev machine to aws-us-east-2; Fly in the same region has shorter network legs)"
        )
    finally:
        await delete_all_created(neon, parent, default_id)
        left = [b["name"] for b in await neon.list_branches() if b["name"].startswith("spike-")]
        out(f"Leftover spike-* branches after the latency run: {left}")
        await neon.aclose()


# ---------------------------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------------------------


async def cleanup(env: dict[str, str]) -> None:
    neon = NeonSpike(env["NEON_API_KEY"], env["NEON_PROJECT_ID"])
    parent = env["NEON_PARENT_BRANCH_ID"]
    try:
        await guard(neon, parent)
        branches = await neon.list_branches()
        default_id = next((b["id"] for b in branches if b.get("default") or b.get("primary")), "")
        leftovers = [
            b
            for b in branches
            if b["name"].startswith("spike-") and b["id"] not in (parent, default_id)
        ]
        out(f"{len(leftovers)} spike-* branches found")
        for b in leftovers:
            CREATED[b["id"]] = b["name"]
            await safe_delete(neon, b["id"], parent, default_id)
            out(f"  deleted {b['id']} {b['name']} created_at={b.get('created_at')}")
        after = [b["name"] for b in await neon.list_branches() if b["name"].startswith("spike-")]
        out(f"spike-* branches remaining: {after}")
    finally:
        await neon.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe")
    lat = sub.add_parser("latency")
    lat.add_argument("--runs", type=int, default=20)
    sub.add_parser("cleanup")
    args = parser.parse_args()

    env = load_env()
    missing = [
        k
        for k in (
            "NEON_API_KEY",
            "NEON_PROJECT_ID",
            "NEON_PARENT_BRANCH_ID",
            "COMPANY_DB_WRITER_URL",
        )
        if not env.get(k)
    ]
    if missing:
        sys.exit(f"Missing settings (names only): {missing}")
    SECRETS.add(env["NEON_API_KEY"])
    if args.command == "probe":
        asyncio.run(probe(env))
    elif args.command == "latency":
        asyncio.run(latency(env, args.runs))
    else:
        asyncio.run(cleanup(env))


if __name__ == "__main__":
    main()
