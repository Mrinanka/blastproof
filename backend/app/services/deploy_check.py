"""A read-only probe of a deployed backend's public URL (backend plan §15, docs/deploy.md).

`python -m app.cli deploy-check --url https://blastproof.fly.dev` needs no key and builds no
Settings, so it reads no `.env` value. It only sends requests that change nothing: GETs, one CORS
preflight and one unauthenticated MCP POST that must be refused. A failed check names what is wrong,
never what the server sent back: a leaked value is reported by its kind, not repeated.
"""

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.services.redaction import _AGENT_KEY, _URL_CREDENTIALS

# /ready names one entry per dependency; a production deploy has all five.
READY_COMPONENTS = {"blastproof_db", "company_db", "checkpointer", "workflow", "neon_api"}
# What no public response may contain, by kind. The first two are the redactor's own patterns.
LEAK_PATTERNS = {
    "a database URL with a password": _URL_CREDENTIALS,
    "an agent key": _AGENT_KEY,
    "a Neon key": re.compile(r"npg_[A-Za-z0-9]{8,}"),
    "a Neon host": re.compile(r"[a-z0-9.-]+\.neon\.tech"),
}
TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _is_envelope(response: httpx.Response, status: int) -> bool:
    body = _json(response)
    return (
        response.status_code == status
        and isinstance(body, dict)
        and set(body) == {"error"}
        and isinstance(body["error"], dict)
        and {"code", "message"} <= set(body["error"])
    )


def _has_request_id(response: httpx.Response) -> bool:
    return bool(response.headers.get("x-request-id"))


async def run_checks(client: httpx.AsyncClient, base_url: str) -> list[Check]:
    """Every check, in order. A timeout or connection error fails its check; nothing raises."""
    base = base_url.rstrip("/")
    bodies: list[str] = []  # every body seen, for the leak check
    request_ids: list[bool] = []

    async def send(method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await client.request(method, base + path, **kwargs)
        bodies.append(response.text)
        request_ids.append(_has_request_id(response))
        return response

    async def health() -> tuple[bool, str]:
        response = await send("GET", "/health")
        ok = response.status_code == 200 and _json(response) == {"status": "ok"}
        return ok, f"status {response.status_code}"

    async def ready() -> tuple[bool, str]:
        started = time.perf_counter()
        response = await send("GET", "/ready")
        seconds = time.perf_counter() - started
        body = _json(response)
        checks = body.get("checks") if isinstance(body, dict) else None
        if response.status_code != 200 or not isinstance(checks, dict):
            return False, f"status {response.status_code}"
        down = sorted(READY_COMPONENTS - {k for k, v in checks.items() if v == "ok"})
        if down:
            return False, f"not ok: {', '.join(down)}"
        return True, f"{len(checks)} components ok in {seconds:.2f} s"

    async def https_redirect() -> tuple[bool, str]:
        if not base.startswith("https://"):
            return False, "the URL is not https"
        response = await client.get("http://" + base[len("https://") :] + "/health")
        location = response.headers.get("location", "")
        ok = response.status_code in (301, 302, 307, 308) and location.startswith("https://")
        return ok, f"status {response.status_code}"

    async def not_found_envelope() -> tuple[bool, str]:
        response = await send("GET", "/no-such-route-deploy-check")
        return _is_envelope(response, 404), f"status {response.status_code}"

    async def no_cors() -> tuple[bool, str]:
        response = await send(
            "OPTIONS",
            "/v1/auth/login",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        cors = sorted(k for k in response.headers if k.lower().startswith("access-control-"))
        return not cors, f"{len(cors)} access-control headers"

    async def mcp_needs_a_key() -> tuple[bool, str]:
        response = await send("POST", "/mcp", json={})
        ok = _is_envelope(response, 401) and response.headers.get("www-authenticate") == "Bearer"
        return ok, f"status {response.status_code}"

    async def docs() -> tuple[bool, str]:
        response = await send("GET", "/docs")
        return response.status_code == 200, f"status {response.status_code}"

    probes: list[tuple[str, Callable[[], Awaitable[tuple[bool, str]]]]] = [
        ("health", health),
        ("ready", ready),
        ("http redirects to https", https_redirect),
        ("unknown route is a 404 error envelope", not_found_envelope),
        ("no CORS headers on a preflight", no_cors),
        ("/mcp refuses a call with no key", mcp_needs_a_key),
        ("/docs is served", docs),
    ]
    results: list[Check] = []
    for name, probe in probes:
        try:
            ok, detail = await probe()
        except httpx.HTTPError as exc:
            ok, detail = False, f"request failed ({type(exc).__name__})"
        results.append(Check(name, ok, detail))

    results.append(
        Check(
            "every response has X-Request-ID",
            bool(request_ids) and all(request_ids),
            f"{request_ids.count(False)} of {len(request_ids)} without",
        )
    )
    leaked = sorted(
        {kind for kind, pattern in LEAK_PATTERNS.items() for body in bodies if pattern.search(body)}
    )
    results.append(
        Check(
            "no response holds a credential",
            not leaked,
            f"found {', '.join(leaked)}" if leaked else f"{len(bodies)} bodies scanned",
        )
    )
    return results


async def probe(base_url: str) -> list[Check]:
    # No redirects followed: the https check must see the 301 itself.
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
        return await run_checks(client, base_url)


def render(checks: list[Check]) -> str:
    return "\n".join(f"{'ok  ' if c.ok else 'FAIL'} {c.name}: {c.detail}" for c in checks)
