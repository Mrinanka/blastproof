"""LangGraph's Postgres checkpointer (D-173 to D-178).

The pool and the saver are built here but opened by the lifespan, so building them touches no
network (TEST-07). Four settings are not optional, each learned in the spike:

* `autocommit=True`: without it the graph runs and stores nothing, silently.
* `check=AsyncConnectionPool.check_connection`: a pool without it keeps handing out connections the
  server has killed.
* `prepare_threshold=0` and `row_factory=dict_row`: what the saver expects of its connections
  (the first matters behind a pooler such as Neon's pooled endpoint).
* strict serde: `allowed_msgpack_modules=None` lets only built-in safe types through, so a dataclass
  that sneaks into the state fails at once instead of being silently trusted later.
"""

from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.db.session import check_app_role


async def _require_app_role(conn: AsyncConnection[Any]) -> None:
    """Refuse a pool whose URL points at the owner or a superuser (SEC-09), as the engine does."""
    cursor = await conn.execute("SELECT current_user")
    row = await cursor.fetchone()
    assert row is not None
    check_app_role(row[0] if not isinstance(row, dict) else row["current_user"])


def make_checkpointer_pool(url: str) -> AsyncConnectionPool[AsyncConnection[Any]]:
    """A closed pool of `blastproof_app` connections: `await pool.open()` starts it."""
    return AsyncConnectionPool(
        url,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        check=AsyncConnectionPool.check_connection,
        configure=_require_app_role,
    )


def make_checkpointer(pool: AsyncConnectionPool[AsyncConnection[Any]]) -> AsyncPostgresSaver:
    return AsyncPostgresSaver(pool, serde=JsonPlusSerializer(allowed_msgpack_modules=None))  # type: ignore[arg-type]
