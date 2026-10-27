"""Unit tests (no Docker) for the approve/reject HTTP layer: the 409 text, the reject body, and the
mapping from the runner's errors to API errors."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.auth import CurrentUser
from app.core.errors import ApiError
from app.db.actions import StatusView
from app.db.enums import UserRole
from app.schemas.actions import RejectRequest
from app.services.decisions import conflict_message, decide
from app.workflow.runner import (
    ActionNotFoundError,
    AlreadyDecidedError,
    Decider,
    WorkflowUnavailableError,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ACTION = uuid.uuid4()


def user(role: UserRole = UserRole.MEMBER) -> CurrentUser:
    return CurrentUser(uuid.uuid4(), "r@example.com", "Rita", role)


class FakeRunner:
    def __init__(self, *, raises: Exception | None = None, view: StatusView | None = None) -> None:
        self.raises = raises
        self.view = view
        self.calls: list[dict[str, Any]] = []

    async def resume(
        self, action_id: uuid.UUID, *, approved: bool, note: str | None, decider: Decider
    ) -> StatusView:
        self.calls.append({"approved": approved, "note": note, "decider": decider})
        if self.raises is not None:
            raise self.raises
        assert self.view is not None
        return self.view


def view(status: str = "approved", note: str | None = None) -> StatusView:
    return StatusView(ACTION, status, None, None, note, None, "Rita", NOW, None, NOW)


# --- conflict_message ---


@pytest.mark.parametrize(
    ("status", "name", "expected"),
    [
        ("approved", "Rita", "Already approved by Rita."),
        ("committing", "Rita", "Already approved by Rita."),
        ("committed", "Rita", "Already approved by Rita."),
        ("rejected", "Sam", "Already rejected by Sam."),
        ("expired", None, "Nobody decided within the review window; the action expired."),
        ("executed", None, "This action is not waiting for review."),
        ("blocked", None, "This action is not waiting for review."),
        ("failed", None, "This action is not waiting for review."),
    ],
)
def test_conflict_message(status: str, name: str | None, expected: str) -> None:
    assert conflict_message(status, name) == expected


# --- RejectRequest ---


def test_a_reason_of_ten_characters_is_accepted() -> None:
    assert RejectRequest(reason="x" * 10).reason == "x" * 10


@pytest.mark.parametrize(
    "reason", ["x" * 9, "x" * 9 + "   ", "   " + "x" * 9, "x" * 501, "", " " * 20]
)
def test_a_bad_reason_is_refused(reason: str) -> None:
    with pytest.raises(ValidationError):
        RejectRequest(reason=reason)


def test_the_reason_is_stripped() -> None:
    assert RejectRequest(reason="  not this table  ").reason == "not this table"


def test_five_hundred_characters_are_accepted() -> None:
    assert len(RejectRequest(reason="x" * 500).reason) == 500


def test_an_extra_or_missing_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        RejectRequest.model_validate({"reason": "x" * 10, "approve": True})
    with pytest.raises(ValidationError):
        RejectRequest.model_validate({})


# --- decide ---


async def test_no_runner_is_a_503() -> None:
    with pytest.raises(ApiError) as raised:
        await decide(None, ACTION, user(), approved=True, note=None)
    assert (raised.value.status_code, raised.value.code) == (503, "unavailable")
    assert "not configured" in raised.value.message


@pytest.mark.parametrize("reason", ["recovering", "draining"])
async def test_an_unavailable_runner_is_a_503_with_retry_after(reason: str) -> None:
    runner = FakeRunner(raises=WorkflowUnavailableError(reason))  # type: ignore[arg-type]
    with pytest.raises(ApiError) as raised:
        await decide(runner, ACTION, user(), approved=True, note=None)  # type: ignore[arg-type]
    assert raised.value.status_code == 503 and raised.value.code == "unavailable"
    assert raised.value.headers == {"Retry-After": "10"}


async def test_an_unknown_action_is_the_same_404_as_get() -> None:
    runner = FakeRunner(raises=ActionNotFoundError())
    with pytest.raises(ApiError) as raised:
        await decide(runner, ACTION, user(), approved=True, note=None)  # type: ignore[arg-type]
    assert (raised.value.status_code, raised.value.code, raised.value.message) == (
        404,
        "not_found",
        "No action was found.",
    )


async def test_a_lost_race_is_a_409_naming_the_decider() -> None:
    runner = FakeRunner(raises=AlreadyDecidedError("rejected", "Sam"))
    with pytest.raises(ApiError) as raised:
        await decide(runner, ACTION, user(), approved=True, note=None)  # type: ignore[arg-type]
    assert (raised.value.status_code, raised.value.code) == (409, "already_decided")
    assert raised.value.message == "Already rejected by Sam."


async def test_the_decider_carries_the_admin_flag_and_the_note() -> None:
    admin = user(UserRole.ADMIN)
    runner = FakeRunner(view=view("rejected", "not this table"))
    result = await decide(runner, ACTION, admin, approved=False, note="not this table")  # type: ignore[arg-type]
    [call] = runner.calls
    assert call["decider"] == Decider(admin.id, "Rita", True)
    assert call["approved"] is False and call["note"] == "not this table"
    assert result.status == "rejected" and result.reviewer_note == "not this table"
    assert result.decided_by.id == admin.id and result.decided_at == NOW


async def test_a_member_is_not_passed_as_admin() -> None:
    runner = FakeRunner(view=view())
    await decide(runner, ACTION, user(), approved=True, note=None)  # type: ignore[arg-type]
    assert runner.calls[0]["decider"].is_admin is False
