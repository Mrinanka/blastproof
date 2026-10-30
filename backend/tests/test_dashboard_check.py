"""The deployed-dashboard probe (Deploy: OPS-14, SEC-28), run against a fake site."""

from collections.abc import Callable

import httpx
import pytest

from app.cli import main
from app.services import dashboard_check
from app.services.dashboard_check import Check, run_checks

BASE = "https://blastproof.example"
HOST = "blastproof.example"
Handler = Callable[[httpx.Request], httpx.Response]

SECURITY = {
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "x-content-type-options": "nosniff",
}
LANDING_CSP = "default-src 'self'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
LOGIN_CSP = (
    "default-src 'self'; script-src 'self' 'nonce-abc' 'strict-dynamic'; frame-ancestors 'none'"
)
COOKIE = "blastproof_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=lax"
OG_PAGE = f'<html><meta property="og:image" content="https://{HOST}/opengraph-image"/></html>'

LANDING = "landing page headers"
HTTPS = "http redirects to https"
LOGIN_REQUIRED = "dashboard needs login"
NONCE = "login page has a nonce CSP"
REACHES = "proxy reaches the backend"
V1_ONLY = "proxy forwards /v1 only"
FOREIGN = "foreign Origin refused"
OWN = "own Origin accepted"
SEO = "share card and SEO files"
COOKIES = "session cookie flags"
LEAKS = "no response holds a credential or the backend address"


def error(status: int, code: str, headers: dict[str, str] | None = None) -> httpx.Response:
    body = {"error": {"code": code, "message": "m"}}
    return httpx.Response(status, json=body, headers=headers or {})


def good(request: httpx.Request) -> httpx.Response:
    """What a correct deployment answers."""
    url, path = request.url, request.url.path
    if url.scheme == "http":
        return httpx.Response(308, headers={"location": f"https://{url.host}{path}"})
    if path == "/":
        headers = SECURITY | {"content-security-policy": LANDING_CSP}
        return httpx.Response(200, text=OG_PAGE, headers=headers)
    if path == "/dashboard":
        return httpx.Response(307, headers={"location": f"{BASE}/login?next=%2Fdashboard"})
    if path == "/login":
        headers = SECURITY | {"content-security-policy": LOGIN_CSP}
        return httpx.Response(200, text="<html/>", headers=headers)
    if path == "/api/blastproof/v1/auth/me":
        return error(401, "unauthorized", {"x-request-id": "r1", "set-cookie": COOKIE})
    if path.startswith("/api/blastproof/docs"):
        return error(404, "not_found")
    if request.method == "POST":
        if request.headers.get("origin") != BASE:
            return error(403, "bad_origin")
        return error(422, "validation_error")
    if path == "/opengraph-image":
        return httpx.Response(200, content=b"png", headers={"content-type": "image/png"})
    if path in ("/sitemap.xml", "/robots.txt"):
        return httpx.Response(200, text="ok")
    return httpx.Response(404, text="")


