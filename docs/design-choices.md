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

---

## Step 3: Demo company database and eval scenarios

### 1. The fake data is deterministic, and the demo's numbers are built in, not hoped for

**What I did.** The seed generates about 270,000 rows for a fake SaaS company: 50,000 users, their
subscriptions, payments, invoices, orders and so on. Every run produces byte-identical tables. The
numbers the whole project quotes, "48,213 inactive users, including 12 paying customers (3
enterprise)", are not luck. I fix the user mix first (exactly 48,201 inactive free users, 9
inactive pro, 3 inactive enterprise, and so on), shuffle it with a seeded generator, and only then
fill in names and details. Every date is relative to a fixed anchor, 1 October 2026, never to
today. No user's last login is within 14 days of the "6 months ago" line, so asking for "6 months"
or "180 days", at any time of day, always gives the same answer. A separate `rebase_dates` script
moves every date forward by whole days when a long-lived database needs to look current, so the ages
stay the same.

**Why.** The project's claims are numbers: the impact report must say 48,213, the evals must say
"100% of incidents caught", and later steps compare their output against expected counts. If the
data changed between runs, every one of those tests would be flaky, and I couldn't tell a bug from
a reshuffle. I was careful about *what* randomness I trust. Python only promises that
`random.random()` returns the same sequence on future versions; `randint` and `choice` may change.
So every value that affects a count goes through `random()`. Faker only writes cosmetic text, so
when Dependabot upgrades Faker, names may change but counts cannot. A `NOT NULL` last login closes a
quiet trap: in SQL, `last_login_at < cutoff` silently skips NULL rows.

**What I rejected.** Letting Faker generate everything and writing down whatever counts came out.
It's quicker, but the numbers would move with any library upgrade, and the spec's headline numbers
would need rewriting each time. Freezing the clock in the running app was also rejected, because a
pinned "now" that isn't now is a bug outside demos. The anchor plus rebasing gives stable numbers
without lying about time.

**Trade-off and limits.** The margin only protects the one time window I designed for (last
login). Other windows, such as "tickets closed over a year ago", have no margin, so their counts are
exact only when `now()` is pinned to midnight on the anchor date. I found this when a count moved by
2 depending on the hour. Step 8's frozen clock must pin to exactly that instant. The data is also
deliberately odd (96% of users inactive), because it serves a demo, not realism.

### 2. The scenario files are facts checked against the database, and labels say what *should* happen

**What I did.** Before any of the safety pipeline exists, I wrote the tests it has to pass. There
are two files. `incidents.yaml` replays the six real-world incident patterns. `labeled.yaml` has 23
writes labeled allow, review or block. Each SQL entry states the decision it should get, the rules
that should match, and where useful, the exact effect it should have: rows deleted, updated or
inserted per table, the plan breakdown, and how many paying customers it touches. Three incidents
aren't SQL at all (running `psql`, editing the hook settings, reading `.env`), so the schema has a
second kind of entry in the shape Claude Code's hook actually sends. A Pydantic model rejects typos
and unknown keys. An integration test runs every SQL scenario inside a transaction on a scratch
copy of the database and rolls it back. It hashes every row before and after, by primary key, the
same way the real preview will, and the measured effect must equal the file.

**Why.** "Measure, don't predict" is the core idea of Blastproof, so the expected numbers must be
measured too, not worked out by hand. Measuring caught two mistakes I would have shipped. An
`UPDATE` that sets a value a row already has changes nothing, so the diff (correctly) doesn't count
it, which meant one scenario's expected rule was wrong. And the time-of-day drift described above.
Labels are the *correct* outcome, not what today's rules happen to do. If I copied the rules' output
into the labels, the false-block rate would be zero by definition and the evals would measure
nothing. Instead, where rules alone get it wrong (a harmless one-row ticket update the rules send
to review; an enterprise account the rules only review but the business rules say never delete),
a note says so, and the eval will count it honestly.

**What I rejected.** Squeezing hook incidents into the SQL shape with empty fields. A single
"expected rule" per entry, when the Replit delete legitimately matches three. Hand-computed
expected counts with no test behind them.

**Trade-off and limits.** The count test copies the shadow run's method instead of using the shadow
run, which doesn't exist until step 10. So step 10 must still reproduce these numbers itself. The
labeled set is 23 of the planned 50. Writing the scenarios exposed a gap in the plan's rules:
nothing blocks `pg_terminate_backend`, a side-effect function. It's labeled `block` and flagged for
steps 8 and 9, not quietly papered over.
