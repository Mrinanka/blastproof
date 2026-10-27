"""BE-112: GET /v1/stats, scoped by `visible_actions`, with exact numbers. Needs Docker."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_clock
from app.core.clock import FrozenClock
from tests.action_helpers import insert_action, make_system, token_for
from tests.admin_api_helpers import build_admin_app, client_for
from tests.conftest import AuthDb

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
YESTERDAY = NOW - timedelta(days=1)


@pytest.fixture
def client(auth_db: AuthDb) -> Iterator[TestClient]:
    app = build_admin_app(auth_db)
    app.dependency_overrides[get_clock] = lambda: FrozenClock(NOW)
    yield from client_for(app)


def stats(client: TestClient, user) -> dict:
    response = client.get("/v1/stats", headers=token_for(user))
    assert response.status_code == 200
    return response.json()


def test_the_numbers_are_exact_for_the_owner(client: TestClient, auth_db: AuthDb) -> None:
    owner = auth_db.make_user()
    agent, system = auth_db.make_agent(owner.id), make_system(auth_db)

    def add(**columns) -> None:
        insert_action(auth_db, owner, agent.id, system, **columns)

    reviewer = auth_db.make_user()
    add(status="awaiting_review", decision="review", created_at=NOW - timedelta(hours=1))
    add(status="awaiting_review", decision="review", created_at=YESTERDAY)
    add(
        status="committed",
        decision="allow",
        created_at=NOW - timedelta(hours=3),
        preview_ms=1000,
        llm_cost_usd=Decimal("0.5"),
        decided_by=reviewer.id,
        decided_at=NOW - timedelta(hours=2),
    )
    add(
        status="blocked",
        decision="block",
        created_at=NOW - timedelta(hours=2),
        preview_ms=3000,
        llm_cost_usd=Decimal("0.25"),
    )
    add(status="executed", created_at=NOW - timedelta(minutes=5))  # a read: no decision, no cost
    add(  # yesterday's action, decided today: counts as reviewed today, not as created today
        status="rejected",
        decision="review",
        created_at=YESTERDAY,
        decided_by=reviewer.id,
        decided_at=NOW - timedelta(hours=1),
        preview_ms=999999,
        llm_cost_usd=Decimal("9"),
    )

    body = stats(client, owner)
    assert body["pending_reviews"] == 2
    assert body["by_status"]["awaiting_review"] == 2
    assert body["by_status"]["committed"] == 1
    assert body["by_status"]["failed"] == 0  # every status is present
    assert body["by_decision"] == {"allow": 1, "review": 3, "block": 1}
    today = body["today"]
    assert today["day"] == "2026-10-27"
    assert today["actions"] == 4
    assert today["by_status"]["awaiting_review"] == 1
    assert today["by_decision"] == {"allow": 1, "review": 1, "block": 1}
    assert today["reviewed"] == 2
    assert today["blocked"] == 1
    assert today["avg_preview_ms"] == 2000.0
    assert today["llm_cost_usd"] == 0.75


def test_nothing_today_gives_zeros_and_no_average(client: TestClient, auth_db: AuthDb) -> None:
    body = stats(client, auth_db.make_user())
    assert body["pending_reviews"] == 0
    assert all(count == 0 for count in body["by_status"].values())
    assert body["today"]["avg_preview_ms"] is None
    assert body["today"]["llm_cost_usd"] == 0
    assert body["today"]["actions"] == 0


def test_an_action_from_yesterday_counts_all_time_but_not_today(
    client: TestClient, auth_db: AuthDb
) -> None:
    owner = auth_db.make_user()
    insert_action(
        auth_db, owner, auth_db.make_agent(owner.id).id, make_system(auth_db), created_at=YESTERDAY
    )
    body = stats(client, owner)
    assert body["by_status"]["executed"] == 1
    assert body["today"]["actions"] == 0


def test_the_midnight_boundary(client: TestClient, auth_db: AuthDb) -> None:
    owner = auth_db.make_user()
    agent, system = auth_db.make_agent(owner.id), make_system(auth_db)
    midnight = datetime(2026, 10, 27, tzinfo=UTC)
    insert_action(auth_db, owner, agent.id, system, created_at=midnight - timedelta(milliseconds=1))
    insert_action(auth_db, owner, agent.id, system, created_at=midnight)
    assert stats(client, owner)["today"]["actions"] == 1


def test_members_see_their_own_and_an_admin_sees_all(client: TestClient, auth_db: AuthDb) -> None:
    owner, other = auth_db.make_user(), auth_db.make_user()
    admin = auth_db.make_user(role="admin")
    for user in (owner, owner, other):
        insert_action(
            auth_db,
            user,
            auth_db.make_agent(user.id).id,
            make_system(auth_db),
            created_at=NOW - timedelta(minutes=1),
            status="awaiting_review",
            decision="review",
        )
    assert stats(client, owner)["pending_reviews"] == 2
    assert stats(client, other)["pending_reviews"] == 1
    stranger = stats(client, auth_db.make_user())
    assert stranger["pending_reviews"] == 0 and stranger["today"]["actions"] == 0
    # The admin sees everyone's, including those of earlier tests in this database.
    assert stats(client, admin)["pending_reviews"] >= 3


def test_anonymous_gets_401(client: TestClient) -> None:
    assert client.get("/v1/stats").status_code == 401
