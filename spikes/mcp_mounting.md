# Spike: MCP mounting (2026-10-25)

Throwaway code: `spikes/mcp_mounting.py` (no `backend/app` imports, no database, fake keys
`spike-key-a` / `spike-key-b`). Versions: `mcp` 2.3.0, `fastapi` 0.162.0, `uvicorn` 0.54.0,
`starlette` 1.10.0, `httpx2` 2.16.0, Claude Code 2.1.310.

Run from the repo root:
- `uv run --project backend python spikes/mcp_mounting.py probe` (scripted checks, about 10 s)
- `uv run --project backend python spikes/mcp_mounting.py serve --mode stateful|stateless|json`
  then `claude -p "..." --mcp-config spikes/mcp-spike.json --strict-mcp-config --allowedTools
  mcp__spike__whoami mcp__spike__sleep` (the real Claude Code checks; nothing is written to
  `~/.claude.json`).

Docs: SDK v2 migration guide (https://py.sdk.modelcontextprotocol.io/v2/migration/), ASGI mounting
(.../v2/run/asgi/), Context (.../v2/handlers/context/), protocol versions (.../v2/protocol-versions/),
Claude Code MCP (https://code.claude.com/docs/en/mcp).

## Answers

| # | Question | Answer (from the output below) |
| --- | --- | --- |
| 1 | Mount path | Neither `Mount` works cleanly. `app.mount("/", ...)` serves `POST /mcp` with 200 but takes over every unmatched path, so FastAPI's 404 handler never runs (`/nope` → plain-text `Not Found`). `app.mount("/mcp", ..., streamable_http_path="/")` redirects `POST /mcp` with 307 to `/mcp/`. **Works:** take the SDK app's single `Route("/mcp")` and add it to FastAPI's router (`app.router.routes.extend(mcp_app.routes)`): `POST /mcp` 200, FastAPI JSON 404s, `/docs` fine, nothing MCP in OpenAPI. |
| 2 | Lifespan | Without `session_manager.run()` the request raises `RuntimeError: Task group is not initialized`. `run()` is **single-use per server instance** (second run raises), so step 6 must build the `MCPServer` inside `create_app()`, not at import, because tests build many apps. |
| 3 | Mode and protocol | Claude Code 2.1.310 speaks the **2026-07-28** per-request protocol: every request carried `mcp-protocol-version: 2026-07-28` and **no `Mcp-Session-Id`**, against both a stateful and a stateless server. The SDK routes 2026-07-28 requests before the stateful/stateless switch, so that setting only affects older (handshake) clients. So for Claude Code, `session_id` is always null. |
| 4 | How a tool gets the agent | All three mechanisms saw the key sent on **that** request in every mode, sequential and concurrent: (a) contextvar set in pure-ASGI middleware, (b) `scope["state"]` read as `ctx.request_context.request.state`, (c) re-checking `ctx.headers`. My hypothesis that (a) would go stale in stateful mode was wrong. **But:** in stateful mode a session opened with key A accepted key B (200, tool saw agent B). Harmless for identity (the tool sees B) but sessions are not bound to agents. Stateless mode has no sessions, so the problem cannot arise. |
| 5 | Auth response | Our middleware's 401 with `WWW-Authenticate: Bearer` and the error envelope works. Claude Code with a wrong key reports `AUTH_HEADER_REJECTED`, HTTP 401, shows our message, and does not fall back to OAuth because an `Authorization` header is configured. It tried 2026-07-28 first, then a handshake `initialize`. No OAuth route exists (`/.well-known/oauth-protected-resource` → 404). SDK `TokenVerifier`/`AuthSettings` not needed. |
| 6 | DNS-rebinding protection | On by default for mounted apps. Passes: `localhost:8000`, `127.0.0.1:8000` (port wildcard). Rejected with 421: `localhost` (no port), `testserver` (Starlette's TestClient default), `x.fly.dev`. A foreign `Origin` gets 403. With `TransportSecuritySettings(allowed_hosts=[..., "x.fly.dev"])` the Fly name passes (Fly sends no port on 443). |
| 7 | Timeouts | Through Claude Code, `sleep(25)` and `sleep(55)` returned in stateful+SSE mode, and `sleep(55)` in stateless+JSON mode. Docs: tool wall-clock default about 28 h (`MCP_TOOL_TIMEOUT`), idle timeout 5 min for HTTP servers, calls over 2 min move to the background in interactive mode. Claude Code is fine with 20 s and 50 s waits. **The proxy is the limit:** Fly's proxy closes a connection with no bytes for about 60 s (Fly community answers, not official docs). In SSE mode the SDK sends `: ping` every 15 s while a tool runs (longest silence 15.4 s on a 35 s call); in JSON mode nothing is sent until the result (35.3 s of silence). So use SSE mode. |
| 8 | In-process tests | Work with no sockets: `httpx2.ASGITransport` plus `app.router.lifespan_context(app)`, both raw JSON-RPC and the SDK `Client(streamable_http_client(url, http_client=...))` in auto (2026-07-28) mode. Base URL must be `http://localhost:8000` (or an allowlisted host), not `testserver`. The SDK client in legacy mode over a real socket also works. |
| 9 | Tool errors | An unexpected exception reaches the agent only as `Error executing tool boom` (`isError: true`); the raw text is withheld. **But the SDK logs the full traceback, including the raw exception text** (`alice@example.com`, a URL with a password), through stdlib logging at ERROR. A `ToolError` message reaches the agent prefixed: `Error executing tool anticipated: <message>`. |

## What differs from the plan, and proposed edits

1. **Mount** (BE step 6 "mount it so the public path is `/mcp`"): add the SDK's `/mcp` route to
   FastAPI's router instead of `app.mount`. Keeps CLAUDE.md's error envelope for 404s (step 19).
2. **Build per app:** create the `MCPServer` and call `streamable_http_app()` inside `create_app()`;
   our lifespan enters `mcp.session_manager.run()` around the existing startup/shutdown.
3. **Mode:** `stateless_http=True`, `json_response=False` (SSE, the default). Claude Code uses
   2026-07-28 either way; stateless removes cross-agent session reuse for older clients and keeps
   no per-session memory; SSE keeps a 50 s long poll alive behind Fly's 60 s idle cut (15 s pings).
   (First draft said JSON; the Q7 keep-alive probe changed it.) A call that finishes within 15 s
   still gets a plain JSON response. Server-to-client requests (sampling, elicitation) are not
   used.
4. **`session_id`** (BE §6): with Claude Code it is always null; say so. Keep the column (Phase 2).
5. **Identity:** keep the plan's contextvar (it passed every check), and also put the agent in
   `scope["state"]`; tools read the contextvar and fail closed if it is unset. Step 6 tests: two
   agents interleaved and concurrent, and a key change mid-"session", through the real middleware.
6. **Transport security:** build `TransportSecuritySettings` from config: `localhost:*`,
   `127.0.0.1:*` plus the host of `PUBLIC_BASE_URL` in production. Tests use
   `http://localhost:8000` as base URL.
7. **Errors:** tools catch their own expected failures and *return* the `agent_messages.py` text
   (not `ToolError`, whose prefix would break the word-for-word templates). Unexpected exceptions
   are withheld from the agent by the SDK, but its stdlib `mcp` logger writes the raw exception;
   step 6 must route or silence that logger (catch in the tool, log a masked `reason_code` through
   structlog) so raw values never reach logs. This extends the existing Known issue on exception
   text in logs.
8. **Timeout:** the 50 s long poll and 20 s wait are fine; no plan change. Note the 5-minute idle
   limit as the hard ceiling for any future wait.

## Real output

`probe` (2026-10-25, all result lines; `FAIL` lines are the rejected options, not spike errors):

```text
LATEST_PROTOCOL_VERSION=2026-07-28, LATEST_HANDSHAKE_VERSION=2025-11-25
[FAIL] Q1 mount=root: POST /mcp -> 200 ; POST /mcp/ -> 307 http://localhost:8000/mcp; /health 200, /v1/auth/me 200, /docs 200, /nope 404 'Not Found', mcp in OpenAPI: False
[FAIL] Q1 mount=prefix: POST /mcp -> 307 http://localhost:8000/mcp/; POST /mcp/ -> 200 ; /health 200, /v1/auth/me 200, /docs 200, /nope 404 '{"detail":"Not Found"}', mcp in OpenAPI: False
[PASS] Q1 mount=route: POST /mcp -> 200 ; POST /mcp/ -> 307 http://localhost:8000/mcp; /health 200, /v1/auth/me 200, /docs 200, /nope 404 '{"detail":"Not Found"}', mcp in OpenAPI: False
[INFO] Q2 no lifespan: raises RuntimeError: Task group is not initialized. Make sure to use run().
[PASS] Q2 with lifespan: initialize -> 200
[INFO] Q2 run() twice on one server: RuntimeError: StreamableHTTPSessionManager .run() can only be called once per instance. Create a new instance if you need to run again.
[PASS] Q4 stateful+sse via contextvar: sequence saw agent-a, agent-b, agent-a
[PASS] Q4 stateful+sse via request_state: sequence saw agent-a, agent-b, agent-a
[PASS] Q4 stateful+sse via header_key: sequence saw agent-a, agent-b, agent-a
[PASS] Q4 stateful+sse concurrent: A saw {'contextvar': 'agent-a', 'header_key': 'agent-a'}, B saw {'contextvar': 'agent-b', 'header_key': 'agent-b'}
[INFO] Q4 stateful+sse session ids: A=58b595f369ba470d956f2fbcb9cd21b2 B=31b5b4bff54c4b52941d861ec0a88c6c; first whoami: {..., 'session_id': '58b595f369ba470d956f2fbcb9cd21b2', 'protocol_version': '2025-11-25', 'request_type': 'Request'}
[FAIL] Q4 stateful+sse key B on A's session: (200, {'contextvar': 'agent-b', 'request_state': 'agent-b', 'header_key': 'agent-b', 'session_id': '58b595f369ba470d956f2fbcb9cd21b2', ...})
[PASS] Q4 stateful+json via contextvar / request_state / header_key: sequence saw agent-a, agent-b, agent-a
[PASS] Q4 stateful+json concurrent: A saw agent-a, B saw agent-b
[FAIL] Q4 stateful+json key B on A's session: (200, {'contextvar': 'agent-b', ..., 'session_id': '77fad5caa944484baee7ae3f8a42cfb4'})
[PASS] Q4 stateless+sse via contextvar / request_state / header_key: sequence saw agent-a, agent-b, agent-a
[PASS] Q4 stateless+sse concurrent: A saw agent-a, B saw agent-b
[INFO] Q4 stateless+sse session ids: A=None B=None
[PASS] Q4 stateless+json via contextvar / request_state / header_key: sequence saw agent-a, agent-b, agent-a
[PASS] Q4 stateless+json concurrent: A saw agent-a, B saw agent-b
[INFO] Q4 stateless+json session ids: A=None B=None
[PASS] Q8/Q4 SDK Client auto mode, stateful+sse: agent-a saw agent-a, agent-b saw agent-b; session_id None; protocol_version 2026-07-28; requests=[('POST', '2026-07-28', None) x3]
[PASS] Q8/Q4 SDK Client auto mode, stateless+json: same
[PASS] Q5 missing key: 401 Bearer {"error": {"code": "not_authenticated", "message": "Invalid agent key."}}
[PASS] Q5 wrong key: 401 Bearer {"error": {"code": "not_authenticated", "message": "Invalid agent key."}}
[INFO] Q5 OAuth metadata route: GET /.well-known/oauth-protected-resource -> 404
[INFO] Q6 hosts, default: localhost:8000=200, 127.0.0.1:8000=200, localhost=421, testserver=421, x.fly.dev=421, origin evil=403
[INFO] Q6 hosts, allowlist: localhost:8000=200, 127.0.0.1:8000=200, localhost=421, testserver=421, x.fly.dev=200, origin evil=403
[PASS] Q9 boom: 200 {"content": [{"text": "Error executing tool boom", "type": "text"}], "isError": true}
[PASS] Q9 anticipated: 200 {"content": [{"text": "Error executing tool anticipated: Blocked: a message for the agent.", "type": "text"}], "isError": true}
[PASS] Q8 socket client mode=auto: agent-b; session_id None; protocol_version 2026-07-28
[PASS] Q8 socket client mode=legacy: agent-b; session_id '4776a754...'; protocol_version 2025-11-25; requests POST initialize, GET stream, POST x2, DELETE
```

(Repeated dict fields shortened with `...`; the full lines are printed by the probe.)

The server log during Q9 (stdlib `mcp` logger), showing the raw text that must never reach our logs:

```text
Tool 'boom' raised an unexpected exception
...
RuntimeError: invalid input syntax: "alice@example.com" postgres://u:pw@h/db
```

Claude Code against `serve --mode stateful`, `whoami` then `sleep 25` then `sleep 55` (exit 0, 93 s):

```text
{"contextvar":"agent-a","request_state":"agent-a","header_key":"agent-a","session_id":null,"protocol_version":"2026-07-28","request_type":"Request"}
{"result":"slept 25.0 s"}
{"result":"slept 55.0 s"}
```

Server log (8 requests, all alike):

```text
REQ {"method": "POST", "path": "/mcp", "host": "localhost:8765", "protocol_version": "2026-07-28", "session_id": null, "accept": "application/json, text/event-stream", "user_agent": "claude-code/2.1.310 (sdk-cli)", "agent": "agent-a"}
```

Claude Code with `spikes/mcp-spike-wrong-key.json` (its own words, trimmed):

```text
The spike MCP server is configured, but it failed to connect when the session started.
Connection error (spike): AUTH_HEADER_REJECTED, HTTP 401. The server rejected the configured
Authorization header with: { error : { code : not_authenticated , message : Invalid agent key. }}
Because headers.Authorization is set in the config, Claude Code won't fall back to OAuth.
```

```text
REQ {"method": "POST", "path": "/mcp", "protocol_version": "2026-07-28", "session_id": null, "agent": null, ...}
REQ {"method": "POST", "path": "/mcp", "protocol_version": null, "session_id": null, "agent": null, ...}
```

Claude Code against `serve --mode stateless`, `whoami` (exit 0): the same result line as above;
6 requests, all `2026-07-28`, no session id.

Claude Code against `serve --mode stateless` (stateless + SSE, the recommended mode), `whoami`
then `sleep 55` (exit 0, 74 s; 7 requests, all `2026-07-28`, no session id):

```text
{"contextvar":"agent-a","request_state":"agent-a","header_key":"agent-a","session_id":null,"protocol_version":"2026-07-28","request_type":"Request"}
{"result":"slept 55.0 s"}
```

Claude Code against `serve --mode json` (stateless + JSON), `whoami` then `sleep 55` (exit 0, 67 s):

```text
{"contextvar":"agent-a","request_state":"agent-a","header_key":"agent-a","session_id":null,"protocol_version":"2026-07-28","request_type":"Request"}
{"result":"slept 55.0 s"}
```

`probe q7_keepalive`: a 2026-07-28 `tools/call` of `sleep(35)` over a real socket, each chunk
timed (the `FAIL` is the rejected JSON mode):

```text
[PASS] Q7 keepalive stateless+sse: 200 text/event-stream; longest silence 15.4 s; chunks=["15.4s:': ping'", "30.4s:': ping'", '35.4s:\'event: message\r\ndata: {"jsonrpc":"2.0","id":7,"result":{... "slept 35.0 s" ...}}\'']
[FAIL] Q7 keepalive stateless+json: 200 application/json; longest silence 35.3 s; chunks=['35.3s:\'{"jsonrpc":"2.0","id":7,"result":{... "slept 35.0 s" ...}}\'']
```

Last full `probe` run: 27 PASS, 9 INFO, 5 FAIL (the five rejected options: two mount forms,
key B on A's session in both stateful modes, JSON mode keep-alive).
