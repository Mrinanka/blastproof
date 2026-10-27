"""What the commit reads from, and saves to, Blastproof's own database (backend plan step 16).

Before-images are the target table's rows as they were (and, for updates and inserts, as they
became), saved before the company transaction commits, so an undo is possible later. They hold real
row values and never leave the backend (SEC-16): nothing here logs a value.
"""

import csv
import io
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Action, BeforeImage, PreviewRow, System
from app.services.commit_sql import canonical_key


@dataclass(frozen=True)
class CommitInputs:
    status: str
    pinned_sql: str
    profile: str
    impact: dict[str, Any]
    cascade_expectations: list[dict[str, Any]]


@dataclass(frozen=True)
class SavedImage:
    """One row for `before_images`; the rows are JSON text, `None` where that side has no row."""

    table: str
    primary_key: str
    row_before: str | None
    row_after: str | None


async def commit_inputs(session: AsyncSession, action_id: uuid.UUID) -> CommitInputs | None:
    """What was approved: the pinned SQL, the system's connection profile, and the preview's
    impact and cascade expectations. None for an unknown action."""
    row = (
        await session.execute(
            select(
                Action.status,
                Action.pinned_sql,
                Action.impact,
                Action.cascade_expectations,
                System.connection_profile,
            )
            .join(System, System.id == Action.system_id)
            .where(Action.id == action_id)
        )
    ).one_or_none()
    if row is None:
        return None
    status, pinned_sql, impact, cascades, profile = row
    return CommitInputs(
        status=status,
        pinned_sql=pinned_sql or "",
        profile=profile,
        impact=impact or {},
        cascade_expectations=list(cascades or []),
    )


async def previewed_keys(
    session: AsyncSession, action_id: uuid.UUID, table: str
) -> tuple[dict[str, str], dict[str, str]]:
    """The deleted and the updated rows of `table` that the preview showed: canonical key -> hash
    before the write."""
    rows = await session.execute(
        select(PreviewRow.primary_key, PreviewRow.before_hash, PreviewRow.change).where(
            PreviewRow.action_id == action_id,
            PreviewRow.table_name == table,
            PreviewRow.change.in_(("deleted", "updated")),
        )
    )
    deleted: dict[str, str] = {}
    updated: dict[str, str] = {}
    for key, before_hash, change in rows:
        (deleted if change == "deleted" else updated)[canonical_key(key)] = before_hash or ""
    return deleted, updated


def _csv(action_id: uuid.UUID, images: Iterable[SavedImage]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    for image in images:
        # None is written as an empty field, which COPY reads as NULL.
        writer.writerow(
            [str(action_id), image.table, image.primary_key, image.row_before, image.row_after]
        )
    return out.getvalue().encode()


async def delete_before_images(session: AsyncSession, action_id: uuid.UUID) -> None:
    await session.execute(delete(BeforeImage).where(BeforeImage.action_id == action_id))


async def replace_before_images(
    session: AsyncSession, action_id: uuid.UUID, images: list[SavedImage]
) -> None:
    """Replace the action's before-images in the caller's transaction (COPY, like `save_preview`).

    A commit that is run again after a crash starts from nothing: the images of the first try are
    deleted, never doubled.
    """
    await delete_before_images(session, action_id)
    if images:
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        await raw.driver_connection.copy_to_table(  # type: ignore[union-attr]
            "before_images",
            source=io.BytesIO(_csv(action_id, images)),
            columns=["action_id", "table_name", "primary_key", "row_before", "row_after"],
            format="csv",
        )
    await session.flush()


async def count_before_images(session: AsyncSession, action_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(BeforeImage).where(BeforeImage.action_id == action_id)
        )
    ).scalar_one()
