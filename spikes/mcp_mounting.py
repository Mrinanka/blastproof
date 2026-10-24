"""THROWAWAY SPIKE: how to mount the MCP SDK v2 server inside FastAPI (before step 6).

Not production code. No imports from backend/app, no database, fake keys only.

Run from the repo root:
  uv run --project backend python spikes/mcp_mounting.py probe
  uv run --project backend python spikes/mcp_mounting.py serve --mode stateful   # for Claude Code

Docs: https://py.sdk.modelcontextprotocol.io/v2/migration/ , .../v2/run/asgi/ ,
.../v2/handlers/context/ , https://code.claude.com/docs/en/mcp
"""

import argparse
import asyncio
import contextvars
import json
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import uvicorn
from fastapi import FastAPI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types.version import LATEST_HANDSHAKE_VERSION, LATEST_PROTOCOL_VERSION

KEYS = {"spike-key-a": "agent-a", "spike-key-b": "agent-b"}
current_agent: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_agent", default=None
)
REQUEST_LOG: list[dict[str, Any]] = []
VERBOSE = False
ONLY: list[str] = []


def agent_for(headers: Any) -> str | None:
    auth = (headers or {}).get("authorization", "")
    return KEYS.get(auth.removeprefix("Bearer ").strip()) if auth.startswith("Bearer ") else None


class AgentAuthMiddleware:
    """Pure ASGI (like D-066). Checks the key on /mcp, then hands the agent on two ways."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/mcp"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        entry = {
            "method": scope["method"],
            "path": scope["path"],
            "host": headers.get("host"),
            "protocol_version": headers.get("mcp-protocol-version"),
            "session_id": headers.get("mcp-session-id"),
            "accept": headers.get("accept"),
            "user_agent": headers.get("user-agent"),
        }
        agent = agent_for(headers)
        entry["agent"] = agent
        REQUEST_LOG.append(entry)
        if VERBOSE:
            print("REQ", json.dumps(entry), flush=True)
        if agent is None:
            body = json.dumps(
                {
                    "error": {
                        "code": "not_authenticated",
                        "message": "Invalid agent key.",
                    }
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        scope.setdefault("state", {})["agent"] = agent  # mechanism (b)
        token = current_agent.set(agent)  # mechanism (a)
        try:
            await self.app(scope, receive, send)
        finally:
            current_agent.reset(token)


def build_app(
    *,
    mount: str = "root",
    stateless: bool = False,
    json_response: bool = False,
    with_lifespan: bool = True,
    transport_security: TransportSecuritySettings | None = None,
) -> tuple[FastAPI, MCPServer]:
    mcp = MCPServer(name="spike")

    @mcp.tool()
    async def whoami(ctx: Context) -> dict[str, Any]:
        """Say which agent each mechanism sees."""
        req = ctx.request_context.request
        state = getattr(req, "state", None)
        return {
            "contextvar": current_agent.get(),
            "request_state": getattr(state, "agent", None) if state is not None else None,
            "header_key": agent_for(ctx.headers),
            "session_id": (ctx.headers or {}).get("mcp-session-id"),
            "protocol_version": ctx.protocol_version,
            "request_type": type(req).__name__,
        }

    @mcp.tool()
    async def slow_whoami(seconds: float, ctx: Context) -> dict[str, Any]:
        """Sleep, then report the agent (checks identity across a slow, concurrent call)."""
        await asyncio.sleep(seconds)
        return {"contextvar": current_agent.get(), "header_key": agent_for(ctx.headers)}

    @mcp.tool()
    async def sleep(seconds: float) -> str:
        """Sleep for the given seconds, then reply."""
        start = time.monotonic()
        await asyncio.sleep(seconds)
        return f"slept {time.monotonic() - start:.1f} s"

    @mcp.tool()
    async def boom() -> str:
        """Raises an unexpected error."""
        raise RuntimeError('invalid input syntax: "alice@example.com" postgres://u:pw@h/db')

    @mcp.tool()
    async def anticipated() -> str:
        """Raises a ToolError."""
        raise ToolError("Blocked: a message for the agent.")

    kwargs: dict[str, Any] = {
        "stateless_http": stateless,
        "json_response": json_response,
        "transport_security": transport_security,
    }
    if mount in ("root", "route"):
        mcp_app = mcp.streamable_http_app(**kwargs)
    else:
        mcp_app = mcp.streamable_http_app(streamable_http_path="/", **kwargs)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="spike", lifespan=lifespan if with_lifespan else None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/auth/me")
    async def me() -> dict[str, str]:
        return {"id": "x"}

    if mount == "route":
        # The SDK app (no SDK auth) is one Route("/mcp"); add that route to FastAPI's own router.
        app.router.routes.extend(mcp_app.routes)
    else:
        app.mount("/" if mount == "root" else "/mcp", mcp_app)
    app.add_middleware(AgentAuthMiddleware)
    return app, mcp


# ---------------------------------------------------------------- probe helpers

RESULTS: list[tuple[str, str, str]] = []


def record(q: str, ok: bool | None, detail: str) -> None:
    status = "INFO" if ok is None else ("PASS" if ok else "FAIL")
    RESULTS.append((q, status, detail))
    print(f"[{status}] {q}: {detail}", flush=True)


def init_body(i: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": i,
        "method": "initialize",
        "params": {
            "protocolVersion": LATEST_HANDSHAKE_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "spike", "version": "0"},
        },
    }


def call_body(i: int, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": i,
        "method": "tools/call",
        "params": {"name": name, "arguments": args or {}},
    }


MCP_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


def parse_rpc(resp: httpx2.Response) -> dict[str, Any]:
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        for line in resp.text.splitlines():
            if line.startswith("data:") and line[5:].strip():
                msg = json.loads(line[5:])
                if "id" in msg:
                    return msg
        raise RuntimeError(f"no response in SSE: {resp.text[:200]}")
    return resp.json()


def tool_json(msg: dict[str, Any]) -> Any:
    result = msg["result"]
    if result.get("structuredContent") is not None:
        sc = result["structuredContent"]
        return sc.get("result", sc)
    return json.loads(result["content"][0]["text"])


def key(k: str) -> dict[str, str]:
    return {"authorization": f"Bearer {k}"}


@asynccontextmanager
async def inproc(app: FastAPI, host: str = "localhost:8000") -> AsyncIterator[httpx2.AsyncClient]:
    """App with its lifespan running, reached through httpx2's ASGITransport (no sockets)."""
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url=f"http://{host}", timeout=30
        ) as client:
            yield client


