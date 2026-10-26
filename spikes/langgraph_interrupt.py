"""THROWAWAY SPIKE (SPIKE-04): LangGraph interrupt and resume on Postgres, before backend plan step 14.

Not production code. No imports from backend/app or demo_company. Local Docker Postgres only
(`blastproof-db` from docker-compose.yml): every run makes a scratch database `bp_spike_<hex>` as the
Compose superuser and drops it at the end. It never reads backend/.env and never calls a network service.

Run from the repo root:
  uv run --project backend python spikes/langgraph_interrupt.py probe     # Q1, Q3, Q4, Q5, Q6, Q7
  uv run --project backend python spikes/langgraph_interrupt.py migrate   # Q2 (Alembic)
  uv run --project backend python spikes/langgraph_interrupt.py cleanup   # drop leftover bp_spike_* databases
(`restart-a`, `restart-b` and `kill-run` are internal: the probe starts them as separate processes.)

Docs: https://docs.langchain.com/oss/python/langgraph/interrupts ,
https://docs.langchain.com/oss/python/langgraph/checkpointers ,
https://docs.langchain.com/oss/python/langgraph/persistence ,
https://pypi.org/project/langgraph-checkpoint-postgres/ , https://www.psycopg.org/psycopg3/docs/advanced/pool.html ,
https://alembic.sqlalchemy.org/en/latest/api/runtime.html#alembic.runtime.migration.MigrationContext.autocommit_block
"""

import argparse
import asyncio
import contextlib
import os
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
import warnings
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TypedDict

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

REPO = Path(__file__).resolve().parents[1]
HOST = os.environ.get("SPIKE_PG_HOST", "localhost:5432")
ADMIN_URL = f"postgresql://blastproof:blastproof@{HOST}/blastproof"
CONN_KWARGS: dict[str, Any] = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}
CHECKPOINT_TABLES = ["checkpoints", "checkpoint_blobs", "checkpoint_writes"]

FAILED: list[str] = []


# ---------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------


def say(qid: str, kind: str, fact: str) -> None:
    print(f"{qid} {kind:<4} {fact}", flush=True)


def check(qid: str, ok: bool, fact: str) -> None:
    say(qid, "PASS" if ok else "FAIL", fact)
    if not ok:
        FAILED.append(qid)


def info(qid: str, fact: str) -> None:
    say(qid, "INFO", fact)


def short(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text[:150]}"


async def attempt(coro: Awaitable[Any]) -> tuple[Any, BaseException | None]:
    try:
        return await coro, None
    except BaseException as exc:  # noqa: BLE001  (a spike reports whatever happens)
        if isinstance(exc, KeyboardInterrupt | SystemExit):
            raise
        return None, exc


# ---------------------------------------------------------------------------------------------
# Scratch database (the owner/app setup of docker/blastproof-init/01-roles.sql, per database)
# ---------------------------------------------------------------------------------------------


def url_for(db: str, role: str | None = None) -> str:
    creds = f"{role}:{role}" if role else "blastproof:blastproof"
    return f"postgresql://{creds}@{HOST}/{db}"


def create_scratch() -> str:
    name = f"bp_spike_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name} OWNER blastproof_owner")
        admin.execute(f"REVOKE ALL ON DATABASE {name} FROM PUBLIC")
        admin.execute(f"GRANT CONNECT ON DATABASE {name} TO blastproof_app")
    with psycopg.connect(url_for(name), autocommit=True) as admin:
        admin.execute("ALTER SCHEMA public OWNER TO blastproof_owner")
        admin.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        admin.execute("GRANT USAGE ON SCHEMA public TO blastproof_app")
    return name


def drop_scratch(name: str) -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@contextlib.contextmanager
def scratch_db() -> Any:
    name = create_scratch()
    try:
        yield name
    finally:
        drop_scratch(name)


def cleanup() -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        rows = admin.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE 'bp\\_spike\\_%'"
        ).fetchall()
        for (name,) in rows:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
            print(f"dropped {name}")
    print(f"cleanup: {len(rows)} leftover database(s)")


SPIKE_TABLES_SQL = """
CREATE TABLE spike_runs (action_id text NOT NULL, node text NOT NULL, n int NOT NULL DEFAULT 0,
                         PRIMARY KEY (action_id, node));
CREATE TABLE spike_status (seq serial PRIMARY KEY, action_id text NOT NULL, status text NOT NULL);
CREATE TABLE spike_flags (action_id text PRIMARY KEY, crash boolean NOT NULL DEFAULT false);
GRANT SELECT, INSERT, UPDATE, DELETE ON spike_runs, spike_status, spike_flags TO blastproof_app;
GRANT USAGE ON SEQUENCE spike_status_seq_seq TO blastproof_app;
"""


async def owner_setup(db: str) -> None:
    """As blastproof_owner: spike tables, then the checkpoint tables through the library's own setup()."""
    async with await psycopg.AsyncConnection.connect(
        url_for(db, "blastproof_owner"), autocommit=True
    ) as conn:
        await conn.execute(SPIKE_TABLES_SQL)
    async with AsyncPostgresSaver.from_conn_string(url_for(db, "blastproof_owner")) as saver:
        await saver.setup()
    async with await psycopg.AsyncConnection.connect(
        url_for(db, "blastproof_owner"), autocommit=True
    ) as conn:
        await conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(CHECKPOINT_TABLES)} TO blastproof_app"
        )
        await conn.execute("GRANT SELECT ON checkpoint_migrations TO blastproof_app")