def client_for(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def results(handler: Handler, base: str = BASE) -> dict[str, Check]:
    async with client_for(handler) as client:
        return {c.name: c for c in await run_checks(client, base)}


def failing(checks: dict[str, Check]) -> set[str]:
    return {name for name, c in checks.items() if not c.ok}


def with_override(match: Callable[[httpx.Request], bool], answer: Handler) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return answer(request) if match(request) else good(request)

    return handler


def path_is(path: str) -> Callable[[httpx.Request], bool]:
    return lambda r: r.url.path == path and r.url.scheme == "https"


def changed(request: httpx.Request, **headers: str) -> httpx.Response:
    """The good answer with some headers replaced."""
    base = good(request)
    return httpx.Response(base.status_code, text=base.text, headers=dict(base.headers) | headers)


def without(request: httpx.Request, header: str) -> httpx.Response:
    base = good(request)
    kept = {k: v for k, v in base.headers.items() if k != header}
    return httpx.Response(base.status_code, text=base.text, headers=kept)


async def test_every_check_passes_on_a_good_deployment() -> None:
    checks = await results(good)
    assert failing(checks) == set()
    assert len(checks) == 11


CASES: list[tuple[str, Handler, str]] = [
    (
        "landing CSP allows unsafe-eval",
        with_override(
            path_is("/"),
            lambda r: changed(r, **{"content-security-policy": LANDING_CSP + " 'unsafe-eval'"}),
        ),
        LANDING,
    ),
    (
        "landing CSP missing",
        with_override(path_is("/"), lambda r: without(r, "content-security-policy")),
        LANDING,
    ),
    (
        "login X-Frame-Options missing",
        with_override(path_is("/login"), lambda r: without(r, "x-frame-options")),
        NONCE,
    ),
    (
        "no https redirect",
        with_override(lambda r: r.url.scheme == "http", lambda r: httpx.Response(200)),
        HTTPS,
    ),
    (
        "dashboard open without a cookie",
        with_override(path_is("/dashboard"), lambda r: httpx.Response(200)),
        LOGIN_REQUIRED,
    ),
    (
        "login CSP has no nonce",
        with_override(
            path_is("/login"),
            lambda r: changed(r, **{"content-security-policy": LANDING_CSP}),
        ),
        NONCE,
    ),
    (
        "proxy config_error",
        with_override(path_is("/api/blastproof/v1/auth/me"), lambda r: error(500, "config_error")),
        REACHES,
    ),
    (
        "proxy backend_unavailable",
        with_override(
            path_is("/api/blastproof/v1/auth/me"), lambda r: error(502, "backend_unavailable")
        ),
        REACHES,
    ),
    (
        "401 without a request id",
        with_override(path_is("/api/blastproof/v1/auth/me"), lambda r: error(401, "unauthorized")),
        REACHES,
    ),
    (
        "proxy serves /docs",
        with_override(path_is("/api/blastproof/docs"), lambda r: httpx.Response(200, text="docs")),
        V1_ONLY,
    ),
    (
        "foreign Origin accepted",
        with_override(
            lambda r: r.method == "POST" and r.headers.get("origin") != BASE,
            lambda r: error(422, "validation_error"),
        ),
        FOREIGN,
    ),
    (
        "own Origin refused",
        with_override(lambda r: r.method == "POST", lambda r: error(403, "bad_origin")),
        OWN,
    ),
    (
        "OG image 404",
        with_override(path_is("/opengraph-image"), lambda r: httpx.Response(404)),
        SEO,
    ),
    (
        "sitemap 404",
        with_override(path_is("/sitemap.xml"), lambda r: httpx.Response(404)),
        SEO,
    ),
    (
        "og:image on another host",
        with_override(
            path_is("/"),
            lambda r: httpx.Response(
                200, text=OG_PAGE.replace(HOST, "other.example"), headers=good(r).headers
            ),
        ),
        SEO,
    ),
]


@pytest.mark.parametrize(("case", "handler", "check"), CASES, ids=[c[0] for c in CASES])
async def test_each_check_fails_on_its_own_bad_case(
    case: str, handler: Handler, check: str
) -> None:
    assert check in failing(await results(handler))


@pytest.mark.parametrize("missing", ["HttpOnly", "Secure"])
async def test_a_cookie_without_a_flag_fails(missing: str) -> None:
    bad_cookie = COOKIE.replace(f"; {missing}", "")
    handler = with_override(
        path_is("/api/blastproof/v1/auth/me"),
        lambda r: error(401, "unauthorized", {"x-request-id": "r1", "set-cookie": bad_cookie}),
    )
    checks = await results(handler)
    assert COOKIES in failing(checks)
    assert missing.lower() in checks[COOKIES].detail


async def test_the_og_host_check_is_skipped_on_a_vercel_app_url() -> None:
    checks = await results(good, "https://blastproof-abc.vercel.app")
    assert checks[SEO].ok
    assert "skipped" in checks[SEO].detail


LEAK_CASES = [
    ("postgresql://user:hunter2secret@db.example/x", "a database URL with a password"),
    ("bp_" + "a" * 43, "an agent key"),
    ("npg_" + "A" * 20, "a Neon key"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl", "a JWT"),
    ("blastproof.fly.dev", "the backend host"),
]


@pytest.mark.parametrize(("leak", "kind"), LEAK_CASES)
async def test_a_leaked_value_fails_and_is_never_repeated(leak: str, kind: str) -> None:
    in_body = with_override(path_is("/robots.txt"), lambda r: httpx.Response(200, text=leak))
    in_header = with_override(
        path_is("/robots.txt"), lambda r: httpx.Response(200, headers={"x-debug": leak})
    )
    for handler in (in_body, in_header):
        checks = await results(handler)
        assert not checks[LEAKS].ok
        assert kind in checks[LEAKS].detail
        assert leak not in checks[LEAKS].detail


async def test_a_timeout_and_a_connection_error_each_fail_only_their_own_check() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            raise httpx.ReadTimeout("slow")
        if request.url.path == "/dashboard":
            raise httpx.ConnectError("down")
        return good(request)

    checks = await results(slow)
    assert failing(checks) == {NONCE, LOGIN_REQUIRED}
    assert "ReadTimeout" in checks[NONCE].detail


async def test_a_non_json_body_does_not_crash() -> None:
    html = with_override(
        lambda r: r.url.path.startswith("/api/"), lambda r: httpx.Response(502, text="<html>")
    )
    assert {REACHES, V1_ONLY, FOREIGN, OWN} <= failing(await results(html))


async def test_probe_runs_every_check_over_the_network_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = httpx.AsyncClient
    monkeypatch.setattr(
        dashboard_check.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(good), **kw),
    )
    assert all(c.ok for c in await dashboard_check.probe(BASE))


def test_the_command_prints_the_table_and_exits_1_on_a_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_probe(url: str) -> list[Check]:
        return [Check("a", True, "fine"), Check("b", False, "status 500")]

    monkeypatch.setattr(dashboard_check, "probe", fake_probe)
    assert main(["dashboard-check", "--url", BASE]) == 1
    assert capsys.readouterr().out.splitlines() == ["ok   a: fine", "FAIL b: status 500"]


def test_the_command_exits_0_and_reads_no_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_probe(url: str) -> list[Check]:
        return [Check("a", True, "fine")]

    monkeypatch.setattr(dashboard_check, "probe", fake_probe)
    monkeypatch.setattr(
        "app.cli.Settings", lambda *a, **k: pytest.fail("dashboard-check built Settings")
    )
    assert main(["dashboard-check", "--url", BASE]) == 0


def test_the_command_rejects_a_url_without_a_scheme(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["dashboard-check", "--url", "blastproof.in"]) == 1
    assert "must start with https://" in capsys.readouterr().err


def test_the_empty_login_body_the_probe_sends_fails_validation_before_any_attempt() -> None:
    # own-Origin check posts `{}`; the route's body model rejects it, so no login_attempts row.
    from pydantic import ValidationError

    from app.schemas.auth import LoginRequest

    with pytest.raises(ValidationError):
        LoginRequest.model_validate({})


async def test_a_non_307_redirect_to_login_fails() -> None:
    handler = with_override(
        path_is("/dashboard"),
        lambda r: httpx.Response(302, headers={"location": f"{BASE}/login?next=%2Fdashboard"}),
    )
    assert LOGIN_REQUIRED in failing(await results(handler))


async def test_a_refused_own_origin_says_the_origin_is_the_problem() -> None:
    handler = with_override(lambda r: r.method == "POST", lambda r: error(403, "bad_origin"))
    assert "own public origin" in (await results(handler))[OWN].detail


async def test_the_leak_check_fails_when_no_response_came_back() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert LEAKS in failing(await results(down))