async def legacy_session(client: httpx2.AsyncClient, k: str, path: str = "/mcp") -> str | None:
    r = await client.post(path, json=init_body(), headers={**MCP_HEADERS, **key(k)})
    sid = r.headers.get("mcp-session-id")
    h = {**MCP_HEADERS, **key(k), "mcp-protocol-version": LATEST_HANDSHAKE_VERSION}
    if sid:
        h["mcp-session-id"] = sid
    await client.post(
        path, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h
    )
    return sid


async def legacy_call(
    client: httpx2.AsyncClient,
    k: str,
    sid: str | None,
    name: str,
    args: dict | None = None,
) -> tuple[int, dict[str, Any] | str]:
    h = {**MCP_HEADERS, **key(k), "mcp-protocol-version": LATEST_HANDSHAKE_VERSION}
    if sid:
        h["mcp-session-id"] = sid
    r = await client.post("/mcp", json=call_body(2, name, args), headers=h)
    if r.status_code != 200:
        return r.status_code, r.text[:200]
    return r.status_code, parse_rpc(r)


# ---------------------------------------------------------------- the questions


async def q1_mount() -> None:
    for mount in ("root", "prefix", "route"):
        app, _ = build_app(mount=mount, json_response=True)
        async with inproc(app) as c:
            r = await c.post(
                "/mcp", json=init_body(), headers={**MCP_HEADERS, **key("spike-key-a")}
            )
            r_slash = await c.post(
                "/mcp/", json=init_body(), headers={**MCP_HEADERS, **key("spike-key-a")}
            )
            health = (await c.get("/health")).status_code
            me = (await c.get("/v1/auth/me")).status_code
            docs = (await c.get("/docs")).status_code
            missing = await c.get("/nope")
            spec = (await c.get("/openapi.json")).json()
            mcp_in_spec = any("mcp" in p for p in spec["paths"])
            ok = (
                r.status_code == 200
                and health == 200
                and me == 200
                and not mcp_in_spec
                and missing.headers.get("content-type") == "application/json"
            )
            record(
                f"Q1 mount={mount}",
                ok,
                f"POST /mcp -> {r.status_code} {r.headers.get('location', '')}; "
                f"POST /mcp/ -> {r_slash.status_code} {r_slash.headers.get('location', '')}; "
                f"/health {health}, /v1/auth/me {me}, /docs {docs}, "
                f"/nope {missing.status_code} {missing.text[:60]!r}, mcp in OpenAPI: {mcp_in_spec}",
            )