# ---------------------------------------------------------------------------------------------
# Savers: one connection vs a pool
# ---------------------------------------------------------------------------------------------


@asynccontextmanager
async def single_saver(db: str, role: str = "blastproof_app") -> AsyncIterator[AsyncPostgresSaver]:
    async with AsyncPostgresSaver.from_conn_string(url_for(db, role)) as saver:
        yield saver


@asynccontextmanager
async def pool_saver(
    db: str, role: str = "blastproof_app", size: int = 5, check: bool = False
) -> AsyncIterator[AsyncPostgresSaver]:
    pool = AsyncConnectionPool(
        url_for(db, role),
        min_size=1,
        max_size=size,
        kwargs=CONN_KWARGS,
        open=False,
        check=AsyncConnectionPool.check_connection if check else None,
    )
    async with pool:
        yield AsyncPostgresSaver(pool)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------
# The fake review graph. Run counts live in the database, not in memory.
# ---------------------------------------------------------------------------------------------

_DB: dict[str, str] = {}  # the scratch database name, set once per process


class Payload(BaseModel):
    action_id: uuid.UUID
    created: datetime
    cost: Decimal
    rows: int


class State(TypedDict, total=False):
    action_id: str
    status: str
    decision: Any
    payload: Payload
    when: datetime
    price: Decimal
    trail: list[str]


async def app_conn() -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(
        url_for(_DB["name"], "blastproof_app"), autocommit=True
    )


async def bump(action_id: str, node: str) -> None:
    async with await app_conn() as conn:
        await conn.execute(
            "INSERT INTO spike_runs (action_id, node, n) VALUES (%s, %s, 1) "
            "ON CONFLICT (action_id, node) DO UPDATE SET n = spike_runs.n + 1",
            (action_id, node),
        )


async def set_status(action_id: str, status: str) -> None:
    async with await app_conn() as conn:
        await conn.execute(
            "INSERT INTO spike_status (action_id, status) VALUES (%s, %s)", (action_id, status)
        )


async def runs(action_id: str) -> dict[str, int]:
    async with await app_conn() as conn:
        rows = await (
            await conn.execute("SELECT node, n FROM spike_runs WHERE action_id = %s", (action_id,))
        ).fetchall()
    return {node: n for node, n in rows}


async def history(action_id: str) -> list[str]:
    async with await app_conn() as conn:
        rows = await (
            await conn.execute(
                "SELECT status FROM spike_status WHERE action_id = %s ORDER BY seq", (action_id,)
            )
        ).fetchall()
    return [s for (s,) in rows]


async def crash_flag(action_id: str) -> bool:
    async with await app_conn() as conn:
        row = await (
            await conn.execute("SELECT crash FROM spike_flags WHERE action_id = %s", (action_id,))
        ).fetchone()
    return bool(row and row[0])


async def set_crash(action_id: str, value: bool) -> None:
    async with await app_conn() as conn:
        await conn.execute(
            "INSERT INTO spike_flags (action_id, crash) VALUES (%s, %s) "
            "ON CONFLICT (action_id) DO UPDATE SET crash = EXCLUDED.crash",
            (action_id, value),
        )


async def classify(state: State) -> State:
    await bump(state["action_id"], "classify")
    return {
        "status": "classified",
        "payload": Payload(
            action_id=uuid.UUID(state["action_id"]),
            created=datetime.now(UTC),
            cost=Decimal("0.0123"),
            rows=48213,
        ),
        "when": datetime.now(UTC),
        "price": Decimal("1.50"),
    }


async def request_review(state: State) -> State:
    await bump(state["action_id"], "request_review")
    await set_status(state["action_id"], "awaiting_review")
    return {"status": "awaiting_review"}


async def human_review(state: State) -> State:
    await bump(state["action_id"], "human_review")
    decision = interrupt({"action_id": state["action_id"], "ask": "approve?"})
    return {"decision": decision}


async def review_in_one_node(state: State) -> State:
    """The counter-example: the status write sits in the same node as interrupt()."""
    await bump(state["action_id"], "review_in_one_node")
    await set_status(state["action_id"], "awaiting_review")
    decision = interrupt({"action_id": state["action_id"]})
    return {"decision": decision}


async def finish(state: State) -> State:
    await bump(state["action_id"], "finish")
    if await crash_flag(state["action_id"]):
        raise RuntimeError("simulated crash after the review")
    await set_status(state["action_id"], "approved")
    return {"status": "approved"}


def build_graph(saver: Any, split: bool = True) -> Any:
    g = StateGraph(State)
    g.add_node("classify", classify)
    g.add_edge(START, "classify")
    if split:
        g.add_node("request_review", request_review)
        g.add_node("human_review", human_review)
        g.add_node("finish", finish)
        g.add_edge("classify", "request_review")
        g.add_edge("request_review", "human_review")
        g.add_edge("human_review", "finish")
    else:
        g.add_node("review_in_one_node", review_in_one_node)
        g.add_node("finish", finish)
        g.add_edge("classify", "review_in_one_node")
        g.add_edge("review_in_one_node", "finish")
    g.add_edge("finish", END)
    return g.compile(checkpointer=saver)


def cfg(thread: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread}}


def new_id() -> str:
    return str(uuid.uuid4())


async def start(graph: Any, action_id: str, **kw: Any) -> Any:
    return await graph.ainvoke({"action_id": action_id}, cfg(action_id), version="v2", **kw)


