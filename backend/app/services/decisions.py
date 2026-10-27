"""A reviewer's decision over HTTP (D-053): maps `WorkflowRunner.resume` and its errors to the API.

The runner claims the decision with one conditional UPDATE; nothing here decides who wins.
"""

import uuid

import structlog

from app.core.auth import CurrentUser
from app.core.errors import ApiError
from app.db.enums import ActionStatus
from app.schemas.actions import DecisionResponse, UserRef
from app.workflow.runner import (
    ActionNotFoundError,
    AlreadyDecidedError,
    Decider,
    WorkflowRunner,
    WorkflowUnavailableError,
)

logger = structlog.get_logger()

RETRY_AFTER_SECONDS = "10"
_DECIDED_VERB = {"rejected": "rejected"}  # every other decided status reads "approved"


def conflict_message(status: str, decided_by_name: str | None) -> str:
    """The 409 text. A later status (`committing`, `committed`, ...) still names an approver."""
    if decided_by_name is not None:
        verb = _DECIDED_VERB.get(status, "approved")
        return f"Already {verb} by {decided_by_name}."
    if status == ActionStatus.EXPIRED.value:
        return "Nobody decided within the review window; the action expired."
    return "This action is not waiting for review."


async def decide(
    runner: WorkflowRunner | None,
    action_id: uuid.UUID,
    user: CurrentUser,
    *,
    approved: bool,
    note: str | None,
) -> DecisionResponse:
    if runner is None:
        raise ApiError(
            503, "unavailable", "Reviews are not available: the workflow is not configured."
        )
    logger.info("decision_requested", approved=approved)
    try:
        view = await runner.resume(
            action_id,
            approved=approved,
            note=note,
            decider=Decider(user.id, user.display_name, user.is_admin),
        )
    except WorkflowUnavailableError:
        raise ApiError(
            503,
            "unavailable",
            "Blastproof is starting up or shutting down. Try again in a few seconds.",
            headers={"Retry-After": RETRY_AFTER_SECONDS},
        ) from None
    except ActionNotFoundError:
        raise ApiError(404, "not_found", "No action was found.") from None
    except AlreadyDecidedError as exc:
        raise ApiError(
            409, "already_decided", conflict_message(exc.status, exc.decided_by_name)
        ) from None
    assert view.decided_at is not None
    logger.info("decision_claimed", approved=approved, status=view.status)
    return DecisionResponse(
        id=view.id,
        status=ActionStatus(view.status),
        decided_by=UserRef(id=user.id, display_name=user.display_name),
        decided_at=view.decided_at,
        reviewer_note=view.reviewer_note,
    )
