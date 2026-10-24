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

---

## Step 2: CI

### 1. CI starts Postgres from the same Compose file I use every day

**What I did.** The GitHub Actions job runs `docker compose up -d --wait` on the repo's own
`docker-compose.yml`, then runs the whole test suite against it, integration tests included. It
pulls the same digest-pinned image and runs the same role init script and healthcheck as my
laptop. `--wait` blocks until the healthcheck passes, and that check passes only once the roles
exist, so tests never race the database. If anything fails, the job prints the container logs.

**Why.** The integration tests check security properties: the app role can't create tables,
outsiders can't connect, neither role is a superuser. Those tests only mean something if CI's
database is set up exactly like the real one. If CI had its own copy of the setup, the two could
drift, and a green check would prove nothing about the setup I actually run. With one Compose file
there is one source of truth. The plan's wording was "a Postgres service container". I changed it
on purpose, because GitHub starts service containers before the repo is checked out, so they can't
mount the init scripts at all. Integration tests also fail, never skip, when the database is
missing. A broken CI database turns the check red instead of quietly running fewer tests.

**What I rejected.** A `services:` container plus a step that replays the init SQL with `psql`. It
matches the plan's wording, but the image, healthcheck and setup would then live in two places.
Running only unit tests in CI is cheaper, but it would leave the role and permission checks
untested on every pull request.

**Trade-off and limits.** The job pulls the image from Docker Hub on every run (about 35 seconds for
the whole job today), and it depends on Docker Hub being up. Compose on the runner is 2.38, while my
machine runs v5. The file uses nothing version-specific, but a future Compose feature could work
locally and fail in CI. The first Linux run also answered two open questions from step 1:
publishing the port on `[::1]` works on GitHub's runner, and the `no_network` test guard passes on
Linux's event loop.

### 2. The CI pipeline is treated as an attack surface: no secrets, read-only, everything pinned

**What I did.** The workflow uses no secrets at all, and its GitHub token can only read the repo.
Checkout doesn't keep the token in the git config. Every third-party action is pinned to a full
commit SHA, not a tag, and the runner image (`ubuntu-24.04`) and uv (0.13.5) are pinned too. uv's
version is written once, as `required-version` in `pyproject.toml`: CI installs that version, and
uv on my machine refuses to run with a different one. Dependabot opens a weekly grouped pull
request for the Python packages, the actions and the Postgres image digest, and each of those PRs
goes through the same CI. A small test file reads the workflow and fails if someone adds a secret,
tag-pins an action, unpins the runner or drops the integration tests.

**Why.** Blastproof holds database credentials, so its build pipeline is a way in. A tag like `v7`
can be moved to different code by whoever controls the action's repo; a commit SHA can't. A PR's
code runs inside CI, so a workflow with no secrets and a read-only token has nothing to leak and
nothing to push. Pinning everything also means a red build is caused by my change, not by a new
uv, Ubuntu or image appearing overnight. The catch with pinning is that pins go stale and miss
security fixes. Dependabot fixes that: updates still arrive, but as reviewable PRs that have to pass
CI, not as silent drift. The config tests exist because these rules are easy to break by accident
in a one-line edit, and nobody re-reads a workflow file once it works.

**What I rejected.** Floating tags (`@v7`, `ubuntu-latest`, "latest uv"), which are less work but
let code change under me without a commit. Putting `GOOGLE_API_KEY` in the PR workflow to run LLM
evals; those go in a separate nightly workflow later. Pinning uv in the workflow file only, which
would let my machine and CI drift apart.

**Trade-off and limits.** Pins need upkeep: Dependabot PRs to read and merge weekly, and a uv
upgrade on my machine now means editing `pyproject.toml` first. The secret check is a text match on
`secrets.`, so `secrets['X']` written with brackets would get past it. Code review is the backstop
there. The workflow can't turn on Dependabot alerts by itself; that is a repo setting the owner
switches on.