# ---------------------------------------------------------------------------------------------
# Q1: connection setup
# ---------------------------------------------------------------------------------------------


async def q1(db: str) -> None:
    _DB["name"] = db
    # Plain postgresql:// URL, app role, from_conn_string (autocommit, dict_row, prepare_threshold=0 inside).
    async with single_saver(db) as saver:
        graph = build_graph(saver)
        aid = new_id()
        out = await start(graph, aid)
        check(
            "Q1.1",
            bool(out.interrupts),
            "as blastproof_app, from_conn_string(plain URL) runs a graph to its interrupt",
        )

    # Without autocommit: a plain psycopg connection handed to the saver.
    conn = await psycopg.AsyncConnection.connect(
        url_for(db, "blastproof_app"), row_factory=dict_row, prepare_threshold=0
    )
    try:
        graph = build_graph(AsyncPostgresSaver(conn))
        aid = new_id()
        _, err = await attempt(start(graph, aid))
        _, err2 = await attempt(conn.execute("SELECT 1"))
        info(
            "Q1.2",
            f"no autocommit: graph run -> {short(err) if err else 'worked'}; connection afterwards -> {short(err2) if err2 else 'usable'}",
        )
        n = (
            await (
                await (await app_conn()).execute(
                    "SELECT count(*) FROM checkpoints WHERE thread_id = %s", (aid,)
                )
            ).fetchone()
        )[0]
        info(
            "Q1.2b",
            f"checkpoints rows persisted for that thread without autocommit: {n} (no explicit commit was issued)",
        )
    finally:
        await conn.close()

    # Without dict_row, with autocommit.
    conn = await psycopg.AsyncConnection.connect(
        url_for(db, "blastproof_app"), autocommit=True, prepare_threshold=0
    )
    try:
        graph = build_graph(AsyncPostgresSaver(conn))
        aid = new_id()
        out, err = await attempt(start(graph, aid))
        info(
            "Q1.3",
            f"no dict_row (autocommit on): {short(err) if err else 'worked, interrupts=' + str(len(out.interrupts))}",
        )
    finally:
        await conn.close()

    # Without prepare_threshold=0 (default 5) on a single connection: works locally (no pgbouncer).
    conn = await psycopg.AsyncConnection.connect(
        url_for(db, "blastproof_app"), autocommit=True, row_factory=dict_row
    )
    try:
        graph = build_graph(AsyncPostgresSaver(conn))
        out, err = await attempt(start(graph, new_id()))
        info(
            "Q1.4",
            f"default prepare_threshold (5), direct connection: {short(err) if err else 'worked'}",
        )
    finally:
        await conn.close()

    # Five actions in parallel: single connection vs pool.
    for kind, maker in (("single", single_saver), ("pool", pool_saver)):
        async with maker(db) as saver:
            graph = build_graph(saver)
            ids = [new_id() for _ in range(5)]
            t0 = time.perf_counter()
            results = await asyncio.gather(*(start(graph, i) for i in ids), return_exceptions=True)
            dt = time.perf_counter() - t0
            good = sum(1 for r in results if not isinstance(r, BaseException) and r.interrupts)
            check(
                f"Q1.5-{kind}",
                good == 5,
                f"5 parallel actions on one {kind} saver all paused ({dt:.2f} s)",
            )

    # Server kills the connection(s).
    for kind, maker in (
        ("single", single_saver),
        ("pool", pool_saver),
        ("pool+check", lambda d: pool_saver(d, check=True)),
    ):
        async with maker(db) as saver:
            graph = build_graph(saver)
            await start(graph, new_id())  # warm
            with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND usename = 'blastproof_app' AND pid <> pg_backend_pid() "
                    "AND application_name = ''",
                    (db,),
                )
            aid = new_id()
            out, err = await attempt(start(graph, aid))
            first = short(err) if err else "worked"
            out2, err2 = await attempt(start(graph, new_id()))
            second = short(err2) if err2 else "worked"
            info(
                "Q1.6", f"{kind}, all app sessions killed: 1st run -> {first}; 2nd run -> {second}"
            )


# ---------------------------------------------------------------------------------------------
# Q3: privileges
# ---------------------------------------------------------------------------------------------


