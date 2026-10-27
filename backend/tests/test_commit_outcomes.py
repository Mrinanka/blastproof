"""Step 16: what an error at COMMIT means. No database: a fake transaction raises."""

import asyncio
import uuid
from typing import Any

import asyncpg
import pytest

from app.config import Settings
from app.core.clock import SystemClock
from app.services.commit import CommitFailedError
from app.services.committer import CompanyCommitter, _OutcomeUnknownError


class FakeTx:
    def __init__(self, raises: BaseException | None = None) -> None:
        self.raises = raises
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1
        if self.raises is not None:
            raise self.raises


class Committer(CompanyCommitter):
    def __init__(self, fault: Any = None) -> None:
        super().__init__(None, Settings(_env_file=None, app_env="test"), SystemClock(), fault=fault)  # type: ignore[arg-type]
        self.dropped: list[uuid.UUID] = []

    async def _drop_images(self, action_id: uuid.UUID) -> None:
        self.dropped.append(action_id)


ACTION = uuid.uuid4()


async def test_a_clean_commit_returns_and_keeps_the_before_images() -> None:
    committer, tx = Committer(), FakeTx()
    await committer._finish(tx, ACTION)
    assert tx.commits == 1 and committer.dropped == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (asyncpg.exceptions.SerializationError("x"), "commit_error_40001"),
        (asyncpg.exceptions.DeadlockDetectedError("x"), "commit_error_40P01"),
        (asyncpg.exceptions.DiskFullError("x"), "commit_error_53100"),
    ],
)
async def test_a_server_error_at_commit_means_nothing_was_applied(
    error: Exception, code: str
) -> None:
    committer = Committer()
    with pytest.raises(CommitFailedError) as raised:
        await committer._finish(FakeTx(error), ACTION)
    assert raised.value.reason_code == code
    assert committer.dropped == [ACTION]  # the before-images of a write that did not happen go


@pytest.mark.parametrize(
    "error",
    [
        asyncpg.exceptions.ConnectionDoesNotExistError("x"),  # 08003
        asyncpg.exceptions.ConnectionFailureError("x"),  # 08006
        ConnectionResetError("x"),
        OSError("x"),
        TimeoutError(),
        asyncpg.InterfaceError("x"),
        RuntimeError("x"),
    ],
)
async def test_any_other_error_at_commit_does_not_say_and_is_never_a_failure(
    error: Exception,
) -> None:
    committer = Committer()
    with pytest.raises(_OutcomeUnknownError):
        await committer._finish(FakeTx(error), ACTION)
    assert committer.dropped == []  # the write may be in the database: keep its before-images


async def test_a_cancel_at_commit_propagates_untouched() -> None:
    committer = Committer()
    with pytest.raises(asyncio.CancelledError):
        await committer._finish(FakeTx(asyncio.CancelledError()), ACTION)
    assert committer.dropped == []


async def test_a_cancel_in_the_fault_seam_propagates_untouched() -> None:
    async def fault(stage: str, _tx: Any) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await Committer(fault)._finish(FakeTx(), ACTION)


async def test_the_seam_runs_before_and_after_the_commit() -> None:
    stages: list[str] = []

    async def fault(stage: str, tx: Any) -> None:
        stages.append(f"{stage}:{tx.commits}")

    tx = FakeTx()
    await Committer(fault)._finish(tx, ACTION)
    assert stages == ["before_commit:0", "after_commit:1"]