async def q2_lifespan() -> None:
    app, _ = build_app(with_lifespan=False, json_response=True)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://localhost:8000") as c:
        try:
            r = await c.post(
                "/mcp", json=init_body(), headers={**MCP_HEADERS, **key("spike-key-a")}
            )
            record("Q2 no lifespan", None, f"status {r.status_code} {r.text[:120]!r}")
        except Exception as exc:  # noqa: BLE001
            record("Q2 no lifespan", None, f"raises {type(exc).__name__}: {exc}")

    app, _mcp = build_app(json_response=True)
    async with inproc(app) as c:
        r = await c.post("/mcp", json=init_body(), headers={**MCP_HEADERS, **key("spike-key-a")})
        record("Q2 with lifespan", r.status_code == 200, f"initialize -> {r.status_code}")
    try:
        async with app.router.lifespan_context(app):
            pass
        record("Q2 run() twice on one server", None, "second lifespan worked")
    except RuntimeError as exc:
        record("Q2 run() twice on one server", None, f"RuntimeError: {exc}")


async def q4_identity() -> None:
    for label, stateless, json_resp in (
        ("stateful+sse", False, False),
        ("stateful+json", False, True),
        ("stateless+sse", True, False),
        ("stateless+json", True, True),
    ):
        app, _ = build_app(stateless=stateless, json_response=json_resp)
        async with inproc(app) as c:
            sid_a = await legacy_session(c, "spike-key-a")
            sid_b = await legacy_session(c, "spike-key-b")
            seen = []
            for k, sid, want in (
                ("spike-key-a", sid_a, "agent-a"),
                ("spike-key-b", sid_b, "agent-b"),
                ("spike-key-a", sid_a, "agent-a"),
            ):
                status, msg = await legacy_call(c, k, sid, "whoami")
                seen.append((want, status, tool_json(msg) if status == 200 else msg))
            # Key B on agent A's session (stateful only: stateless has no session id).
            cross = None
            if sid_a:
                status, msg = await legacy_call(c, "spike-key-b", sid_a, "whoami")
                cross = (status, tool_json(msg) if status == 200 else msg)

            # Concurrent: A and B slow calls overlapping.
            async def slow(k: str, sid: str | None) -> Any:
                h = {
                    **MCP_HEADERS,
                    **key(k),
                    "mcp-protocol-version": LATEST_HANDSHAKE_VERSION,
                }
                if sid:
                    h["mcp-session-id"] = sid
                r = await c.post(
                    "/mcp",
                    json=call_body(3, "slow_whoami", {"seconds": 0.5}),
                    headers=h,
                )
                return tool_json(parse_rpc(r))

            conc = await asyncio.gather(slow("spike-key-a", sid_a), slow("spike-key-b", sid_b))

        for mech in ("contextvar", "request_state", "header_key"):
            ok = all(isinstance(s, dict) and s.get(mech) == w for w, _, s in seen)
            record(
                f"Q4 {label} via {mech}",
                ok,
                "sequence saw "
                + ", ".join(
                    str(s.get(mech)) if isinstance(s, dict) else str(s) for _, _, s in seen
                ),
            )
        record(
            f"Q4 {label} concurrent",
            conc[0]["contextvar"] == "agent-a" and conc[1]["contextvar"] == "agent-b",
            f"A saw {conc[0]}, B saw {conc[1]}",
        )
        record(
            f"Q4 {label} session ids",
            None,
            f"A={sid_a} B={sid_b}; first whoami: {seen[0][2]}",
        )
        if cross is not None:
            record(f"Q4 {label} key B on A's session", cross[0] != 200, f"{cross}")


