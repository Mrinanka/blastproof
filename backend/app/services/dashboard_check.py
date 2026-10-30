"""A read-only probe of the deployed dashboard (frontend plan §13, docs/deploy-dashboard.md).

`python -m app.cli dashboard-check --url https://blastproof.in` needs no key and builds no Settings,
so it reads no `.env` value. It sends GETs, plus POSTs that must be refused or rejected as invalid
before they change anything. A failed check names what is wrong, never what the server sent back: a
leaked value is reported by its kind, not repeated.
"""

import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.services.deploy_check import LEAK_PATTERNS, Check, render

__all__ = ["Check", "probe", "render", "run_checks"]

TIMEOUT_SECONDS = 10.0
REDIRECTS = (301, 302, 307, 308)
FOREIGN_ORIGIN = "https://evil.example"
# On top of the backend's patterns: a session token, and the backend's own host.
DASHBOARD_LEAK_PATTERNS = LEAK_PATTERNS | {
    "a JWT": re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    "the backend host": re.compile(r"[a-z0-9.-]+\.fly\.dev"),
}


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _error_code(response: httpx.Response) -> str | None:
    """The `error.code` of a `{"error": {code, message}}` body, or None for any other body."""
    body = _json(response)
    error = body.get("error") if isinstance(body, dict) and set(body) == {"error"} else None
    if isinstance(error, dict) and {"code", "message"} <= set(error):
        return str(error["code"])
    return None


def _csp(response: httpx.Response) -> str:
    return response.headers.get("content-security-policy", "")


def _header_problems(response: httpx.Response) -> list[str]:
    problems = []
    if response.headers.get("x-frame-options", "").upper() != "DENY":
        problems.append("X-Frame-Options is not DENY")
    if not response.headers.get("referrer-policy"):
        problems.append("Referrer-Policy is missing")
    if response.headers.get("x-content-type-options", "").lower() != "nosniff":
        problems.append("X-Content-Type-Options is not nosniff")
    csp = _csp(response)
    if not csp:
        problems.append("Content-Security-Policy is missing")
    else:
        if "'unsafe-eval'" in csp:
            problems.append("the CSP allows unsafe-eval")
        if "frame-ancestors 'none'" not in csp:
            problems.append("the CSP lacks frame-ancestors 'none'")
    return problems


def _cookie_problems(cookies: list[str]) -> list[str]:
    problems = set()
    for cookie in cookies:
        attributes = {part.strip().lower() for part in cookie.split(";")[1:]}
        for flag in ("httponly", "secure", "samesite=lax", "path=/"):
            if flag not in attributes:
                problems.add(flag)
    return sorted(problems)


def _outcome(problems: list[str], ok_detail: str) -> tuple[bool, str]:
    return (not problems, "; ".join(problems) if problems else ok_detail)


