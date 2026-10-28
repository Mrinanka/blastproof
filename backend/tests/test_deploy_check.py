"""The deployed-backend probe (Deploy: OPS-13, SEC-22, BE-119), run against a fake server."""

import json
from collections.abc import Callable

import httpx
import pytest

from app.cli import main
from app.services import deploy_check
from app.services.deploy_check import READY_COMPONENTS, Check, render, run_checks

BASE = "https://blastproof.example"
ENVELOPE = {"error": {"code": "x", "message": "y"}}
Handler = Callable[[httpx.Request], httpx.Response]


def good(request: httpx.Request) -> httpx.Response:
    """What a correct deployment answers."""
    headers = {"x-request-id": "r1"}
    url = request.url
    if url.scheme == "http":
        return httpx.Response(301, headers={"location": f"https://{url.host}{url.path}"})
    if url.path == "/health":
        return httpx.Response(200, json={"status": "ok"}, headers=headers)
    if url.path == "/ready":
        checks = dict.fromkeys(sorted(READY_COMPONENTS), "ok")
        return httpx.Response(200, json={"status": "ready", "checks": checks}, headers=headers)
    if url.path == "/mcp":
        return httpx.Response(401, json=ENVELOPE, headers=headers | {"www-authenticate": "Bearer"})
    if url.path == "/docs":
        return httpx.Response(200, text="<html>docs</html>", headers=headers)
    if request.method == "OPTIONS":
        return httpx.Response(405, json=ENVELOPE, headers=headers)
    return httpx.Response(404, json=ENVELOPE, headers=headers)


def client_for(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def results(handler: Handler) -> dict[str, Check]:
    async with client_for(handler) as client:
        return {c.name: c for c in await run_checks(client, BASE)}


def failing(checks: dict[str, Check]) -> set[str]:
    return {name for name, c in checks.items() if not c.ok}


def with_override(match: Callable[[httpx.Request], bool], answer: Handler) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return answer(request) if match(request) else good(request)

    return handler


def path_is(path: str) -> Callable[[httpx.Request], bool]:
    return lambda request: request.url.path == path and request.url.scheme == "https"


async def test_every_check_passes_on_a_good_deployment() -> None:
    checks = await results(good)
    assert len(checks) == 9
    assert failing(checks) == set()


@pytest.mark.parametrize(
    ("case", "handler", "expected"),
    [
        (
            "health 500",
            with_override(path_is("/health"), lambda r: httpx.Response(500, json={})),
            {"health", "every response has X-Request-ID"},
        ),
        (
            "ready with a component down",
            with_override(
                path_is("/ready"),
                lambda r: httpx.Response(
                    200,
                    json={"status": "ready", "checks": {"blastproof_db": "ok"}},
                    headers={"x-request-id": "r"},
                ),
            ),
            {"ready"},
        ),
        (
            "no https redirect",
            with_override(lambda r: r.url.scheme == "http", lambda r: httpx.Response(200, json={})),
            {"http redirects to https"},
        ),
        (
            "fastapi detail 404",
            with_override(
                lambda r: r.url.path.startswith("/no-such"),
                lambda r: httpx.Response(
                    404, json={"detail": "Not Found"}, headers={"x-request-id": "r"}
                ),
            ),
            {"unknown route is a 404 error envelope"},
        ),
        (
            "a CORS header",
            with_override(
                lambda r: r.method == "OPTIONS",
                lambda r: httpx.Response(
                    200,
                    headers={"x-request-id": "r", "access-control-allow-origin": "*"},
                ),
            ),
            {"no CORS headers on a preflight"},
        ),
        (
            "mcp answers 200",
            with_override(path_is("/mcp"), lambda r: httpx.Response(200, json={})),
            {"/mcp refuses a call with no key", "every response has X-Request-ID"},
        ),
        (
            "mcp 401 without the envelope",
            with_override(
                path_is("/mcp"),
                lambda r: httpx.Response(401, json={"detail": "x"}, headers={"x-request-id": "r"}),
            ),
            {"/mcp refuses a call with no key"},
        ),
        (
            "docs 404",
            with_override(
                path_is("/docs"),
                lambda r: httpx.Response(404, json=ENVELOPE, headers={"x-request-id": "r"}),
            ),
            {"/docs is served"},
        ),
        (
            "missing request id",
            with_override(path_is("/docs"), lambda r: httpx.Response(200, text="docs")),
            {"every response has X-Request-ID"},
        ),
    ],
)
async def test_each_check_fails_on_its_own_bad_case(
    case: str, handler: Handler, expected: set[str]
) -> None:
    checks = await results(handler)
    assert failing(checks) == expected, case


@pytest.mark.parametrize(
    ("leak", "kind"),
    [
        ("postgresql://user:hunter2pass@db.example/x", "a database URL with a password"),
        ("bp_" + "A" * 43, "an agent key"),
        ("npg_" + "B" * 12, "a Neon key"),
        ("ep-cool-123.us-east-2.aws.neon.tech", "a Neon host"),
    ],
)
async def test_a_leaked_value_fails_and_is_never_repeated(leak: str, kind: str) -> None:
    handler = with_override(
        path_is("/docs"),
        lambda r: httpx.Response(200, text=f"oops {leak}", headers={"x-request-id": "r"}),
    )
    checks = await results(handler)
    assert failing(checks) == {"no response holds a credential"}
    output = render(list(checks.values()))
    assert kind in output
    assert leak not in output
    assert "hunter2pass" not in output


async def test_a_timeout_or_connection_error_is_a_failed_check_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            raise httpx.ConnectTimeout("slow", request=request)
        if request.url.path == "/ready":
            raise httpx.ConnectError("refused postgresql://u:secretpw@h/x", request=request)
        return good(request)

    checks = await results(handler)
    assert {"health", "ready"} <= failing(checks)
    assert "secretpw" not in render(list(checks.values()))


def test_the_command_prints_one_line_per_check_and_exits_1_on_a_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_probe(url: str) -> list[Check]:
        return [Check("a", True, "fine"), Check("b", False, "status 500")]

    monkeypatch.setattr(deploy_check, "probe", fake_probe)
    assert main(["deploy-check", "--url", BASE]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["ok   a: fine", "FAIL b: status 500"]


def test_the_command_exits_0_when_every_check_passes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_probe(url: str) -> list[Check]:
        return [Check("a", True, "fine")]

    monkeypatch.setattr(deploy_check, "probe", fake_probe)
    assert main(["deploy-check", "--url", BASE]) == 0
    assert "FAIL" not in capsys.readouterr().out


def test_the_command_rejects_a_url_without_a_scheme(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["deploy-check", "--url", "blastproof.fly.dev"]) == 1
    assert "must start with https://" in capsys.readouterr().err


def test_the_command_reads_no_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # It must work with no .env at all, and never touch a secret.
    async def fake_probe(url: str) -> list[Check]:
        return []

    monkeypatch.setattr(deploy_check, "probe", fake_probe)
    monkeypatch.setattr(
        "app.cli.Settings", lambda *a, **k: pytest.fail("deploy-check built Settings")
    )
    assert main(["deploy-check", "--url", BASE]) == 0


def test_the_json_helper_tolerates_a_non_json_body() -> None:
    assert deploy_check._json(httpx.Response(200, text="<html>")) is None
    assert json.loads('{"a": 1}') == {"a": 1}
