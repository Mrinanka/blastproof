"""Saving a shadow run (backend plan step 10): the per-row diff and the action's impact facts.

`preview_rows` can hold hundreds of thousands of rows, so they go in with COPY through the asyncpg
driver connection that the SQLAlchemy session already uses (same transaction). COPY's CSV text
form is used because jsonb needs no codec there.
"""

import csv
import io
import json
import uuid

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Action, PreviewRow
from app.services.shadow import Diff


def _csv(action_id: uuid.UUID, diff: Diff) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    for change in diff.changes:
        writer.writerow(
            [
                str(action_id),
                change.table,
                json.dumps(change.primary_key, separators=(", ", ": ")),
                change.before_hash,  # None is written as an empty field, which COPY reads as NULL
                change.change,
            ]
        )
    return out.getvalue().encode()


async def save_preview(
    session: AsyncSession, action_id: uuid.UUID, diff: Diff, preview_ms: int
) -> None:
    """Replace the action's preview rows and store its impact, in the caller's transaction.

    A re-preview after drift starts from nothing (D-084): the old rows are deleted first.
    """
    await session.execute(delete(PreviewRow).where(PreviewRow.action_id == action_id))
    if diff.changes:
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        await raw.driver_connection.copy_to_table(  # type: ignore[union-attr]
            "preview_rows",
            source=io.BytesIO(_csv(action_id, diff)),
            columns=["action_id", "table_name", "primary_key", "before_hash", "change"],
            format="csv",
        )
    await session.execute(
        update(Action)
        .where(Action.id == action_id)
        .values(
            impact=diff.impact_json(),
            cascade_expectations=diff.cascade_json(),
            preview_ms=preview_ms,
        )
    )
    await session.flush()