async def q3(db: str) -> None:
    _DB["name"] = db
    async with single_saver(db) as saver:
        graph = build_graph(saver)
        aid = new_id()
        await start(graph, aid)
        await graph.ainvoke(Command(resume={"approve": True}), cfg(aid), version="v2")
        check(
            "Q3.1",
            True,
            "aput, aput_writes and aget_tuple (inside ainvoke) work as blastproof_app with SELECT/INSERT/UPDATE/DELETE only",
        )
        listed = [c async for c in saver.alist(cfg(aid))]
        check(
            "Q3.2",
            len(listed) > 0,
            f"alist works ({len(listed)} checkpoints for one finished action)",
        )
        _, err = await attempt(saver.adelete_thread(aid))
        check("Q3.3", err is None, f"adelete_thread works ({short(err) if err else 'ok'})")
        left = [c async for c in saver.alist(cfg(aid))]
        check("Q3.3b", len(left) == 0, "nothing left for that thread after adelete_thread")
        _, err = await attempt(saver.setup())
        check(
            "Q3.4",
            err is not None,
            f"setup() as blastproof_app is refused: {short(err) if err else 'NOT refused'}",
        )
    async with await app_conn() as conn:
        _, err = await attempt(conn.execute("CREATE TEMP TABLE t (x int)"))
        check(
            "Q3.5",
            err is not None,
            f"app has no TEMP right: {short(err) if err else 'NOT refused'}",
        )
        for sql in (
            "DROP TABLE checkpoints",
            "TRUNCATE checkpoints",
            "ALTER TABLE checkpoints ADD COLUMN x int",
        ):
            _, err = await attempt(conn.execute(sql))
            check(
                "Q3.6",
                err is not None,
                f"app refused `{sql}`: {short(err) if err else 'NOT refused'}",
            )
        rows = await (
            await conn.execute(
                "SELECT table_name, string_agg(privilege_type, ',' ORDER BY privilege_type) FROM information_schema.role_table_grants "
                "WHERE grantee = 'blastproof_app' AND table_name LIKE 'checkpoint%' GROUP BY table_name ORDER BY table_name"
            )
        ).fetchall()
        info("Q3.7", f"app grants on checkpoint tables: {dict(rows)}")

    # ALTER DEFAULT PRIVILEGES FOR ROLE blastproof_owner leaks to future tables.
    async with await psycopg.AsyncConnection.connect(
        url_for(db, "blastproof_owner"), autocommit=True
    ) as owner:
        await owner.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE blastproof_owner IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO blastproof_app"
        )
        await owner.execute("CREATE TABLE checkpoint_future (x int)")
        await owner.execute("CREATE TABLE audit_like (x int)")
        got = await (
            await owner.execute(
                "SELECT t, has_table_privilege('blastproof_app', t, 'UPDATE'), has_table_privilege('blastproof_app', t, 'DELETE') "
                "FROM unnest(ARRAY['checkpoint_future', 'audit_like']) AS t"
            )
        ).fetchall()
    for t, can_update, can_delete in got:
        info(
            "Q3.8",
            f"after ALTER DEFAULT PRIVILEGES, a new table `{t}` gives app UPDATE={can_update} DELETE={can_delete}",
        )
    check(
        "Q3.8b",
        all(u and d for _, u, d in got),
        "ALTER DEFAULT PRIVILEGES also grants UPDATE/DELETE on a later non-checkpoint table (the plan should drop it)",
    )


# ---------------------------------------------------------------------------------------------
# Q4: interrupt semantics
# ---------------------------------------------------------------------------------------------


async def q4(db: str) -> None:
    _DB["name"] = db
    async with single_saver(db) as saver:
        graph = build_graph(saver, split=True)
        aid = new_id()
        out = await start(graph, aid)
        r1 = await runs(aid)
        info(
            "Q4.1",
            f"after the first ainvoke: interrupts={[i.value for i in out.interrupts]} runs={r1}",
        )
        check(
            "Q4.2",
            r1 == {"classify": 1, "request_review": 1, "human_review": 1},
            "each node ran once up to the interrupt",
        )
        snap = await graph.aget_state(cfg(aid))
        check(
            "Q4.3",
            snap.next == ("human_review",),
            f"aget_state().next == ('human_review',): {snap.next}",
        )
        check(
            "Q4.3b",
            len(snap.interrupts) == 1,
            f"aget_state().interrupts has the pending interrupt: {len(snap.interrupts)}",
        )
        out2 = await graph.ainvoke(
            Command(resume={"approve": True, "note": "ok"}), cfg(aid), version="v2"
        )
        r2 = await runs(aid)
        info(
            "Q4.4",
            f"after Command(resume): value.status={out2.value.get('status')!r} decision={out2.value.get('decision')} runs={r2}",
        )
        check(
            "Q4.5",
            r2.get("request_review") == 1 and r2.get("classify") == 1,
            "classify and request_review did NOT re-run",
        )
        check(
            "Q4.6", r2.get("human_review") == 2, "human_review DID re-run from its start (2 runs)"
        )
        check(
            "Q4.7",
            out2.value.get("status") == "approved" and not out2.interrupts,
            "final status approved, no pending interrupt",
        )
        h = await history(aid)
        check("Q4.8", h == ["awaiting_review", "approved"], f"status history in the database: {h}")
        check(
            "Q4.8b",
            out2.value["decision"] == {"approve": True, "note": "ok"},
            "resume value (a dict with a note) arrives intact",
        )

        # Counter-example: the status write in the same node as interrupt().
        graph1 = build_graph(saver, split=False)
        bid = new_id()
        await start(graph1, bid)
        await graph1.ainvoke(Command(resume=True), cfg(bid), version="v2")
        rb = await runs(bid)
        hb = await history(bid)
        check(
            "Q4.9",
            rb.get("review_in_one_node") == 2,
            f"one-node version: the node ran twice (runs={rb})",
        )
        check(
            "Q4.10",
            hb == ["awaiting_review", "awaiting_review", "approved"],
            f"one-node version: status written twice, history {hb}",
        )


# ---------------------------------------------------------------------------------------------
# Q5: restart across processes, D-029 cases
# ---------------------------------------------------------------------------------------------


async def restart_a(db: str, thread: str) -> None:
    _DB["name"] = db
    async with single_saver(db) as saver:
        out = await start(build_graph(saver), thread)
        print(f"A paused interrupts={len(out.interrupts)}", flush=True)