async def q4_modern() -> None:
    """The 2026-07-28 per-request path, through the SDK Client (mode='auto' probes discover)."""
    for label, stateless, json_resp in (
        ("stateful+sse", False, False),
        ("stateless+json", True, True),
    ):
        app, _ = build_app(stateless=stateless, json_response=json_resp)
        async with app.router.lifespan_context(app):
            results = {}
            for k in ("spike-key-a", "spike-key-b"):
                hc = httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url="http://localhost:8000",
                    headers=key(k),
                    timeout=30,
                )
                async with hc:
                    REQUEST_LOG.clear()
                    try:
                        async with asyncio.timeout(15):
                            async with Client(
                                streamable_http_client("http://localhost:8000/mcp", http_client=hc)
                            ) as client:
                                res = await client.call_tool("whoami", {})
                                results[k] = res.structured_content
                                pv = (
                                    client.protocol_version
                                    if hasattr(client, "protocol_version")
                                    else "?"
                                )
                    except Exception as exc:  # noqa: BLE001
                        results[k] = f"{type(exc).__name__}: {exc}"
                        pv = "?"
                    methods = [
                        (e["method"], e["protocol_version"], e["session_id"]) for e in REQUEST_LOG
                    ]
            ok = all(
                isinstance(results[k], dict)
                and results[k].get("contextvar") == KEYS[k]
                and results[k].get("request_state") == KEYS[k]
                for k in results
            )
            record(
                f"Q8/Q4 SDK Client auto mode, {label}",
                ok,
                f"{results}; pv={pv}; requests={methods}",
            )


async def q5_auth() -> None:
    app, _ = build_app(json_response=True)
    async with inproc(app) as c:
        for label, h in (("missing key", {}), ("wrong key", key("bp_wrong"))):
            r = await c.post("/mcp", json=init_body(), headers={**MCP_HEADERS, **h})
            record(
                f"Q5 {label}",
                r.status_code == 401,
                f"{r.status_code} {r.headers.get('www-authenticate')} {r.text}",
            )
        r = await c.get("/.well-known/oauth-protected-resource")
        record(
            "Q5 OAuth metadata route",
            None,
            f"GET /.well-known/oauth-protected-resource -> {r.status_code}",
        )


async def q6_hosts() -> None:
    hosts = ["localhost:8000", "127.0.0.1:8000", "localhost", "testserver", "x.fly.dev"]
    for label, ts in (
        ("default", None),
        (
            "allowlist",
            TransportSecuritySettings(
                allowed_hosts=["localhost:*", "127.0.0.1:*", "x.fly.dev"],
                allowed_origins=[],
            ),
        ),
    ):
        app, _ = build_app(json_response=True, transport_security=ts)
        out = []
        async with app.router.lifespan_context(app):
            for host in hosts:
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url=f"http://{host}"
                ) as c:
                    r = await c.post(
                        "/mcp",
                        json=init_body(),
                        headers={**MCP_HEADERS, **key("spike-key-a")},
                    )
                    out.append(f"{host}={r.status_code}")
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://localhost:8000",
            ) as c:
                r = await c.post(
                    "/mcp",
                    json=init_body(),
                    headers={
                        **MCP_HEADERS,
                        **key("spike-key-a"),
                        "origin": "https://evil.example",
                    },
                )
                out.append(f"origin evil={r.status_code}")
        record(f"Q6 hosts, {label}", None, ", ".join(out))


async def q9_errors() -> None:
    app, _ = build_app(json_response=True)
    async with inproc(app) as c:
        sid = await legacy_session(c, "spike-key-a")
        for name in ("boom", "anticipated"):
            status, msg = await legacy_call(c, "spike-key-a", sid, name)
            result = msg.get("result", msg) if isinstance(msg, dict) else msg
            leaked = "alice@example.com" in json.dumps(msg) or "pw@" in json.dumps(msg)
            record(f"Q9 {name}", not leaked, f"{status} {json.dumps(result)[:200]}")


