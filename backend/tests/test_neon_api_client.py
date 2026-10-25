"""Step 10: NeonApiClient against Neon's REST API, played back by httpx.MockTransport."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest

from app.config import Settings
from app.core.clock import FrozenClock
from app.services.neon import (
    FakeNeonClient,
    NeonApiClient,
    NeonBusyError,
    NeonError,
    ShadowTarget,
    neon_client_for,
)

pytestmark = pytest.mark.usefixtures("no_network")

ACTION = UUID("11111111-2222-3333-4444-555555555555")
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
WRITER = (
    "postgresql://blastproof_writer:wr1ter-secret@ep-main.example.neon.tech/neondb?sslmode=require"
)
API_KEY = "napi_super_secret_key"


class FakeNeon:
    """A tiny Neon: records requests and answers from a script."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.create: list[tuple[int, dict]] = [
            (201, self.created("br-1", "ep-1.example.neon.tech"))
        ]
        self.operation_states = ["finished"]
        self.delete_status = 200
        self.branches: list[dict] = []
        self.pages: list[dict] = []

    @staticmethod
    def created(branch: str, host: str) -> dict:
        return {
            "branch": {"id": branch},
            "endpoints": [{"host": host}],
            "operations": [{"id": "op-1"}, {"id": "op-2"}],
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api/v2")
        if request.method == "POST" and path.endswith("/branches"):
            status, body = self.create.pop(0) if len(self.create) > 1 else self.create[0]
            return httpx.Response(status, json=body)
        if request.method == "GET" and "/operations/" in path:
            states = self.operation_states
            state = states.pop(0) if len(states) > 1 else states[0]
            return httpx.Response(200, json={"operation": {"status": state}})
        if request.method == "DELETE":
            content = b"" if self.delete_status == 204 else b"{}"
            return httpx.Response(self.delete_status, content=content)
        if request.method == "GET" and path.endswith("/branches"):
            page = self.pages.pop(0) if self.pages else {"branches": self.branches}
            return httpx.Response(200, json=page)
        if request.method == "GET" and path.startswith("/projects/"):
            return httpx.Response(200, json={"project": {"name": "blastproof-company-dev"}})
        return httpx.Response(500)

    def calls(self, method: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method]


class Connections:
    """Stands in for asyncpg.connect: fails `fail_first` times, then yields a closable stub."""

    def __init__(self, fail_first: int = 0, always_fail: bool = False) -> None:
        self.fail_first = fail_first
        self.always_fail = always_fail
        self.attempts = 0

    async def __call__(self, **kwargs: object) -> "Connections":
        self.attempts += 1
        if self.always_fail or self.attempts <= self.fail_first:
            raise OSError("not up yet")
        return self

    async def fetchval(self, sql: str) -> int:
        return 1

    async def close(self) -> None:
        return None


def client(
    neon: FakeNeon, connect: Connections | None = None, **overrides: object
) -> NeonApiClient:
    values: dict[str, object] = {
        "api_key": API_KEY,
        "project_id": "proj-1",
        "parent_branch_id": "br-parent",
        "writer_url": WRITER,
        "clock": FrozenClock(NOW),
        "transport": httpx.MockTransport(neon.handler),
        "poll_interval": 0,
        "retry_delays": (0, 0, 0),
        "connect_timeout": 0.05,
        "connect": connect or Connections(),
    }
    return NeonApiClient(**(values | overrides))  # type: ignore[arg-type]


async def test_create_sends_the_exact_payload_and_returns_a_target() -> None:
    neon = FakeNeon()
    target = await client(neon).create_shadow(ACTION)
    post = neon.calls("POST")[0]
    assert post.url.path == "/api/v2/projects/proj-1/branches"
    assert post.headers["authorization"] == f"Bearer {API_KEY}"
    assert json.loads(post.content) == {
        "branch": {
            "parent_id": "br-parent",
            "name": f"shadow-{ACTION}",
            "expires_at": "2026-10-01T12:15:00Z",
        },
        "endpoints": [{"type": "read_write"}],
    }
    assert target.branch_id == "br-1"
    assert target.connect_kwargs == {
        "host": "ep-1.example.neon.tech",
        "port": 5432,
        "user": "blastproof_writer",
        "password": "wr1ter-secret",
        "database": "neondb",
        "ssl": "require",
    }


async def test_both_operations_are_polled_until_finished_or_skipped() -> None:
    neon = FakeNeon()
    neon.operation_states = ["running", "finished", "scheduling", "skipped"]
    await client(neon).create_shadow(ACTION)
    polled = [r.url.path.rsplit("/", 1)[1] for r in neon.calls("GET")]
    assert polled == ["op-1", "op-1", "op-2", "op-2"]


@pytest.mark.parametrize("bad", ["failed", "error", "cancelled"])
async def test_a_bad_operation_fails_and_the_branch_is_deleted(bad: str) -> None:
    neon = FakeNeon()
    neon.operation_states = [bad]
    with pytest.raises(NeonError) as raised:
        await client(neon).create_shadow(ACTION)
    assert raised.value.reason_code == "neon_operation_failed"
    assert [r.url.path.rsplit("/", 1)[1] for r in neon.calls("DELETE")] == ["br-1"]


async def test_an_operation_that_never_ends_times_out_and_deletes() -> None:
    neon = FakeNeon()
    neon.operation_states = ["running"]
    with pytest.raises(NeonError) as raised:
        await client(neon, operation_timeout=0.05, poll_interval=0.01).create_shadow(ACTION)
    assert raised.value.reason_code == "neon_operation_timeout"
    assert len(neon.calls("DELETE")) == 1


async def test_the_branch_cap_is_busy_not_failed() -> None:
    neon = FakeNeon()
    neon.create = [(422, {"code": "BRANCHES_LIMIT_EXCEEDED", "message": "branches limit exceeded"})]
    with pytest.raises(NeonBusyError):
        await client(neon).create_shadow(ACTION)
    assert neon.calls("DELETE") == []


@pytest.mark.parametrize("status", [400, 401, 404, 500])
async def test_other_errors_carry_the_status(status: int) -> None:
    neon = FakeNeon()
    neon.create = [(status, {"code": "", "message": "x"})]
    with pytest.raises(NeonError) as raised:
        await client(neon).create_shadow(ACTION)
    assert raised.value.reason_code == f"neon_{status}"
    assert not isinstance(raised.value, NeonBusyError)


async def test_423_and_503_are_retried_then_succeed() -> None:
    neon = FakeNeon()
    neon.create = [(423, {}), (503, {}), (201, FakeNeon.created("br-9", "ep-9.example"))]
    target = await client(neon).create_shadow(ACTION)
    assert target.branch_id == "br-9" and len(neon.calls("POST")) == 3


async def test_a_423_that_never_clears_gives_up() -> None:
    neon = FakeNeon()
    neon.create = [(423, {})]
    with pytest.raises(NeonError) as raised:
        await client(neon).create_shadow(ACTION)
    assert raised.value.reason_code == "neon_423"
    assert len(neon.calls("POST")) == 4  # the first try and three retries


async def test_a_malformed_create_response_fails() -> None:
    neon = FakeNeon()
    neon.create = [(201, {"branch": {}})]
    with pytest.raises(NeonError) as raised:
        await client(neon).create_shadow(ACTION)
    assert raised.value.reason_code == "neon_bad_response"


async def test_the_first_connection_is_retried() -> None:
    neon, connect = FakeNeon(), Connections(fail_first=2)
    await client(neon, connect, connect_timeout=5).create_shadow(ACTION)
    assert connect.attempts == 3 and neon.calls("DELETE") == []


async def test_a_branch_that_never_accepts_connections_is_given_up_and_deleted() -> None:
    neon, connect = FakeNeon(), Connections(always_fail=True)
    with pytest.raises(NeonError) as raised:
        await client(neon, connect).create_shadow(ACTION)
    assert raised.value.reason_code == "neon_connect_failed"
    assert len(neon.calls("DELETE")) == 1


@pytest.mark.parametrize("status", [200, 204])
async def test_delete_accepts_200_and_204(status: int) -> None:
    neon = FakeNeon()
    neon.delete_status = status
    await client(neon).delete_shadow(ShadowTarget(branch_id="br-1", connect_kwargs={}))
    assert neon.calls("DELETE")[0].url.path == "/api/v2/projects/proj-1/branches/br-1"


async def test_delete_500_raises() -> None:
    neon = FakeNeon()
    neon.delete_status = 500
    with pytest.raises(NeonError) as raised:
        await client(neon).delete_shadow(ShadowTarget(branch_id="br-1", connect_kwargs={}))
    assert raised.value.reason_code == "neon_500"


def branch(name: str, age_minutes: int, *, default: bool = False) -> dict:
    created = (NOW - timedelta(minutes=age_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": f"br-{name}", "name": name, "created_at": created, "default": default}


async def test_sweep_pages_and_deletes_only_old_non_default_shadow_branches() -> None:
    neon = FakeNeon()
    neon.pages = [
        {
            "branches": [
                branch("shadow-old", 30),
                branch("shadow-fresh", 5),
                branch("production", 600, default=True),
                branch("feature-old", 600),
            ],
            "pagination": {"next": "cursor-2"},
        },
        {"branches": [branch("shadow-old-2", 20), branch("shadow-default", 90, default=True)]},
    ]
    assert await client(neon).sweep(timedelta(minutes=15)) == 2
    listed = neon.calls("GET")
    assert listed[0].url.params.get("cursor") is None
    assert listed[1].url.params["cursor"] == "cursor-2"
    assert sorted(r.url.path.rsplit("/", 1)[1] for r in neon.calls("DELETE")) == [
        "br-shadow-old",
        "br-shadow-old-2",
    ]


async def test_ping_reads_the_project() -> None:
    neon = FakeNeon()
    await client(neon).ping()
    assert neon.requests[0].url.path == "/api/v2/projects/proj-1"


async def test_ping_fails_on_a_bad_key() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "supplied credentials do not pass"})

    with pytest.raises(NeonError) as raised:
        await client(FakeNeon(), transport=httpx.MockTransport(refuse)).ping()
    assert raised.value.reason_code == "neon_401"


async def test_secrets_stay_out_of_reprs_and_errors() -> None:
    neon = FakeNeon()
    api = client(neon)
    target = await api.create_shadow(ACTION)
    assert API_KEY not in repr(api) and "wr1ter-secret" not in repr(api)
    assert "wr1ter-secret" not in repr(target) and "wr1ter-secret" not in str(target)
    neon.create = [(500, {"message": f"boom {API_KEY}"})]
    with pytest.raises(NeonError) as raised:
        await api.create_shadow(ACTION)
    assert API_KEY not in str(raised.value) and "boom" not in str(raised.value)


# --- choosing the client ----------------------------------------------------------------------


def settings(**values: object) -> Settings:
    base: dict[str, object] = {
        "company_db_writer_url": WRITER,
        "local_company_writer_url": "postgresql://blastproof_writer:w@localhost:5433/company_main",
        "fake_neon_admin_url": "postgresql://company:company@localhost:5433/postgres",
        "neon_api_key": API_KEY,
        "neon_project_id": "proj-1",
        "neon_parent_branch_id": "br-parent",
        "neon_client": "api",  # CI sets NEON_CLIENT=fake
    }
    return Settings(_env_file=None, app_env="test", **(base | values))  # type: ignore[arg-type]


def test_the_local_profile_always_uses_the_fake() -> None:
    clock = FrozenClock(NOW)
    assert isinstance(neon_client_for("local", settings(neon_client="api"), clock), FakeNeonClient)


def test_the_neon_profile_uses_the_api_unless_told_to_fake() -> None:
    clock = FrozenClock(NOW)
    assert isinstance(neon_client_for("neon", settings(neon_client="api"), clock), NeonApiClient)
    assert isinstance(neon_client_for("neon", settings(neon_client="fake"), clock), FakeNeonClient)


@pytest.mark.parametrize(
    "missing", ["company_db_writer_url", "neon_api_key", "neon_project_id", "neon_parent_branch_id"]
)
def test_an_unconfigured_neon_profile_has_no_client(missing: str) -> None:
    assert neon_client_for("neon", settings(**{missing: None}), FrozenClock(NOW)) is None