async def restart_b(db: str, thread: str) -> None:
    _DB["name"] = db
    async with single_saver(db) as saver:
        graph = build_graph(saver)
        snap = await graph.aget_state(cfg(thread))
        print(
            f"B next={snap.next} interrupts={len(snap.interrupts)} status={snap.values.get('status')}",
            flush=True,
        )
        out = await graph.ainvoke(Command(resume={"approve": True}), cfg(thread), version="v2")
        print(
            f"B resumed status={out.value.get('status')} interrupts={len(out.interrupts)}",
            flush=True,
        )


def run_child(*args: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    out = proc.stdout.strip().splitlines()
    return (
        f"exit={proc.returncode} "
        + " | ".join(out[-3:])
        + (" | stderr: " + proc.stderr.strip()[-200:] if proc.returncode and proc.stderr else "")
    )


async def q5(db: str) -> None:
    _DB["name"] = db
    thread = new_id()
    a = await asyncio.to_thread(run_child, "restart-a", "--db", db, "--thread", thread)
    info("Q5.1", f"process A: {a}")
    b = await asyncio.to_thread(run_child, "restart-b", "--db", db, "--thread", thread)
    info("Q5.2", f"process B (new saver, new graph object): {b}")
    check(
        "Q5.3",
        "next=('human_review',)" in b and "interrupts=1" in b and "resumed status=approved" in b,
        "a second process sees the pause and resumes it",
    )
    r = await runs(thread)
    check(
        "Q5.3b",
        r.get("request_review") == 1 and r.get("human_review") == 2,
        f"run counts across the two processes: {r}",
    )

    async with single_saver(db) as saver:
        graph = build_graph(saver)
        # D-029 case 1: past the interrupt, a node after it crashes; ainvoke(None) continues.
        aid = new_id()
        await set_crash(aid, True)
        await start(graph, aid)
        _, err = await attempt(
            graph.ainvoke(Command(resume={"approve": True}), cfg(aid), version="v2")
        )
        info("Q5.4", f"resume with a crashing `finish`: {short(err) if err else 'no error'}")
        snap = await graph.aget_state(cfg(aid))
        errs = [t.error for t in snap.tasks]
        check(
            "Q5.5",
            snap.next == ("finish",),
            f"after the crash, aget_state().next == ('finish',): {snap.next}",
        )
        info(
            "Q5.5b",
            f"tasks[].error after the crash: {[str(e)[:60] if e else e for e in errs]}; pending interrupts={len(snap.interrupts)}",
        )
        before = await runs(aid)
        await set_crash(aid, False)
        out, err = await attempt(graph.ainvoke(None, cfg(aid), version="v2"))
        check(
            "Q5.6",
            err is None and out.value.get("status") == "approved",
            f"ainvoke(None, config) continues from the checkpoint: {short(err) if err else out.value.get('status')}",
        )
        after = await runs(aid)
        check(
            "Q5.6b",
            after.get("human_review") == before.get("human_review"),
            f"human_review not re-run by the continue (before {before.get('human_review')}, after {after.get('human_review')})",
        )
        # Calling the interrupted-thread API on a crashed thread
        # Unknown thread.
        snap = await graph.aget_state(cfg(new_id()))
        info(
            "Q5.7",
            f"unknown thread: values={snap.values!r} next={snap.next!r} tasks={snap.tasks!r} interrupts={snap.interrupts!r} config={'checkpoint_id' in snap.config.get('configurable', {})}",
        )
        check(
            "Q5.7b",
            snap.values == {} and snap.next == (),
            "unknown thread -> empty snapshot (values == {}, next == ())",
        )
        # Resume on a finished thread and on a never-paused thread.
        before = await runs(aid)
        out, err = await attempt(
            graph.ainvoke(Command(resume={"approve": True}), cfg(aid), version="v2")
        )
        info(
            "Q5.8",
            f"Command(resume) on a FINISHED thread: {short(err) if err else 'no error, status=' + str(out.value.get('status'))}; runs unchanged={await runs(aid) == before}",
        )
        out, err = await attempt(
            graph.ainvoke(Command(resume={"approve": True}), cfg(new_id()), version="v2")
        )
        info(
            "Q5.9",
            f"Command(resume) on an UNKNOWN thread: {short(err) if err else 'no error, value=' + repr(out.value)}",
        )
        out, err = await attempt(graph.ainvoke(None, cfg(aid), version="v2"))
        info(
            "Q5.10",
            f"ainvoke(None) on a FINISHED thread: {short(err) if err else 'no error, status=' + str(out.value.get('status'))}",
        )
        out, err = await attempt(graph.ainvoke(None, cfg(new_id()), version="v2"))
        info(
            "Q5.11",
            f"ainvoke(None) on an UNKNOWN thread: {short(err) if err else 'no error, value=' + repr(out.value)}",
        )


# ---------------------------------------------------------------------------------------------
# Q6: edge cases
# ---------------------------------------------------------------------------------------------


async def kill_run(db: str, mode: str, thread: str) -> None:
    """Child process: two nodes; the second ends the process hard (os._exit) as soon as it starts."""
    _DB["name"] = db

    async def n1(state: State) -> State:
        return {"status": "n1 done"}

    async def n2(state: State) -> State:
        os._exit(1)

    g = StateGraph(State)
    g.add_node("n1", n1)
    g.add_node("n2", n2)
    g.add_edge(START, "n1")
    g.add_edge("n1", "n2")
    g.add_edge("n2", END)
    async with single_saver(db) as saver:
        await g.compile(checkpointer=saver).ainvoke(
            {"action_id": thread}, cfg(thread), durability=mode
        )


async def q6(db: str) -> None:
    _DB["name"] = db
    async with pool_saver(db) as saver:
        graph = build_graph(saver)
        # Two concurrent resumes of one paused thread.
        aid = new_id()
        await start(graph, aid)
        graph2 = build_graph(saver)
        results = await asyncio.gather(
            graph.ainvoke(Command(resume={"approve": True, "who": "a"}), cfg(aid), version="v2"),
            graph2.ainvoke(Command(resume={"approve": False, "who": "b"}), cfg(aid), version="v2"),
            return_exceptions=True,
        )
        r = await runs(aid)
        h = await history(aid)
        errs = [short(x) for x in results if isinstance(x, BaseException)]
        info("Q6.1", f"two concurrent Command(resume): errors={errs}; runs={r}; status history={h}")
        snap = await graph.aget_state(cfg(aid))
        info("Q6.1b", f"final decision stored: {snap.values.get('decision')}")
        check(
            "Q6.2",
            r.get("finish", 0) >= 2 or r.get("human_review", 0) >= 3,
            f"LangGraph does NOT stop a double resume (finish ran {r.get('finish')}x): step 15's SQL claim is the guard",
        )

        # Serializer round trip.
        aid = new_id()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            await start(graph, aid)
            snap = await graph.aget_state(cfg(aid))
        v = snap.values
        ok = (
            isinstance(v["payload"], Payload)
            and isinstance(v["payload"].action_id, uuid.UUID)
            and isinstance(v["payload"].created, datetime)
            and isinstance(v["payload"].cost, Decimal)
            and isinstance(v["when"], datetime)
            and v["price"] == Decimal("1.50")
            and isinstance(v["price"], Decimal)
        )
        check(
            "Q6.3",
            ok,
            f"Pydantic model / UUID / datetime / Decimal round-trip with their types: {type(v['payload']).__name__}, {type(v['price']).__name__}",
        )
        info(
            "Q6.3b",
            f"warnings during the round trip: {[str(w.message)[:100] for w in caught] or 'none'}",
        )

        # Exception in a node.
        aid = new_id()
        await set_crash(aid, True)
        await start(graph, aid)
        _, err = await attempt(graph.ainvoke(Command(resume=True), cfg(aid), version="v2"))
        snap = await graph.aget_state(cfg(aid))
        info(
            "Q6.5",
            f"exception in a node: raised {short(err) if err else None}; next={snap.next}; tasks={[(t.name, str(t.error)[:40]) for t in snap.tasks]}; status={snap.values.get('status')!r}",
        )

    # Durability under a hard kill.
    for mode in ("async", "sync", "exit"):
        lost = 0
        trials = 3
        seen: list[str] = []
        for _ in range(trials):
            thread = new_id()
            res = await asyncio.to_thread(
                run_child, "kill-run", "--db", db, "--mode", mode, "--thread", thread
            )
            async with single_saver(db) as saver:
                g = StateGraph(State)
                g.add_node("n1", lambda s: s)
                g.add_node("n2", lambda s: s)
                g.add_edge(START, "n1")
                g.add_edge("n1", "n2")
                g.add_edge("n2", END)
                snap = await g.compile(checkpointer=saver).aget_state(cfg(thread))
            seen.append(f"next={snap.next} status={snap.values.get('status')!r}")
            if snap.values.get("status") != "n1 done":
                lost += 1
        info(
            "Q6.6",
            f"durability={mode!r}, process killed at the start of node 2, {trials} trials: n1's result lost {lost}x; {seen}; child {res[:30]}",
        )


# ---------------------------------------------------------------------------------------------
# Q7: timing
# ---------------------------------------------------------------------------------------------


async def q7(db: str) -> None:
    _DB["name"] = db

    async def a(state: State) -> State:
        return {"status": "a"}

    def plain(saver: Any) -> Any:
        g = StateGraph(State)
        for n in ("n1", "n2", "n3"):
            g.add_node(n, a)
        g.add_edge(START, "n1")
        g.add_edge("n1", "n2")
        g.add_edge("n2", "n3")
        g.add_edge("n3", END)
        return g.compile(checkpointer=saver)

    async def timed(graph: Any, n: int, **kw: Any) -> float:
        ts = []
        for _ in range(n):
            t0 = time.perf_counter()
            await graph.ainvoke({"action_id": "x"}, cfg(new_id()), **kw)
            ts.append((time.perf_counter() - t0) * 1000)
        return statistics.median(ts)

    base = await timed(plain(None), 30) if False else None
    g0 = StateGraph(State)
    for n in ("n1", "n2", "n3"):
        g0.add_node(n, a)
    g0.add_edge(START, "n1")
    g0.add_edge("n1", "n2")
    g0.add_edge("n2", "n3")
    g0.add_edge("n3", END)
    ts = []
    comp = g0.compile()
    for _ in range(30):
        t0 = time.perf_counter()
        await comp.ainvoke({"action_id": "x"})
        ts.append((time.perf_counter() - t0) * 1000)
    base = statistics.median(ts)
    async with single_saver(db) as s1:
        single = {m: await timed(plain(s1), 30, durability=m) for m in ("async", "sync")}
    async with pool_saver(db) as s2:
        pooled = {m: await timed(plain(s2), 30, durability=m) for m in ("async", "sync")}
    info(
        "Q7.1",
        f"3-node graph, median of 30 runs, no DB work in nodes: no checkpointer {base:.1f} ms; single {single}; pool {pooled} (ms)",
    )


# ---------------------------------------------------------------------------------------------
# Q2: migrate (Alembic)
# ---------------------------------------------------------------------------------------------

ENV_PY = """
import os
from alembic import context
from sqlalchemy import MetaData, create_engine, pool

config = context.config


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name.startswith("checkpoint"))


engine = create_engine(config.attributes["url"], poolclass=pool.NullPool)
with engine.connect() as connection:
    kw = {"include_object": include_object} if os.environ.get("SPIKE_FILTER") == "1" else {}
    context.configure(connection=connection, target_metadata=MetaData(), **kw)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
"""

REVISION_PY = """
import os
import psycopg
from alembic import op
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.base import BasePostgresSaver

revision = "0001"
down_revision = None
APPROACH = os.environ["SPIKE_APPROACH"]
OWNER_URL = os.environ["SPIKE_OWNER_URL"]


def upgrade():
    if APPROACH.startswith("naive"):
        # `setup()` on a second autocommit connection while this migration's transaction is open.
        if APPROACH == "naive-write":
            op.execute("CREATE TABLE before_checkpoint (x int)")  # the transaction now holds an xid
        else:
            op.execute("SELECT 1")
        with psycopg.connect(OWNER_URL, autocommit=True, options="-c lock_timeout=10000 -c statement_timeout=10000") as conn:
            PostgresSaver(conn).setup()
    elif APPROACH == "a":
        with op.get_context().autocommit_block():
            raw = op.get_bind().connection.driver_connection
            PostgresSaver(raw).setup()
    elif APPROACH == "b":
        for i, sql in enumerate(BasePostgresSaver.MIGRATIONS):
            op.execute(sql.replace("CONCURRENTLY ", ""))
            op.execute(
                f"INSERT INTO checkpoint_migrations (v) VALUES ({i}) ON CONFLICT DO NOTHING"
            ) if i > 0 else op.execute("INSERT INTO checkpoint_migrations (v) VALUES (0) ON CONFLICT DO NOTHING")
        op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON checkpoints, checkpoint_blobs, checkpoint_writes TO blastproof_app")
        op.execute("GRANT SELECT ON checkpoint_migrations TO blastproof_app")
    op.execute("CREATE TABLE after_checkpoint (x int)")  # a later statement in the same revision
    if os.environ.get("SPIKE_FAIL") == "1":
        op.execute("SELECT 1/0")


def downgrade():
    op.execute("DROP TABLE IF EXISTS after_checkpoint")
    for t in ("checkpoint_writes", "checkpoint_blobs", "checkpoints", "checkpoint_migrations"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
"""


def alembic_cfg(root: Path, url: str) -> Any:
    from alembic.config import Config

    (root / "versions").mkdir(parents=True, exist_ok=True)
    (root / "env.py").write_text(ENV_PY, encoding="utf-8")
    (root / "versions" / "0001_checkpoint.py").write_text(REVISION_PY, encoding="utf-8")
    c = Config()
    c.set_main_option("script_location", str(root))
    c.attributes["url"] = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return c


def table_names(db: str) -> list[str]:
    with psycopg.connect(url_for(db), autocommit=True) as conn:
        return [
            r[0]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"
            ).fetchall()
        ]