async def q8_socket() -> None:
    """The same SDK Client over a real socket (uvicorn), legacy and auto mode."""
    app, _ = build_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=8766, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(200):  # up to 10 s for uvicorn to bind
        if server.started:
            break
        await asyncio.sleep(0.05)
    try:
        for mode in ("auto", "legacy"):
            REQUEST_LOG.clear()
            async with (
                httpx2.AsyncClient(headers=key("spike-key-b"), timeout=30) as hc,
                Client(
                    streamable_http_client("http://localhost:8766/mcp", http_client=hc),
                    mode=mode,
                ) as client,
            ):
                res = await client.call_tool("whoami", {})
            methods = [(e["method"], e["protocol_version"], e["session_id"]) for e in REQUEST_LOG]
            record(
                f"Q8 socket client mode={mode}",
                res.structured_content.get("contextvar") == "agent-b",
                f"{res.structured_content}; requests={methods}",
            )
    finally:
        server.should_exit = True
        await task


async def q7_keepalive() -> None:
    """A slow 2026-07-28 call over a socket: does the server send bytes while the tool runs?

    Fly's proxy closes a connection after about 60 s with no bytes; the long poll waits 50 s.
    """
    body = call_body(7, "sleep", {"seconds": 35})
    body["params"]["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "spike", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    headers = {
        **MCP_HEADERS,
        **key("spike-key-a"),
        "mcp-protocol-version": "2026-07-28",
        "mcp-method": "tools/call",
        "mcp-name": "sleep",
    }
    for label, json_resp in (("stateless+sse", False), ("stateless+json", True)):
        app, _ = build_app(mount="route", stateless=True, json_response=json_resp)
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=8767, log_level="warning")
        )
        task = asyncio.create_task(server.serve())
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.05)
        try:
            chunks: list[str] = []
            start = time.monotonic()
            async with (
                httpx2.AsyncClient(timeout=60) as c,
                c.stream("POST", "http://localhost:8767/mcp", json=body, headers=headers) as r,
            ):
                ctype = r.headers.get("content-type")
                async for chunk in r.aiter_text():
                    chunks.append(f"{time.monotonic() - start:.1f}s:{chunk.strip()[:300]!r}")
            longest_gap = 0.0
            times = [0.0] + [float(c.split("s:")[0]) for c in chunks]
            longest_gap = max(b - a for a, b in zip(times, times[1:], strict=False))
            record(
                f"Q7 keepalive {label}",
                r.status_code == 200 and "slept" in "".join(chunks) and longest_gap < 20,
                f"{r.status_code} {ctype}; longest silence {longest_gap:.1f} s; chunks={chunks}",
            )
        finally:
            server.should_exit = True
            await task


async def probe() -> None:
    print(
        f"LATEST_PROTOCOL_VERSION={LATEST_PROTOCOL_VERSION}, "
        f"LATEST_HANDSHAKE_VERSION={LATEST_HANDSHAKE_VERSION}"
    )
    for fn in (
        q1_mount,
        q2_lifespan,
        q4_identity,
        q4_modern,
        q5_auth,
        q6_hosts,
        q9_errors,
        q8_socket,
        q7_keepalive,
    ):
        if ONLY and fn.__name__ not in ONLY:
            continue
        print(f"--- {fn.__name__}", flush=True)
        try:
            await fn()
        except Exception as exc:  # noqa: BLE001
            record(fn.__name__, False, f"crashed: {type(exc).__name__}: {exc}")
    print("\n=== summary")
    for q, status, _ in RESULTS:
        print(f"{status:4}  {q}")


def main() -> None:
    global VERBOSE
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("only", nargs="*", help="run only these question functions, e.g. q1_mount")
    s = sub.add_parser("serve")
    s.add_argument("--mode", choices=["stateful", "stateless", "json"], default="stateful")
    s.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.cmd == "probe":
        ONLY.extend(args.only)
        asyncio.run(probe())
        return
    VERBOSE = True
    app, _ = build_app(
        mount="route",
        stateless=args.mode in ("stateless", "json"),
        json_response=args.mode == "json",
    )
    print(f"serving mode={args.mode} on 127.0.0.1:{args.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    sys.exit(main())
