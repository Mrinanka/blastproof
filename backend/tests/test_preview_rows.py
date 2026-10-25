"""Step 10: saving a diff as preview_rows and the action's impact (one Blastproof transaction)."""

import json
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.actions import create_action
from app.db.preview import save_preview
from app.db.session import make_engine, make_sessionmaker
from app.services.company_schema import ForeignKey
from app.services.shadow import CascadePath, Diff, RowChange, TableDiff
from tests.conftest import AuthDb, asyncpg_url

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]


def diff(*changes: RowChange, deleted: int = 0, cascades: tuple[CascadePath, ...] = ()) -> Diff:
    tables = {"users": TableDiff("users", deleted=deleted, rows_before=10)} if deleted else {}
    return Diff(
        tables=tables,
        derived_groups={"users.paying": 1},
        cascade_expectations=cascades,
        changes=changes,
    )


async def make_action(auth_db: AuthDb, session: AsyncSession) -> uuid.UUID:
    user = auth_db.make_user()
    agent = auth_db.make_agent(user.id)
    [(system_id,)] = auth_db.execute(
        "INSERT INTO systems (name, kind, connection_profile)"
        " VALUES (%s, 'postgres', 'neon') RETURNING id",
        (f"company-{uuid.uuid4().hex[:8]}",),
    )
    action = await create_action(
        session, user_id=user.id, agent_id=agent.id, system_id=system_id, tool="query_database"
    )
    await session.commit()
    return action.id


async def test_rows_are_copied_and_a_second_save_replaces_the_first(auth_db: AuthDb) -> None:
    engine = make_engine(asyncpg_url(auth_db.app_url))
    path = CascadePath(
        "users", (ForeignKey("orders", ("user_id",), "users", ("id",), "cascade"),), "cascade", 3
    )
    try:
        async with make_sessionmaker(engine)() as session:
            action_id = await make_action(auth_db, session)
            first = diff(
                RowChange("users", {"id": 1}, "h1", "deleted"),
                RowChange("orders", {"id": 2}, 'h, with "quotes"', "updated"),
                RowChange("orders", {"id": 3}, None, "inserted"),
                deleted=1,
                cascades=(path,),
            )
            await save_preview(session, action_id, first, 1234)
            await session.commit()
            rows = auth_db.execute(
                "SELECT table_name, primary_key, before_hash, change FROM preview_rows"
                " WHERE action_id = %s ORDER BY table_name, primary_key->>'id'",
                (action_id,),
            )
            assert rows == [
                ("orders", {"id": 2}, 'h, with "quotes"', "updated"),
                ("orders", {"id": 3}, None, "inserted"),
                ("users", {"id": 1}, "h1", "deleted"),
            ]
            [(impact, cascades, ms)] = auth_db.execute(
                "SELECT impact, cascade_expectations, preview_ms FROM actions WHERE id = %s",
                (action_id,),
            )
            assert impact["table_changes"] == {"users": {"deleted": 1, "updated": 0, "inserted": 0}}
            assert impact["derived_groups"] == {"users.paying": 1}
            assert cascades == [
                {
                    "target": "users",
                    "table": "orders",
                    "via": [],
                    "on_delete": "cascade",
                    "count": 3,
                }
            ]
            assert ms == 1234

            second = diff(RowChange("users", {"id": 9}, "h9", "deleted"), deleted=1)
            await save_preview(session, action_id, second, 99)
            await session.commit()
            rows = auth_db.execute(
                "SELECT table_name, primary_key FROM preview_rows WHERE action_id = %s",
                (action_id,),
            )
            assert rows == [("users", {"id": 9})]
            [(ms,)] = auth_db.execute("SELECT preview_ms FROM actions WHERE id = %s", (action_id,))
            assert ms == 99
    finally:
        await engine.dispose()


async def test_a_diff_with_no_changes_still_stores_the_impact(auth_db: AuthDb) -> None:
    engine = make_engine(asyncpg_url(auth_db.app_url))
    try:
        async with make_sessionmaker(engine)() as session:
            action_id = await make_action(auth_db, session)
            await save_preview(session, action_id, diff(), 5)
            await session.commit()
        [(count,)] = auth_db.execute(
            "SELECT count(*) FROM preview_rows WHERE action_id = %s", (action_id,)
        )
        assert count == 0
        [(impact,)] = auth_db.execute("SELECT impact FROM actions WHERE id = %s", (action_id,))
        assert impact["table_changes"] == {}
    finally:
        await engine.dispose()


async def test_nothing_is_saved_if_the_transaction_rolls_back(auth_db: AuthDb) -> None:
    engine = make_engine(asyncpg_url(auth_db.app_url))
    try:
        async with make_sessionmaker(engine)() as session:
            action_id = await make_action(auth_db, session)
            changes = diff(RowChange("users", {"id": 1}, "h", "deleted"), deleted=1)
            await save_preview(session, action_id, changes, 1)
            await session.rollback()
        [(count,)] = auth_db.execute(
            "SELECT count(*) FROM preview_rows WHERE action_id = %s", (action_id,)
        )
        assert count == 0
        [(impact,)] = auth_db.execute("SELECT impact FROM actions WHERE id = %s", (action_id,))
        assert impact is None
    finally:
        await engine.dispose()


def test_the_impact_and_cascade_json_are_plain_data() -> None:
    json.dumps(diff(deleted=1).impact_json())
    json.dumps(diff(deleted=1).cascade_json())