def migrate() -> None:
    from alembic import command
    from alembic.util import CommandError

    for approach in ("a", "b"):  # atomicity: a failing statement after the checkpoint DDL
        with scratch_db() as db, tempfile.TemporaryDirectory() as tmp:
            owner = url_for(db, "blastproof_owner")
            os.environ.update(SPIKE_APPROACH=approach, SPIKE_OWNER_URL=owner, SPIKE_FAIL="1")
            c = alembic_cfg(Path(tmp), owner)
            try:
                command.upgrade(c, "head")
                err = None
            except BaseException as exc:  # noqa: BLE001
                err = exc
            left = [t for t in table_names(db) if t.startswith(("checkpoint", "after"))]
            info(
                f"Q2.{approach}-atomic",
                f"revision fails after the checkpoint DDL ({short(err) if err else 'NO ERROR'}); checkpoint/after tables left: {left}",
            )
            if approach == "b":
                check(
                    f"Q2.{approach}-atomic",
                    left == [],
                    "approach b leaves nothing half-built after a failure",
                )
            else:
                info(f"Q2.{approach}-atomic", f"approach a is NOT atomic: left behind {left}")
    os.environ.pop("SPIKE_FAIL", None)
    for approach in ("naive", "naive-write", "a", "b"):
        with scratch_db() as db, tempfile.TemporaryDirectory() as tmp:
            owner = url_for(db, "blastproof_owner")
            os.environ["SPIKE_APPROACH"] = approach
            os.environ["SPIKE_OWNER_URL"] = owner
            os.environ.pop("SPIKE_FILTER", None)
            c = alembic_cfg(Path(tmp), owner)
            qid = f"Q2.{approach}"
            t0 = time.perf_counter()
            try:
                command.upgrade(c, "head")
                err: BaseException | None = None
            except BaseException as exc:  # noqa: BLE001
                err = exc
            dt = time.perf_counter() - t0
            tables = table_names(db)
            if approach.startswith("naive"):
                info(
                    qid,
                    f"setup() on a second connection inside the one-transaction migration: {dt:.1f} s (guard 10 s): {short(err) if err else 'no error'}; tables left: {tables}",
                )
                continue
            check(
                qid,
                err is None,
                f"upgrade head with approach {approach}: {short(err) if err else 'ok'} ({dt:.1f} s); tables={tables}",
            )
            if err:
                continue
            with psycopg.connect(url_for(db), autocommit=True) as conn:
                versions = [
                    r[0]
                    for r in conn.execute(
                        "SELECT v FROM checkpoint_migrations ORDER BY v"
                    ).fetchall()
                ]
                idx = [
                    r[0]
                    for r in conn.execute(
                        "SELECT indexname FROM pg_indexes WHERE tablename LIKE 'checkpoint%' AND indexname LIKE '%thread_id_idx' ORDER BY 1"
                    ).fetchall()
                ]
                cols = [
                    r[0]
                    for r in conn.execute(
                        "SELECT column_name FROM information_schema.columns WHERE table_name = 'checkpoint_writes' AND column_name = 'task_path'"
                    ).fetchall()
                ]
                invalid = conn.execute(
                    "SELECT count(*) FROM pg_index WHERE NOT indisvalid"
                ).fetchone()[0]
            info(
                qid,
                f"checkpoint_migrations={versions} indexes={idx} task_path={bool(cols)} invalid_indexes={invalid}",
            )
            # A later setup() as the owner must be a no-op.
            with psycopg.connect(owner, autocommit=True) as conn:
                _, e2 = None, None
                try:
                    PostgresSaver(conn).setup()
                except BaseException as exc:  # noqa: BLE001
                    e2 = exc
                n = conn.execute("SELECT count(*) FROM checkpoint_migrations").fetchone()[0]
            check(
                f"{qid}-setup",
                e2 is None and n == len(versions),
                f"setup() afterwards is a no-op (rows {len(versions)} -> {n}; {short(e2) if e2 else 'no error'})",
            )
            # Atomic: a failing statement after setup in the same revision rolled back?  (not tested separately; see notes)
            # The migration really is one transaction only if the table rows vanish on rollback: tested via a failing re-run.
            # Downgrade, then upgrade again.
            command.downgrade(c, "base")
            check(
                f"{qid}-down",
                [t for t in table_names(db) if t.startswith("checkpoint")] == [],
                f"downgrade drops all four tables cleanly; left: {table_names(db)}",
            )
            _, e3 = None, None
            try:
                command.upgrade(c, "head")
            except BaseException as exc:  # noqa: BLE001
                e3 = exc
            check(
                f"{qid}-reup",
                e3 is None,
                f"upgrade again after downgrade: {short(e3) if e3 else 'ok'}",
            )
            with psycopg.connect(url_for(db), autocommit=True) as conn:
                conn.execute(
                    "DROP TABLE after_checkpoint"
                )  # the spike's own marker table is not under test
            # alembic check against empty metadata, with and without include_object.
            for flt in (False, True):
                if flt:
                    os.environ["SPIKE_FILTER"] = "1"
                else:
                    os.environ.pop("SPIKE_FILTER", None)
                try:
                    command.check(c)
                    res = "clean"
                except CommandError as exc:
                    res = short(exc)
                except BaseException as exc:  # noqa: BLE001
                    res = short(exc)
                if approach == "b":
                    info(
                        f"{qid}-check",
                        f"alembic check (empty metadata) include_object filter={flt}: {res}",
                    )
                    if flt:
                        check(
                            f"{qid}-check",
                            res == "clean",
                            "with the include_object filter alembic check is clean",
                        )
            os.environ.pop("SPIKE_FILTER", None)
            # Privileges of approach b as the app
            if approach == "b":
                import asyncio as _a

                async def app_run() -> None:
                    _DB["name"] = db
                    async with single_saver(db) as saver:
                        graph_state = await saver.aget_tuple(cfg("nope"))
                        info(
                            f"{qid}-app",
                            f"app role can read checkpoints after the migration's own grants: aget_tuple on empty -> {graph_state}",
                        )

                _a.run(app_run())


