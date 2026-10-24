# Design choices guide

The two most important design choices in each build step, in plain language, the way I would
explain them in an interview: what I chose, why, what I rejected, and the honest limits.
The formal record (with D-numbers) is `docs/decisions.md`; this file is the spoken version.

---

## Step 1: Repo and skeleton

### 1. Two database roles from day one: one owns the schema, the app only uses it

**What I did.** Blastproof's own Postgres has two login roles. `blastproof_owner` owns the database
and the schema; it is the only role that can create or change tables, and only migrations use it.
`blastproof_app` is what the running server connects as. It can connect, but it cannot create a
table. Later, the migrations grant it exactly the table rights it needs, and nothing more. For
example, it can add audit events but never edit or delete them. Neither role is a superuser, and
nobody else can connect at all. An init script sets this up when the container is first created,
and an integration test proves it on every run.

**Why.** Blastproof's whole pitch is "an agent can't do more damage than you allowed". The same
idea has to apply to Blastproof itself. If the server ran as the owner, any bug or injected SQL in
it could drop tables or rewrite the audit log. With a separate app role, the database refuses that
work, whatever the code does. It also lines up with production. Neon roles and Fly.io secrets
follow the same split, so the code never assumes it has more power locally than it will in
production.

**What I rejected.** Using the Compose superuser for everything (the default in most tutorials).
It is simpler for a week and wrong forever after. Every test would pass with privileges the real
app should never have, and you would find the missing grants in production.

**Trade-off and limits.** Every new table now needs an explicit grant in its migration, so there is
a bit more ceremony per migration. One open question is whether `CREATE EXTENSION vector` works for
a non-superuser owner. Step 4 checks it, and if not, the init script creates the extension
instead. The local passwords are throwaway values, which is only acceptable because the port
listens on this machine's loopback address alone, never on the network. One thing I noticed only in
review: by default `REVOKE ALL` also took away the app's right to create temporary tables. I kept
that on purpose and wrote a test for it, so if a library ever needs temp tables, the failure is
obvious and explained.

### 2. Every request gets an ID, and every log line is JSON that carries it

**What I did.** A small middleware gives each HTTP request a UUID. It sends that UUID back in the
`X-Request-ID` header and attaches it to every log line written while handling the request. All
logs, including uvicorn's and other libraries', come out as one JSON object per line, with a UTC
timestamp, level and event name. The middleware writes one `request_finished` line per request,
with the method, path, status and duration. If a caller sends its own `X-Request-ID`, I reuse it
only if it is a valid UUID; anything else is replaced.

**Why.** Blastproof is a pipeline. One agent write passes through classify, preview, judge, decide
and commit, sometimes with a human approving in between. When something goes wrong, I need to pull
every line for that one request and nothing else. JSON makes logs searchable by field instead of
by grep. The request ID ties the lines together, and later steps add an `action_id` the same way.
Accepting only UUIDs from outside closes a log-injection hole: a caller can't slip a newline or a
huge string into my logs to fake entries.

I wrote it as plain ASGI middleware instead of Starlette's convenient `BaseHTTPMiddleware`, because
that one buffers responses. That breaks streaming endpoints, and the MCP server in step 6 streams.
Getting this right now means step 6 doesn't have to rewrite it.

**What I rejected.** Plain-text logs, which are fine to read but impossible to query reliably.
Echoing any incoming request ID, which is easy but lets callers write into the logs. Keeping
uvicorn's access log next to my own line, which logs every request twice.

**Trade-off and limits.** The first version had a gap I found in review. A crash returned a 500
without the header, because Starlette's error handler sits outside my middleware. Now the
middleware catches the crash itself. It logs the traceback with the request ID and returns a
generic 500 that still carries the header and never leaks the error text. If a streaming response
has already started, a clean 500 isn't possible any more, so it logs and lets the server close the
connection. In development, uvicorn's file-watcher process logs JSON only when started with
`--log-config log_config.json`, because that process never loads the app.
