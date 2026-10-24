"""SEC-06, DW-B05: at most 5 failed logins per 15 min per email+IP, and 20 per hour per email.

Windows use the database clock, so tests move earlier attempts back in time instead of waiting.
"""

import asyncio
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import TEST_CLIENT_IP_HEADER, AuthDb, login, with_client_ip

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("blastproof_db_reachable")]

WRONG = "definitely the wrong password"
TOO_MANY = {
    "error": {"code": "too_many_attempts", "message": "Too many failed logins. Try again later."}
}


def fail(client: TestClient, email: str, ip: str, times: int) -> None:
    for _ in range(times):
        assert login(client, email, WRONG, ip).status_code == 401


def test_done_when_sixth_failure_from_one_ip_is_refused(
    auth_client: TestClient, auth_db: AuthDb
) -> None:
    user = auth_db.make_user()
    fail(auth_client, user.email, "198.51.100.7", 5)

    # Refused even with the right password, which is not checked at all.
    refused = login(auth_client, user.email, user.password, "198.51.100.7")
    assert refused.status_code == 429
    assert refused.json() == TOO_MANY
    assert 1 <= int(refused.headers["retry-after"]) <= 15 * 60
    assert "access_token" not in refused.text
    # Refused attempts are not recorded.
    assert auth_db.failures(user.email) == 5
    assert auth_db.execute(
        "SELECT count(*) FROM login_attempts WHERE email = %s AND succeeded", (user.email,)
    ) == [(0,)]

    # The same email from another IP is not limited by that IP's failures.
    assert login(auth_client, user.email, user.password, "198.51.100.8").status_code == 200


def test_ip_limit_ends_when_the_window_passes(auth_client: TestClient, auth_db: AuthDb) -> None:
    user = auth_db.make_user()
    fail(auth_client, user.email, "198.51.100.9", 5)
    assert login(auth_client, user.email, user.password, "198.51.100.9").status_code == 429
    auth_db.backdate_attempts(user.email, timedelta(minutes=15, seconds=1))
    assert login(auth_client, user.email, user.password, "198.51.100.9").status_code == 200


def test_four_failures_then_the_right_password_is_fine(
    auth_client: TestClient, auth_db: AuthDb
) -> None:
    user = auth_db.make_user()
    fail(auth_client, user.email, "198.51.100.10", 4)
    assert login(auth_client, user.email, user.password, "198.51.100.10").status_code == 200
    # A success does not clear the failures: one more and the IP is limited.
    fail(auth_client, user.email, "198.51.100.10", 1)
    assert login(auth_client, user.email, user.password, "198.51.100.10").status_code == 429


def test_done_when_21st_failure_for_one_email_is_refused_across_ips(
    auth_client: TestClient, auth_db: AuthDb
) -> None:
    user = auth_db.make_user()
    # 20 failures from 5 IPs, 4 each: never over the per-IP limit.
    for n in range(5):
        fail(auth_client, user.email, f"192.0.2.{n + 1}", 4)
    refused = login(auth_client, user.email, user.password, "192.0.2.99")
    assert refused.status_code == 429
    assert refused.json() == TOO_MANY
    assert 15 * 60 < int(refused.headers["retry-after"]) <= 60 * 60
    assert auth_db.failures(user.email) == 20

    # Older than an hour, they no longer count.
    auth_db.backdate_attempts(user.email, timedelta(hours=1, seconds=1))
    assert login(auth_client, user.email, user.password, "192.0.2.99").status_code == 200


def test_unknown_emails_are_limited_too(auth_client: TestClient) -> None:
    fail(auth_client, "ghost@example.com", "198.51.100.11", 5)
    assert login(auth_client, "ghost@example.com", WRONG, "198.51.100.11").status_code == 429


def test_limits_are_per_email(auth_client: TestClient, auth_db: AuthDb) -> None:
    locked, other = auth_db.make_user(), auth_db.make_user()
    fail(auth_client, locked.email, "198.51.100.12", 5)
    assert login(auth_client, other.email, other.password, "198.51.100.12").status_code == 200


def test_successes_do_not_count(auth_client: TestClient, auth_db: AuthDb) -> None:
    user = auth_db.make_user()
    for _ in range(6):
        assert login(auth_client, user.email, user.password, "198.51.100.13").status_code == 200


async def test_parallel_failures_are_counted_one_at_a_time(
    auth_settings: Settings, auth_db: AuthDb
) -> None:
    """The per-email lock stops parallel attempts from all seeing "4 failures so far"."""
    user = auth_db.make_user()
    app = create_app(auth_settings)
    transport = httpx.ASGITransport(app=with_client_ip(app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    "/v1/auth/login",
                    json={"email": user.email, "password": WRONG},
                    headers={TEST_CLIENT_IP_HEADER.decode(): "198.51.100.14"},
                )
                for _ in range(10)
            )
        )
    await app.state.engine.dispose()
    assert sorted(r.status_code for r in responses) == [401] * 5 + [429] * 5
    assert auth_db.failures(user.email) == 5