async def run_checks(client: httpx.AsyncClient, base_url: str) -> list[Check]:
    """Every check, in order. A timeout or connection error fails its check; nothing raises."""
    base = base_url.rstrip("/")
    host = urlsplit(base).netloc
    bodies: list[str] = []  # every body and header seen, for the leak check
    cookies: list[str] = []

    async def send(method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await client.request(method, base + path, **kwargs)
        bodies.append(response.text)
        bodies.append(" ".join(f"{k}: {v}" for k, v in response.headers.items()))
        cookies.extend(response.headers.get_list("set-cookie"))
        return response

    async def landing_headers() -> tuple[bool, str]:
        response = await send("GET", "/")
        problems = [] if response.status_code == 200 else [f"status {response.status_code}"]
        if "script-src 'self' 'unsafe-inline'" not in _csp(response):
            problems.append("the landing CSP lacks script-src 'self' 'unsafe-inline'")
        return _outcome(problems + _header_problems(response), "status 200, headers set")

    async def https_redirect() -> tuple[bool, str]:
        if not base.startswith("https://"):
            return False, "the URL is not https"
        response = await client.get("http://" + base[len("https://") :] + "/")
        location = response.headers.get("location", "")
        ok = response.status_code in REDIRECTS and location.startswith("https://")
        return ok, f"status {response.status_code}"

    async def dashboard_needs_login() -> tuple[bool, str]:
        response = await send("GET", "/dashboard")
        location = response.headers.get("location", "")
        ok = (
            response.status_code == 307
            and urlsplit(location).path == "/login"
            and "next=%2Fdashboard" in location
        )
        return ok, f"status {response.status_code}"

    async def login_nonce_csp() -> tuple[bool, str]:
        response = await send("GET", "/login")
        problems = [] if response.status_code == 200 else [f"status {response.status_code}"]
        csp = _csp(response)
        if "'nonce-" not in csp or "'strict-dynamic'" not in csp:
            problems.append("the login CSP has no nonce and strict-dynamic")
        return _outcome(problems + _header_problems(response), "status 200, nonce CSP")

    async def proxy_reaches_backend() -> tuple[bool, str]:
        response = await send("GET", "/api/blastproof/v1/auth/me")
        code = _error_code(response)
        if response.status_code == 401 and code and response.headers.get("x-request-id"):
            return True, "401 with a backend request id"
        if response.status_code == 500 and code == "config_error":
            return False, "a dashboard environment variable is missing"
        if response.status_code in (502, 504):
            return False, f"status {response.status_code}: BLASTPROOF_API_URL is wrong or down"
        return False, f"status {response.status_code}, not the backend's answer"

    async def proxy_v1_only() -> tuple[bool, str]:
        response = await send("GET", "/api/blastproof/docs")
        ok = response.status_code == 404 and _error_code(response) == "not_found"
        return ok, f"status {response.status_code}"

    async def foreign_origin_refused() -> tuple[bool, str]:
        problems = []
        for path in ("/api/auth/login", "/api/blastproof/v1/agents"):
            response = await send("POST", path, headers={"Origin": FOREIGN_ORIGIN}, json={})
            if response.status_code != 403 or _error_code(response) != "bad_origin":
                problems.append(f"{path} answered {response.status_code}")
        return _outcome(problems, "both refused with bad_origin")

    async def own_origin_accepted() -> tuple[bool, str]:
        # `{}` fails the backend's validation before any login attempt is recorded.
        response = await send("POST", "/api/auth/login", headers={"Origin": base}, json={})
        if response.status_code == 403:
            return False, "403: the dashboard does not see its own public origin"
        ok = response.status_code == 422 and _error_code(response) is not None
        return ok, f"status {response.status_code}"

    async def share_card_and_seo() -> tuple[bool, str]:
        problems = []
        image = await send("GET", "/opengraph-image")
        if image.status_code != 200 or not image.headers.get("content-type", "").startswith(
            "image/png"
        ):
            problems.append(f"/opengraph-image answered {image.status_code}")
        for path in ("/sitemap.xml", "/robots.txt"):
            response = await send("GET", path)
            if response.status_code != 200:
                problems.append(f"{path} answered {response.status_code}")
        if host.endswith(".vercel.app"):
            return _outcome(problems, "og:image host skipped on a vercel.app URL")
        page = await send("GET", "/")
        tag = next(iter(re.findall(r"<meta[^>]*property=\"og:image\"[^>]*>", page.text)), "")
        content = re.search(r'content="([^"]+)"', tag)
        if not content or urlsplit(content.group(1)).netloc != host:
            problems.append("og:image does not point at this host")
        return _outcome(problems, "image, sitemap, robots and og:image host ok")

    async def cookie_flags() -> tuple[bool, str]:
        problems = _cookie_problems(cookies)
        if problems:
            return False, f"a cookie lacks {', '.join(problems)}"
        return True, f"{len(cookies)} cookies checked"

    probes: list[tuple[str, Callable[[], Awaitable[tuple[bool, str]]]]] = [
        ("landing page headers", landing_headers),
        ("http redirects to https", https_redirect),
        ("dashboard needs login", dashboard_needs_login),
        ("login page has a nonce CSP", login_nonce_csp),
        ("proxy reaches the backend", proxy_reaches_backend),
        ("proxy forwards /v1 only", proxy_v1_only),
        ("foreign Origin refused", foreign_origin_refused),
        ("own Origin accepted", own_origin_accepted),
        ("share card and SEO files", share_card_and_seo),
        # Last of the probes, so every cookie they set is checked.
        ("session cookie flags", cookie_flags),
    ]
    results: list[Check] = []
    for name, check in probes:
        try:
            ok, detail = await check()
        except httpx.HTTPError as exc:
            ok, detail = False, f"request failed ({type(exc).__name__})"
        results.append(Check(name, ok, detail))

    leaked = sorted(
        {
            kind
            for kind, pattern in DASHBOARD_LEAK_PATTERNS.items()
            for body in bodies
            if pattern.search(body)
        }
    )
    results.append(
        Check(
            "no response holds a credential or the backend address",
            bool(bodies) and not leaked,
            f"found {', '.join(leaked)}"
            if leaked
            else (f"{len(bodies)} bodies scanned" if bodies else "no response to scan"),
        )
    )
    return results


async def probe(base_url: str) -> list[Check]:
    # No redirects followed: the checks must see the 301 and 307 themselves.
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
        return await run_checks(client, base_url)