# ---------------------------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------------------------


async def probe() -> None:
    with scratch_db() as db:
        info("setup", f"scratch database {db}; versions: see spikes/langgraph_interrupt.md")
        await owner_setup(db)
        for name, fn in (("Q1", q1), ("Q3", q3), ("Q4", q4), ("Q5", q5), ("Q6", q6), ("Q7", q7)):
            print(f"--- {name} ---", flush=True)
            try:
                await fn(db)
            except BaseException as exc:  # noqa: BLE001
                if isinstance(exc, KeyboardInterrupt):
                    raise
                check(f"{name}.crash", False, f"section crashed: {short(exc)}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "cmd", choices=["probe", "migrate", "cleanup", "restart-a", "restart-b", "kill-run"]
    )
    p.add_argument("--db")
    p.add_argument("--thread")
    p.add_argument("--mode", default="async")
    a = p.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(
            asyncio.WindowsSelectorEventLoopPolicy()
        )  # psycopg async needs it on Windows
    if a.cmd == "cleanup":
        cleanup()
        return
    if a.cmd == "migrate":
        migrate()
    elif a.cmd == "probe":
        asyncio.run(probe())
    elif a.cmd == "restart-a":
        asyncio.run(restart_a(a.db, a.thread))
    elif a.cmd == "restart-b":
        asyncio.run(restart_b(a.db, a.thread))
    elif a.cmd == "kill-run":
        asyncio.run(kill_run(a.db, a.mode, a.thread))
    if a.cmd in ("probe", "migrate"):
        print(f"\n{'FAILED: ' + ', '.join(FAILED) if FAILED else 'all expected checks passed'}")
        sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
