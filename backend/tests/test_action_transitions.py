"""BE-16: the status state machine. `set_status` refuses every move the plan does not draw."""

import itertools

import pytest

from app.db.actions import IllegalTransitionError
from app.db.enums import ALLOWED_TRANSITIONS, ActionStatus
from tests.conftest import AuthDb

S = ActionStatus

# Backend plan §5, spelled out edge by edge, not read from ALLOWED_TRANSITIONS (so it is checked).
DRAWN = {
    (S.RECEIVED, S.CLASSIFIED),
    (S.RECEIVED, S.EXECUTED),
    (S.RECEIVED, S.FAILED),
    (S.CLASSIFIED, S.PREVIEWING),
    (S.CLASSIFIED, S.APPROVED),  # the fast lane
    (S.CLASSIFIED, S.BLOCKED),
    (S.CLASSIFIED, S.FAILED),
    (S.PREVIEWING, S.AWAITING_REVIEW),
    (S.PREVIEWING, S.APPROVED),  # auto-allow
    (S.PREVIEWING, S.BLOCKED),
    (S.PREVIEWING, S.FAILED),
    (S.AWAITING_REVIEW, S.APPROVED),
    (S.AWAITING_REVIEW, S.REJECTED),
    (S.AWAITING_REVIEW, S.EXPIRED),
    (S.AWAITING_REVIEW, S.FAILED),
    (S.APPROVED, S.COMMITTING),
    (S.APPROVED, S.FAILED),
    (S.COMMITTING, S.COMMITTED),
    (S.COMMITTING, S.DRIFTED),
    (S.COMMITTING, S.FAILED),
    (S.DRIFTED, S.PREVIEWING),
    (S.DRIFTED, S.FAILED),
}
TERMINAL = {S.REJECTED, S.EXPIRED, S.BLOCKED, S.COMMITTED, S.EXECUTED, S.FAILED}


def test_the_map_has_exactly_the_drawn_edges() -> None:
    actual = {(a, b) for a, targets in ALLOWED_TRANSITIONS.items() for b in targets}
    assert actual == DRAWN


def test_every_status_has_an_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(ActionStatus)


@pytest.mark.parametrize("status", sorted(TERMINAL))
def test_terminal_statuses_go_nowhere(status: ActionStatus) -> None:
    assert ALLOWED_TRANSITIONS[status] == frozenset()


def test_pending_is_not_a_status() -> None:
    assert "pending" not in {s.value for s in ActionStatus}


def test_every_pair_is_either_drawn_or_refused() -> None:
    refused = [pair for pair in itertools.product(S, S) if pair not in DRAWN]
    assert len(refused) == len(S) ** 2 - len(DRAWN)
    for source, target in refused:
        assert target not in ALLOWED_TRANSITIONS[source]


def test_illegal_transition_error_is_a_runtime_error() -> None:
    assert issubclass(IllegalTransitionError, RuntimeError)


# --- the guard itself, on a real row --------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("blastproof_db_reachable")
@pytest.mark.parametrize(
    ("source", "target"),
    [
        (S.RECEIVED, S.COMMITTED),  # skipping the whole flow
        (S.CLASSIFIED, S.COMMITTING),  # approval skipped
        (S.AWAITING_REVIEW, S.COMMITTED),
        (S.BLOCKED, S.APPROVED),  # out of a terminal status
        (S.COMMITTED, S.FAILED),
        (S.APPROVED, S.AWAITING_REVIEW),  # backwards: what the two-node review prevents
    ],
)
async def test_set_status_refuses_an_illegal_move_and_changes_nothing(
    auth_db: AuthDb, source: ActionStatus, target: ActionStatus
) -> None:
    from app.db.actions import set_status
    from app.db.session import make_engine, make_sessionmaker
    from tests.action_helpers import insert_action, make_system
    from tests.conftest import asyncpg_url

    user = auth_db.make_user()
    action_id = insert_action(
        auth_db, user, auth_db.make_agent(user.id).id, make_system(auth_db), status=source.value
    )
    engine = make_engine(asyncpg_url(auth_db.app_url))
    try:
        with pytest.raises(IllegalTransitionError, match=f"{source.value} -> {target.value}"):
            async with make_sessionmaker(engine).begin() as session:
                await set_status(session, action_id, target)
    finally:
        await engine.dispose()
    assert auth_db.execute("SELECT status FROM actions WHERE id = %s", (action_id,)) == [
        (source.value,)
    ]
    assert auth_db.execute(
        "SELECT count(*) FROM audit_events WHERE action_id = %s", (action_id,)
    ) == [(0,)]
