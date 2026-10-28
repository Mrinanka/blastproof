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

---

## Step 4: Blastproof data model

### 1. The audit log is append-only because the database says so, not because the code is careful

**What I did.** `audit_events` is the record of what every agent did and who approved it, so
nobody may rewrite it. Two layers in Postgres enforce that. First, privileges: the running app's
role, `blastproof_app`, can only `SELECT` and `INSERT` on that table, so an `UPDATE`, `DELETE` or
`TRUNCATE` from the app gets "permission denied". Second, a trigger: the owner role,
`blastproof_owner`, owns the table and so has every privilege, and a trigger stops it anyway with
"audit_events is append-only". The plan asked for a `BEFORE UPDATE OR DELETE` trigger. While
building it I noticed that `TRUNCATE` skips row-level delete triggers entirely, so the owner could
still have emptied the table in one statement. I made the trigger statement-level and added
`TRUNCATE` to it. That also means it fires even when an `UPDATE` matches no rows. Tests cover every
combination: both roles, all three operations, plus a check of the app's exact privilege on every
table. To prove the tests weren't just passing by luck, I removed the trigger and widened the
grant, and watched them fail.

**Why.** An audit trail you can quietly edit isn't evidence of anything. If the rule lived only in
application code, a single bug or a successful SQL injection in Blastproof would be enough to
rewrite history. Putting it in the database means the code *can't* do it, whatever goes wrong
above. It's the same principle Blastproof applies to agents: limit what the caller is able to
do, instead of trusting it to behave.

**What I rejected.** A single role with `REVOKE` (the owner of a table can always grant itself rights
back). A row-level trigger only, which was the plan's wording and had the `TRUNCATE` hole.

**Trade-off and limits.** The owner can still drop or disable the trigger. That is a deliberate
schema change, done in a migration and visible in code review, not a quiet edit, and the app role
can do neither (tested). Retention never deletes audit events, so this table only grows. That's
fine at MVP scale, but it will eventually need partitioning or archiving.

### 2. The schema is tested like code: throwaway databases, three cross-checks, one transaction

**What I did.** The models (`app/db/models.py`) and the migration (`0001_initial_schema.py`) are
two descriptions of the same schema, and two descriptions drift apart. So I check them against each
other, and against reality. Alembic generated the first draft of the migration from the models.
I then read it and fixed it: the generated file referenced the vector type without importing it,
and it knew nothing about extensions, grants or triggers, which I added by hand. Three checks
follow. `alembic check` compares the models with the migrated database and fails on any difference.
Alembic doesn't compare CHECK constraints, though, so a second test reads the CHECKs straight out
of Postgres and compares them with the models. A third compares the models' allowed values with the
Python enums. Every migration test runs on a fresh, throwaway database, which the test creates and
drops, so tests can freely upgrade, downgrade and break things without touching my dev data. The
whole migration runs in one transaction. When the `vector` extension is missing, the run fails
cleanly and leaves no half-built schema behind, and a test proves exactly that.

**Why.** The schema is the foundation every later step stands on: auth, the workflow, commits.
A mismatch between models and database doesn't fail loudly; it shows up weeks later as a wrong
query or a missing permission. Cross-checking catches it on the next test run. The second check
exists because of a gap I found reviewing my own work. I had written that the first and third
checks covered the CHECK constraints, but they didn't: an enum value added without a migration
passed every test. I added the second check and confirmed it catches exactly that case. That
missing extension was a real surprise, too. The plan assumed the owner could install pgvector,
but pgvector isn't marked "trusted" by Postgres, so only a superuser can. I found that out by
checking the database before writing code, not after a failed deploy, and moved its creation into
the container's init script.

**What I rejected.** Hand-writing the whole migration with no comparison against the models.
Running migration tests against the shared dev database (an upgrade/downgrade test would wipe it).
Giving the owner role CREATEDB or superuser rights to make testing easier.

**Trade-off and limits.** The test helper that builds a scratch database repeats the per-database
part of the init script, so the two could drift. They're short, and a comment points at each.
Enum-like columns are text with CHECK constraints rather than Postgres enum types. That's easier to
extend, but every new value now needs a small migration, which is exactly what the new test
enforces. Production on Neon has no init script, so the deploy step must create the extension and
the database-level grants by hand.

---

## Step 5: Auth

### 1. Login rate limiting is counted in the database, one attempt at a time per email

**What I did.** Every login attempt, failed or not, is written to a `login_attempts` table with
the email and the IP it came from. Before checking a password, the server counts recent failures.
At 5 failures for this email from this IP in 15 minutes, or 20 for this email from anywhere in an
hour, it answers "too many attempts" (HTTP 429) and does not look at the password at all, so even
the right one is refused. Each attempt runs inside one database transaction that first takes a
Postgres advisory lock keyed on the email. Two attempts for the same email therefore wait for each
other, and each one sees the count the previous one left behind. Unknown emails are recorded and
limited exactly like real ones, and the "wrong password", "no such user" and "disabled account"
answers are identical.

**Why.** The two limits cover two attacks: one machine hammering one account, and a botnet
rotating IPs against one account (the second limit ignores the IP). The lock matters more than it
looks. Without it, ten requests fired at once all read "4 failures so far" before any of them
writes, and all ten get a guess. I wrote a test that fires ten in parallel and expects exactly
five recorded failures, and it fails when the lock is removed. Refused attempts are not recorded,
so a lockout ends exactly 15 minutes (or an hour) after the last real guess. Counting in Postgres
rather than in memory means it survives restarts and needs no Redis, which the MVP leaves out.

**What I rejected.** An in-memory counter (lost on restart, wrong with two machines). Counting the
refused attempts too (an attacker who keeps trying would keep the owner locked out for good).
Trusting the `X-Forwarded-For` header to find the real IP (anyone can set it, so an attacker could
pretend to be a new IP on every request).

**Trade-off and limits.** Anyone who knows your email can lock you out for an hour by failing 20
times. That comes with any per-account limit, and the MVP has no admin "unlock" button. In
production, logins reach the backend through the dashboard's server and Fly's proxy, so every
user appears to come from the same IP and the per-IP limit acts per email. That is stricter, so
it's safe, but the real fix (the dashboard passes the browser's IP and the backend trusts it only
from the proxy) belongs to the frontend login step. The lock serialises logins for one email only;
different users never wait on each other.

### 2. Tokens don't need a session table, but can still be revoked at once

**What I did.** Logging in returns a signed token (a JWT) that lasts 8 hours. It holds only the
user's ID and a number, `token_version`, copied from the user's row. On every request the server
checks the signature, then reloads the user from the database. If the account is disabled, or its
`token_version` no longer matches the one in the token, the request gets 401. Bumping that number
by one logs the user out everywhere. The role is not in the token; it is read from the row each
time, so a demoted admin loses admin rights on their very next request. Agent keys work
differently: they are long random strings shown once, and only their SHA-256 hash is stored. An
agent is refused if it is deactivated, and also if its owner is.

**Why.** A plain JWT can't be taken back before it expires, which is a bad property for the
dashboard of a safety tool: an offboarded admin would keep approving writes for up to 8 hours.
Reloading the user costs one indexed query per request, and buys instant deactivation, instant
demotion and a "log out everywhere" switch with no session table to clean up. Agent keys get a
fast hash rather than argon2 because they are 256 random bits: there is nothing to brute-force, and
the lookup by hash runs on every MCP call. Passwords are chosen by people, so they get argon2,
which is slow on purpose.

**What I rejected.** Putting the role in the token (a demotion would wait up to 8 hours). Server-side
sessions in a table (more moving parts for the same result). Hashing agent keys with argon2 (slow on
every call, and no gain for random keys). A key check that ignores the owner (an offboarded user's
agent would keep writing actions under their name).

**Trade-off and limits.** Every authenticated request costs one database read, so the database must
be up for anything to work; that is fail-closed, which is what we want. Logout in the dashboard only
deletes the cookie (D-041); the token itself stays valid until it expires unless `token_version` is
bumped, and nothing bumps it automatically yet. There is no password change in the MVP.

---

## Spike: MCP mounting

### 1. The agent's identity comes from the key on each request, and the server keeps no sessions

**What I did.** Every call to `/mcp` passes through a small middleware that reads the bearer key,
looks up the agent, and answers 401 if the key is wrong. If the key is right, it hands the agent to
the tool. The MCP server runs in stateless mode, so it never creates a session the next call could
reuse. The spike put two agents' keys through three possible hand-off methods: a context variable,
the request's `state`, and re-reading the header. It tried them one call after another, both at
once, and in every server mode. All three saw the right agent every time.

**Why.** Ownership is the core of Blastproof: every action records whose agent made it, and each
user sees only their own. An identity mix-up would be an authorisation bug, not a cosmetic one. The
surprise was elsewhere. In the default stateful mode, a session opened with agent A's key happily
accepted agent B's key. The tool still saw B, so nothing was misattributed, but sessions were not
tied to anyone. Stateless mode removes sessions, so that whole class of problem cannot happen. It
costs nothing: Claude Code already uses the new 2026-07-28 protocol, which has no sessions anyway.

**What I rejected.** The SDK's own OAuth-style auth (`TokenVerifier`). It is built for OAuth
servers and adds routes we don't need. Our middleware already gives Claude Code a clear 401, which
it shows as "AUTH_HEADER_REJECTED". Stateful mode with session-to-agent binding (more code to guard
a feature we don't use).

**Trade-off and limits.** With no sessions, `session_id` on actions is always null for Claude Code.
The plan allowed that, and the column stays for later. The context variable reaching the tool relies
on how the SDK schedules work, which its docs don't promise. So step 6 keeps the two-agent tests
running through the real middleware, and a tool that finds no agent refuses the call.

### 2. Mount the MCP route inside FastAPI's router, and stream so slow calls stay alive

**What I did.** The SDK hands back a tiny app with one route, `/mcp`. Instead of mounting that app,
I add its route to FastAPI's own route list. I also keep the response streaming (SSE) instead of
switching to plain JSON. The server is built inside `create_app()`, and our startup code starts its
session manager.

**Why.** Both obvious ways to mount it broke something. Mounting at `/` worked for `/mcp` but
swallowed every unknown URL, so our JSON 404 errors became plain text. Mounting at `/mcp` redirected
`POST /mcp` to `/mcp/`. Adding the route directly fixed both. Streaming came from measuring rather
than guessing: our `get_action_status` long poll can take 50 seconds, and Fly's proxy drops a
connection that sends nothing for about 60. In streaming mode the SDK sends a small ping every 15
seconds (the longest silence measured was 15.4 s). In JSON mode the line was silent for the whole
call, which is too close to that limit. The server is built per app because its session manager
can only be started once, and the tests build many apps.

**What I rejected.** `app.mount` in either form (the reasons above). JSON responses, which were my
first draft and are simpler to read in tests, but leave long calls exposed to the proxy's idle cut.

**Trade-off and limits.** Adding the SDK's route by hand only works because we don't use the SDK's
auth: with auth turned on, its app has more routes and middleware. The step 6 tests guard this by
checking the route list. The 60-second Fly figure comes from Fly's community forum, not its docs,
so the deploy step should check it on the real machine.

---

## Spike: Claude Code hooks

### 1. A hook that fails must block, so I set `onFailure: block` and test the failure paths

**What I did.** I ran about 30 real Claude Code sessions against a decoy folder, each with a hook
that was made to sleep, crash, point at a missing script, deny, or stay silent, and recorded whether
the agent's command ran. A hook that timed out or crashed let the command through. Setting
`"onFailure": "block"` turned both into a block. A Python script that does not exist exits with
code 2, which counts as a deliberate block, so one failure mode is closed by accident while the
others are open.

**Why.** Blastproof's first rule is fail closed: a failed check never lets a write through. The
documentation says hooks fail open, and without these runs I would have trusted the script's own
error handling, which cannot help if the script never starts or hangs.

**What I rejected.** Wrapping the script in try/except and calling it done; an `http` hook, which
works but drops its auth header silently without an allow-list and cannot do the retry and
local fallback the plan needs.

**Trade-off and limits.** Blocking on failure means a Blastproof outage stops shell commands and
edits (reads fall back to a local deny-list). Each result is one run of a small model, so I treat
"leaks" as solid and "not tried" as nothing.

### 2. The hook is a guardrail, so the agent must not be able to switch it off

**What I did.** I checked what an agent can do to its own hook. It could write
`.claude/settings.local.json` with `disableAllHooks: true` and run the blocked command next. Adding
`Edit(.claude/**)` to the deny rules stopped that. A deny rule on the backend folder blocked Read,
Grep, Glob, `cat` and `Get-Content`, but `python -c open(...)` still read it.

**Why.** If the guardrail can be turned off from inside, it is not a guardrail. The real boundary is
that the agent never holds the database credentials; the hook and deny rules only make accidents
and obvious attempts fail loudly.

**What I rejected.** Treating `permissions.deny` as the security boundary, and relying on the hook
alone to protect `.env`.

**Trade-off and limits.** On the dev machine an agent can still reach `.env` through an interpreter,
which is the accepted limitation (D-002). Settings reload with about a one-call lag, and I did not
test a nested `claude --settings` started from Bash (docs only).

## Step 6: MCP server

### 1. Write the record first, then do the work, and make every ending a record too

**What I did.** Each recorded tool call (`query_database`, `list_tables`, `describe_table`) starts
by saving an `actions` row with status `received`, under the agent owner's user ID, and commits
it before touching the company database. Only then does the read run. It ends in a second short
transaction that sets the status to `executed` or `failed`, with the total time, and adds the
matching audit event. Every expected failure has a short reason code: the read was refused, the
5-second timeout hit, Postgres rejected the query, or the company database was down. Anything I
didn't expect is caught, logged as just the exception's class name, saved as
`failed: internal_error`, and the agent gets the standard "could not complete this safely" message.

**Why.** Blastproof's value is the audit trail: "every call is recorded with its owner". If I
recorded the action only after a successful read, the cases that matter most would leave no trace:
a timeout, a crash, a query the agent shouldn't have sent. Committing first means even a process
that dies mid-read leaves a `received` row. Someone looking at it can see the call happened and
never finished. Logging only class names was a deliberate choice. A Postgres error can quote the
value it choked on (an email, say), and a connection error can include the database URL, so the
exception's text never goes into a log. The agent does get Postgres's message for a failed query,
so it can fix a typo. The agent already has access to that data.

**What I rejected.** One transaction around everything (a crash would roll back the record of the
crash). Recording only successful reads. Letting exceptions reach the MCP SDK, which logs the full
traceback, text and all. That was the leak the spike found.

**Trade-off and limits.** Two transactions per call instead of one, which adds a little latency.
A call that fails the SDK's own argument checks (a `wait_seconds` of 51, say) never reaches our
code, so it isn't recorded. If Blastproof's own database is down, nothing can be recorded, and the
middleware answers 503 before any tool runs.

### 2. Reads are guarded by four independent layers, not one clever check

**What I did.** Writes come in step 17, so for now `query_database` must refuse anything that isn't
a plain read. A small sqlglot check in front accepts exactly one SELECT (WITH and UNION are fine).
It refuses writes, a DELETE hidden inside a WITH, `SELECT INTO`, `FOR UPDATE`, `EXPLAIN ANALYZE`,
several statements at once, and functions with side effects such as `pg_terminate_backend` or
advisory locks. Behind that check, three things stop a write each on their own. The query runs as
`blastproof_reader`, a role that can only SELECT. It runs inside a `READ ONLY` transaction. And it
has a 5-second statement timeout. The reply is capped at 500 rows: we fetch 501 to know whether to
add the "showing the first 500 rows" note.

**Why.** A parser is a heuristic, and SQL has many corners. If the check were the only guard, one
missed case would be a write on the company database. With layers, a missed case has to get past a
role with no write rights and a read-only transaction too. A test hands `INSERT` and `nextval`
straight to the read function, skipping the check, and Postgres refuses both. The check still
earns its place. It catches things the database would allow a read-only role to do, such as
taking advisory locks or ending other sessions. It also gives the agent a clear "only SELECT for
now" answer instead of a cryptic permission error.

**What I rejected.** Relying on the reader role alone, which lets side-effect functions through.
A regex check, which comments and quoting defeat. Waiting for the real classifier in step 8, which
would leave step 6 unable to say no.

**Trade-off and limits.** The check is stricter than it needs to be: a SELECT wrapped in brackets,
or `TABLE users`, is refused. That's the right direction for a safety tool, and step 8 replaces the
check anyway. Rows go back to the agent unmasked. Masking covers logs, prompts and the dashboard,
and the reader role could read those rows regardless. Each call opens a fresh connection, which on
Neon costs about 1.7 s of the roughly 3 s a call takes; a pool would fix that.

## Step 7: Remove direct access

**What we built.** A separate folder for Claude Code, outside the repo, that holds no database
credential. Its only secret is the agent key. Claude Code reaches company data only through
Blastproof's MCP server. One small script (`claude_hook/workspace.py`) creates the folder, scans it
and its environment for anything that looks like a database credential, and drives Claude Code to
prove the guardrails hold.

**Choice 1: the boundary is the missing credential, and the deny rules are only a guardrail.**
The agent cannot leak or misuse a password it never had. So the real protection is that the
workspace and its environment contain nothing to find. The deny rules (no reading the repo,
`.env` files, SSH or Neon tool configs, and no editing its own settings) sit on top. They stop the
easy, accidental routes, and they matter because an agent that could edit its own settings could
switch off its own hook. We deny the whole repo, not just `backend/`, because the Docker Compose
file at the repo root holds the local database passwords. We know the rules leak: a Python one-liner
can still read a file, and the local Docker databases still have guessable passwords. We wrote that
down (D-119) instead of pretending otherwise. Full isolation is checked on the deployed demo,
where the credentials live only in the backend's environment.
*Rejected:* denying only `backend/`, and counting the deny rules as the security boundary.

**Choice 2: the key travels by stdin, lives in one file, and the checks never print values.**
The agent key is piped into the script, so it never reaches the command line, shell history or
screen. It is written only to the workspace's local settings file. `.mcp.json` refers to it as
`${BLASTPROOF_AGENT_KEY}`, so the shared config never contains the key. The scan and verify
commands print only file names and variable names. The `.env` check reads a throwaway canary file
first, and touches the real `.env` only if the canary was denied, because a failed deny would put
real secrets in front of the model. We checked the one thing the docs did not say (that Claude Code
fills the header from the settings file) with a fake key before building on it.
*Rejected:* a key in the command line or `.mcp.json`, and testing the real `.env` first.

**Limits.** The two deny checks use Sonnet because Haiku declines to attempt the read, which proves
nothing. `verify` needs a Claude login, so it runs by hand and not in CI. A Bash redirect or a
Python one-liner can still get past the rules; step 18's server-side rules cover part of that.

## Gate audit after step 7 (fixes, D-121)

**Choice 1: audit against the code, then fix only what matches how the app is used.**
The audit ignored the ticks in PROGRESS.md and asked one question: can the agent reach the company
data without going through Blastproof? It found a route nobody had written down: `docker exec`
into the local database container gives a superuser shell with no password. We closed it with a
deny rule in the agent's workspace, and we did not pretend that makes it a security boundary.
Dev-machine problems got dev-machine guardrails. The real isolation is still checked on the
deployed demo, where the agent's machine holds no credential at all.
*Rejected:* random Compose passwords now (bigger change, belongs to Deploy), and ticking the gate
because the tests were green.

**Choice 2: the read check is a first layer, so we block more of what runs SQL, but no allowlist yet.**
`check_read` runs in production, so its gaps matter more than the dev ones. A SELECT can hide a
second query inside text, for example `query_to_xml('select pg_advisory_lock(1)', ...)`. We now
refuse those and the file-reading functions. We kept `pg_sleep` allowed because the 5 second
timeout already stops it, and a test depends on it. Behind this check, the read-only database
role and the read-only transaction still stop writes on their own. A blocklist always lags behind
new functions, so step 8's classifier should use an allowlist instead.
*Rejected:* building the allowlist now (step 8 owns it), and blocking `pg_sleep` (it would break
the timeout test for no extra safety).


---

## Step 8: Classifier

### 1. Pin the clock by cutting text, not by rewriting the query

**What I did.** An agent's SQL often says `now()`. If we preview the write now and commit it five
minutes later, `now()` means two different moments, so the rows could differ. The classifier
replaces every clock function with one fixed UTC timestamp taken when the call arrived, and that
pinned text is what gets previewed, approved and committed. I do the swap by finding each clock
function in the tokenizer's character positions and cutting that exact span out of the original
string. Then I parse the result again and require it to classify identically, with no clock
function left. If anything differs, the statement becomes `unknown`, which is blocked.

**Why.** The obvious route is to parse the SQL into a tree, edit the tree and print it back. I
tried it: the printer turns `current_time` into `CURRENT_TIME()`, which Postgres rejects, and it
drops the precision from `current_timestamp(3)`. Cutting spans leaves every other byte alone, so
SQL with no clock function comes back identical. The re-parse is the safety net: any mistake in my
splicing fails closed instead of silently running different SQL than the owner approved.

**What I rejected and the limits.** Printing the tree back (above); pinning strings like
`'now'::timestamptz` (they convert implicitly, so I treat them as volatile and block them in a
WHERE). A bare `'now'` with no cast inside a comparison is not detected yet.

### 2. Allowlists, so anything the classifier does not recognise is treated as dangerous

**What I did.** The classifier only calls a statement a read if its root is a plain SELECT, and
only calls a function safe if it is a typed function or on a list of about 250 known-safe names.
Everything else is a write or `unknown`. A DELETE hidden inside a CTE makes the whole statement a
write. `SELECT pg_terminate_backend(pid)` is a write because the function has a side effect. A
function the list has never seen is also a write.

**Why.** The alternative is a blocklist ("refuse these bad things"), and a blocklist only works
until someone finds the thing you forgot. sqlglot, the parser, even reads `START TRANSACTION`,
`SAVEPOINT` and `NOTIFY` as harmless expressions, so I could not trust its types alone; I look at
the leading keywords too. With an allowlist, my mistakes make Blastproof stricter, not weaker,
which fits the rule that a failed check never lets a write through.

**What I rejected and the limits.** A deny list of statement types. The cost of the allowlist is
that a legitimate function missing from the list gets refused once reads use the classifier (step
17); the list is generous and easy to extend, and a test covers the common functions.

## Step 9: Policy rules

### 1. The rules are a file, judged in two passes, and the strictest answer wins

**What I did.** What Blastproof allows lives in `backend/policies/rules.yaml`, not in Python. Each rule
has conditions, an outcome (allow, review, block) and a reason. Rules run twice. Pass 1 sees only the
SQL, before any preview: a rule that needs the preview's numbers (rows changed, share of the table) is
skipped, not counted as false. A block ends the request at once; a fast-lane allow skips the preview.
Pass 2 runs after the preview with every fact. If several rules match, the strictest wins (block over
review over allow), and a write that no rule explicitly allows goes to a person.

**Why.** "Skipped, not false" matters: a rule like "more than half the table" cannot be answered from
SQL, and treating it as false would let a dangerous write look clean. Making the allow need a positive
match means silence is never approval. And putting the rules in a file means the owner can read and
change policy without touching code, while order only changes which reason is shown, never the outcome.

**What I rejected and the limits.** A code-level safety net that blocks things even if someone deletes a
rule: I declined it so outcomes come from one place, which means deleting "Unparseable SQL" really does
remove that block. A test pins the known gaps (the CTE delete feeding a small insert) so any change is
deliberate; the MERGE gap I closed by listing `merge` in the two fraction rules.

### 2. A bad rules file stops the app, and bad facts stop the write

**What I did.** The loader is strict. It rejects unknown keys, a key written twice (PyYAML would quietly
keep the last one), a "true" in quotes where a real yes/no belongs, a misspelled statement type, and a
fast-lane rule that could never fire. The SQL condition used to count paying users may only use columns,
literals and comparisons, no functions or subqueries. At run time the same attitude applies: if the
preview's numbers are missing or negative, the policy raises and the caller blocks the write.

**Why.** The file decides what reaches a customer's database. A typo that silently disables a rule is
the same as deleting it, so every mistake has to be loud, at startup, naming the rule and the key. This
is the project's "fail closed" rule applied to configuration: when unsure, refuse to run.

**What I rejected and the limits.** Checking at startup that the columns exist in the real database:
startup has to work offline, so that check is a pure function that step 10's preview will call.

## Spike: Neon branching

### 1. Measure first, and measure the whole preview, not just the branch

**What I did.** Before writing the Neon client I ran 20 real create, connect, query, delete cycles on the
dev project and timed each stage. Five of them also ran a preview-shaped workload: hash all 11 tables,
run the delete in a transaction, diff, roll back. The branch is usable at about 5 s (p50 4.99, p95 5.16),
but a whole preview is about 14 s.

**Why.** "Branches are fast" is true and misleading: the branch is only a third of the wait. If I had
only timed the create call, the latency target in the spec would have been wrong by a factor of three.
The measurement shows where the time really goes (hashing), so step 10 optimises the right thing.

**What I rejected and the limits.** A warm, pre-created branch to hide the 5 s; it adds cost, cap pressure
and stale data, and the MVP does not need it. Timings are from a dev laptop; Fly in the same region
should be quicker, so the numbers are an upper bound.

### 2. Build the connection ourselves, use the direct endpoint, and keep two cleanups

**What I did.** Neon does not return a connection string when the parent has several roles, so the client
builds one from the branch's host plus our own writer role, password and database. It uses the direct
endpoint, not the pooled one, because a shadow run needs session state and a multi-statement transaction.
Each shadow branch gets an `expires_at` of 15 minutes (the API accepts it) and the startup cleanup stays.

**Why.** The free plan allows 10 branches, and a leaked one counts until it is really gone. If leaks fill
the cap, every write fails closed, which is safe but makes the product useless. Two independent cleanups
(Neon's expiry and our own sweep) mean one failing does not do that. Using our own role also keeps the
credential rule: only the backend knows the writer password.

**What I rejected and the limits.** Relying on `expires_at` alone: its deletion delay is not documented
and I did not measure it. I also could not prove that the pooled endpoint breaks session state, because
the test endpoint had pooling off; I chose direct on design grounds, not on a failed test.

## Step 10: Neon client and shadow executor

### 1. Measure the write on a throwaway copy instead of predicting it
**What we did.** Before a write is judged, we make a disposable copy of the company database (a Neon branch in production, a local clone in tests). We fingerprint every row of every table with one Postgres-computed hash. We run the exact `pinned_sql` inside a transaction, compare fingerprints, and roll back. The copy is always deleted.
**How I'd say it in an interview.** "An agent says `DELETE FROM users WHERE ...`. Parsing that SQL can't tell you the 48,000 deletes also wipe 121,000 order items through foreign keys, or that triggers or defaults change other tables. So we don't guess. We run it on a copy and count what actually changed, row by row. The numbers come from the database, never from a model. The judge and the human see facts. The same per-row hashes are saved so that at commit time we can check the real database still looks like the preview, and roll back if it drifted."
**Why not the alternatives.** Static analysis misses cascades and defaults. `EXPLAIN` gives estimates, not exact counts. Running on the real database inside a rolled-back transaction would hold locks on production and risk side effects. A branch is copy-on-write, so it is cheap and isolated.

### 2. Make the measurement trustworthy: pin the hash inputs, and let the agent name only known tables
**What we did.** `row_to_json` text depends on session settings (time zone, date style, float digits, and others). Every connection that hashes pins them, so a hash taken on the copy equals one taken on the real database later. The agent's SQL may only name tables that exist in the `public` schema of the copy, because our before-snapshot lives in temp tables and temp tables are searched first. Snapshot names are random per run, and the run fails closed on any error with a reason code only.
**How I'd say it in an interview.** "A diff is only as good as its fingerprints. If the hash changed with the time zone, the drift check at commit time would raise false alarms or, worse, hide real changes. So we fix every setting that feeds the hash and have a test that flips the zone and expects the same hash, plus a test showing a raw connection in another zone gives a different hash, which proves the test can fail. And because the agent writes the SQL, it must not be able to edit the evidence: a statement like `UPDATE before_users ...` could rewrite our snapshot and fake a harmless diff. So the SQL may only name real tables, and our snapshot names are random. Anything unexpected blocks the write."

## Step 11: Knowledge and RAG, swappable vector store

### 1. Text in Postgres, vectors behind an interface
**What we did.** Every chunk's text and table tags live in `knowledge_chunks`. Vectors live in a `VectorStore` (pgvector today), computed by an `Embedder`. A store only returns chunk IDs and scores; the text is read back from Postgres. If Postgres no longer has an ID, it is dropped. `reindex` rebuilds all vectors from the stored text.
**How I'd say it in an interview.** "Vectors are a derived index, not data. If the text is the source of truth, swapping pgvector for Qdrant, or changing the embedding model, is just a rebuild. The same contract test suite runs against every store, so a new one can't behave differently. Ingest writes the text first and the vectors second; if the second step fails we have text without vectors, which `reindex` repairs, never the reverse."
**Rejected.** Keeping text in the vector store (ties us to one vendor, and permissions and masking would live in two places).

### 2. Tagged chunks plus similar chunks, and fail if retrieval fails
**What we did.** For a write we take every chunk tagged with an affected table (plain SQL, exact), plus the top 5 by vector similarity to the agent's intent, without duplicates. Any embedder, store or database failure raises `RetrievalError`; we never return the tagged chunks alone.
**How I'd say it in an interview.** "Tags give certainty: the notes about `users` always come. Similarity gives reach: a rule such as 'never delete paying customers' isn't about one table. A partial answer is dangerous, because the judge would think it saw all the rules. So retrieval fails loudly and the caller sends the write to review."
**Limit found.** At k=5 the business rules ranked 5th to 7th for some user-delete intents, so we use the top 10 (D-148). If the knowledge base grows, revisit.

## Step 12: Impact engine

### 1. Code counts, the LLM explains, and a number check decides whether we trust the wording
**What we did.** Every number a reviewer sees (rows per table, totals, the plan breakdown, "12 paying customers", cascades, "96.4% of the table") is computed in Python from the measured diff. Only then is the model asked to explain those facts as a headline, key points and concerns. Before we use its answer, code pulls every number out of it and checks each against an allowed list: the computed facts, numbers the agent itself wrote in its intent or SQL, and the rule thresholds read from `rules.yaml`. Unicode tricks (full-width digits), "12 %" versus "12", and number words like "thirteen" are all caught. One unknown number, an over-long or empty answer, a timeout or any error, and we throw the model's text away and use a plain template built from the same facts. The engine never raises for a model problem.
**How I'd say it in an interview.** "Models are good at sentences and bad at arithmetic, and a reviewer approving a mass delete must be able to trust '12 paying customers'. So the model never produces a number; it may only repeat ours. A regex plus an allowed set is cheap, deterministic and testable: we have table-driven tests for commas, percentages, decimals, Unicode digits and words, and a mutation check for every guard. The fallback matters as much as the check: the summary is an explanation, not a safety decision, so when it fails the write still gets an accurate, if plainer, summary, and the rules still decide. The honest limit is that the check verifies membership, not meaning: '48,213 paying customers' would pass, because 48,213 is a real count, just of a different thing. That's why the computed numbers are shown beside the summary, and the judge reads the facts, not the summary."
**Why not the alternatives.** Asking a second model to check the first (expensive, and also not deterministic). Trimming a too-long answer (could silently drop a concern). Letting the model compute percentages (exactly the mistake we're guarding against).

### 2. Everything the agent or a document wrote goes into the prompt as quoted data, never as instructions
**What we did.** The prompt has two parts. The rules are in the system message, and FACTS (our JSON) come first in the user message. Then the agent's intent, the SQL, sample rows and retrieved knowledge each go into their own `<untrusted_data kind="...">` block. Their content is JSON-encoded with `<` escaped, so a document containing `</untrusted_data> ignore previous instructions` can't close the block. The prompt only ever sees `sql_display` (the function takes the classification, never raw SQL), the intent and knowledge pass through `mask_text`, samples are masked again and capped at 5 per table, and knowledge is capped at 24,000 characters, with the chunks about the target table first. The number check deliberately never takes numbers from knowledge.
**How I'd say it in an interview.** "Three parties write text that reaches this prompt: the agent, the database, and whoever uploaded a document. None of them is trusted. Tags alone aren't enough, because attackers can type tags. So the content is encoded so it can't break out, and we test that by injecting a closing tag and parsing the message back: still exactly four blocks. Masking happens before the prompt, so personal data never reaches the provider or LangSmith, and the caps keep a flood of uploaded chunks from running up cost or pushing the facts out of the model's attention. Injection can still sway the wording; it can't change a number (the check) or a decision (rules and a human)."
**Rejected.** Sending raw SQL for "better context" (it can hold emails), and allowing numbers from knowledge (anyone who can upload a file could then make an invented number pass).

## Step 13: Risk judge, pluggable

### 1. The judge can only add caution, and a judge that fails is never an allow
**What we did.** The AI judge gives a second opinion (allow, review or block, a risk level, a confidence and a reason). One pure function, `decide`, combines it with the rules: a rule block is final and the judge is not even asked; otherwise the strictest of the rule outcome, the judge's recommendation and the injection check wins. A judge that errors, times out, returns garbage or never ran gives review with an "AI check unavailable" flag, never allow. A judge that says "allow" with confidence under 0.6 counts as review. A wrapper, `assess_safely`, turns any raise, hang or malformed result from any judge implementation into that same failure.
**How I'd say it in an interview.** "The rules are deterministic and tested; the model is neither. So the model is only allowed to move a decision in one direction, towards caution. That removes a whole class of attack: even if a poisoned note convinces the judge that everything is fine, the answer can't be looser than the rules. The failure case is the subtle one: if 'the judge broke' meant 'no objection', an outage, or an attacker causing a timeout, would turn the safety check off. So failure means a human looks. `decide` has no I/O, so a property test runs every combination of rule outcome, judge result, confidence and injection and asserts the result is never looser than the rules and never allow after a failure. And because someone will later plug in a different judge, the wrapper doesn't trust any implementation to behave."
**Rejected.** Letting a confident judge lower a review to allow (that is exactly the Replit mistake with a nicer voice), and blocking on a failed judge (an outage would then halt every write; a person can still decide because the facts exist).

### 2. The judge's words are checked like the summary's, and the agent's text is treated as hostile
**What we did.** The judge sees the same quoted-data prompt as the summary (intent, SQL, samples, knowledge and even the earlier model's impact report in `<untrusted_data>` blocks, `<` escaped), plus our computed facts and the matched rules from the owner's file. Its reason must pass the same number check as the summary; if it cites a number nobody measured, we keep its verdict but replace the reason with a sentence written by code. Separately, a pattern check scans the intent, SQL comments, sample values and every retrieved document for text aimed at the reviewer ("ignore previous instructions", "mark this as safe", a fake closing tag, even with full-width letters or zero-width characters), and any hit forces review. Hits record the field and pattern name, never the text.
**How I'd say it in an interview.** "Prompt injection can't be solved by one filter, so there are layers, each one limiting damage. The prompt encoding makes it hard for data to pose as instructions. The 'only add caution' rule means a successful injection can't make anything looser. The pattern check catches the lazy attacks and makes a human look. And the number check keeps the judge from inventing evidence, but I chose to keep its verdict when the explanation is bad, because a 'block' is still useful even if the wording is wrong. The honest limit: a reworded attack gets past the pattern list. That's accepted, because the structural rules above are what we rely on. We also test the list against every legitimate SQL scenario and our schema docs so it doesn't cry wolf."
**Rejected.** Blocking on an injection hit (a customer note that quotes the phrase would be refused outright), and scanning only the part of the knowledge that fits in the prompt (a poisoned chunk past the cap would go unseen).

## Gate audit after step 13 (fixes, D-165, D-166)

### 1. Convenient defaults on a laptop, explicit settings in production
**What we did.** The model settings must name their provider (`google_genai:gemini-3.8-flash`, never just `gemini-3.8-flash`), checked when the settings load. The Gemini default model and its price stay in code so a fresh dev machine works, but a backend started with `APP_ENV=production` refuses to start unless `SUMMARY_MODEL`, `JUDGE_MODEL` and `LLM_PRICES` are set in its environment. An empty line in `.env` counts as not set.
**How I'd say it in an interview.** "LangChain guesses a provider from a bare model name, and for Gemini it guesses Vertex AI, which is the wrong product, so a typo-sized config mistake could send prompts somewhere unexpected. Making the provider mandatory turns that into a startup error. For the defaults, I followed the twelve-factor idea: defaults are a developer convenience, but production must be configured on purpose. The cost cap reads the price table, and that price changes on a known date, so a deployed system silently running on a price someone typed into the code months ago is exactly the kind of quiet drift I want to rule out. Failing at startup is cheap; finding out from a wrong bill is not."
**Rejected.** Removing the defaults (every write on a fresh laptop would go to human review with no warning), and checking the provider against LangChain's private provider list (private API; a misspelt provider already fails safe when the model is first built).

### 2. Clean up what you might have created, not only what you know you created
**What we did.** A preview creates a Neon branch, and we already deleted it on any failure once we knew its ID. The audit found the gap before that: the create request times out, is cancelled by our own timeout, gets a server error or an unreadable answer. Neon may have made the branch, but we never learned its ID. Now the client looks the branch up by its unique name (`shadow-<action_id>`, compared exactly because Neon's search matches partial names) and deletes it, then re-raises the original error.
**How I'd say it in an interview.** "With a remote create, 'it failed' and 'it was not created' are different things: a timeout only tells you the answer didn't arrive. Because we name each branch after the action, the name works like an idempotency key, so we can find our own orphan without guessing. The project has only 10 branch slots, so one leak per timeout would turn into 'busy' errors for everyone. The cleanup is best effort, shielded from cancellation and capped at 10 seconds, and the 15-minute expiry plus the startup sweep are still there as backstops."
**Rejected.** A new delete-by-action method on the `NeonClient` interface called by the shadow runner (only the API client has this gap; the local fake already drops its copy on failure).

## Actions read routes (Stage 3 pull-forward, D-168 to D-171)

### 1. A stranger's action and a missing action look exactly the same
**What we did.** Every action query starts from `visible_actions(user)`, which adds `WHERE user_id = me` unless the caller is an admin. The detail route answers an unknown ID, a malformed ID and someone else's ID with the same 404 and the same body. The list route cannot return another user's rows at all, and its `total` goes through the same filter. A test table (the authorization matrix) calls every `/v1` route as anonymous, owner, other member and admin, and fails if a route has no row.
**How I'd say it in an interview.** "If I return 403 for someone else's action and 404 for a missing one, I've told an attacker which IDs exist. So both are 404. The filter lives in one function that every query starts from, so a new route can't forget it, and the matrix test makes forgetting a route a red test. The dashboard just shows what the backend returns; it holds no permission logic."
**Rejected.** A 403 for other people's actions (it leaks existence), and checking ownership in each route (one forgotten check is a data leak).

### 2. The API never returns raw SQL, and it fails loudly on data it does not understand
**What we did.** The response models have no `sql` or `pinned_sql` field at all, so raw SQL cannot leave by accident; only the masked `sql_display` does. Agent-written text (the intent, the first line of a knowledge chunk) is masked again on the way out. The big stored JSON blocks (impact, judge, rules) are parsed strictly against the shapes their writers produce. If one does not match, the request is a 500 and the log names the field, never the value. Tests feed each writer's real output into the views, so a writer cannot change its format unnoticed.
**How I'd say it in an interview.** "The safest field is one that does not exist, so the models simply have no raw SQL. For stored JSON I chose to fail rather than guess: if the impact block is broken, showing a reviewer an action without its impact would invite a blind approval. The error message holds no values because the data may be personal. The contract tests are what keep the writers and the page in step."
**Rejected.** Hiding a broken part and showing the rest (a reviewer would not know something was missing), and trusting write-time masking alone (agent text reached the UI unmasked before).

## Step 14: the write workflow (D-180 to D-186)

### 1. The pipeline's saved state never holds the agent's raw text
**What we did.** Every write runs through one LangGraph graph that saves its state to Postgres after each step, so a paused review survives a restart. That saved state holds only masked SQL (`sql_display`), the masked intent and a summary of the preview (counts and a few masked samples). The raw SQL, the raw intent and the 246,000 per-row changes of a big delete are kept out. A node that needs the exact SQL reads it from the `actions` table.
**How I'd say it in an interview.** "The framework writes its whole state into a checkpoint after every step and also sends it to the tracing tool. If the state held the agent's raw SQL, an email address inside a WHERE clause would end up in checkpoints and traces, unmasked. So the state carries only things that are already safe, and the one place that holds the real SQL is the table we already protect. A test plants an email in the SQL and the intent, runs a write, and searches every log line, checkpoint and model prompt for it. It also keeps checkpoints small: we save counts, not 246,000 rows, several times per run."
**Rejected.** Putting the full classification and diff in the state (simpler, but it leaks and bloats), and registering model classes with the serializer (keeps raw text in checkpoints).

### 2. Every step can fail, and failing always means "blocked", never "let through"
**What we did.** Every node except the human-review pause runs inside one wrapper. Any exception marks the action `failed` with decision `block`, writes a `fail_closed` audit event and a log line with a reason code, and ends the run normally. The one exception to "errors block" is the AI judge: if only the judge fails (or is skipped for cost, or has no knowledge to read), the write goes to a human instead, because rules have already passed. When a reviewer's approval comes back, the commit step re-reads the row and does nothing unless the status is still `approved`.
**How I'd say it in an interview.** "A safety gateway must be safe when it breaks, not just when it works. So there is no path where an error quietly lets a write through: one wrapper turns every error into a blocked action with a reason, and a test breaks each node on purpose to prove it. I also don't trust the resume signal on its own. Between the reviewer's click and the commit, the review might have expired or been rejected, so the commit checks the database row again. The database is the source of truth, the graph is just the sequencer."
**Rejected.** Letting exceptions reach the caller (a crash could leave a half-processed write with no record), and trusting the resume value alone (a stale or duplicate resume could commit an expired approval).

## Approve and reject routes (Stage 3 pull-forward, D-192 to D-194)

### 1. The database decides who wins, not the route and not the graph
**What we did.** The routes are thin. They call `WorkflowRunner.resume`, which claims the decision with one conditional UPDATE: the row must still be `awaiting_review`, not past `expires_at`, and visible to the caller. Only the caller whose UPDATE changes a row gets a 200 and resumes the paused graph. Everyone else gets a 409 that names who decided, or a 404 if the action is not theirs.
**How I'd say it in an interview.** "Two people can click Approve at the same moment, and an agent's SQL must be committed at most once. I did not check the status in Python and then write, because both requests would pass the check. One UPDATE with the condition in its WHERE clause lets Postgres pick a single winner through its row lock. The expiry is in the same statement, so a review can never be approved after its deadline just because the cleanup job has not run yet. The graph never decides who may approve; it only continues after the database has already said yes."
**Rejected.** Read-then-write in the route (a race), and letting the graph's resume value be the authority (a stale or duplicate resume could commit an expired approval).

### 2. Errors that tell the caller only what they may know
**What we did.** Another user's action, an unknown ID and a malformed ID all return the identical 404 body, and a malformed ID is answered before the request body is checked. While the runner is starting up or draining, every caller gets a 503 whatever the ID. A loser gets a 409 naming the winner's `display_name`. The reject reason is validated (stripped, 10 to 500 characters), stored as written, never logged, and a 422 never echoes it.
**How I'd say it in an interview.** "Every error is a small information leak if I am careless. A 403 for someone else's action would confirm the ID exists, so it is a 404 that looks exactly like a missing one. During a restart I answer 503 for everything, so timing cannot reveal which IDs are real. The 409 does name the person who decided, because a reviewer needs to know who got there first, and only people allowed to see the action ever reach that branch. The reason is the reviewer's own message to the agent, so I keep it intact but out of logs and error bodies."
**Rejected.** A 403 for other users (leaks existence), checking visibility before runner availability (leaks during restarts), and masking the reason (it would change what the agent reads).

## seed-demo-actions (Stage 3 pull-forward, D-195 to D-198)

### 1. Seed through the real pipeline, not by inserting rows
**What we did.** The command creates actions by starting them in the real `WorkflowRunner`: real classifier, rules, shadow run on `FakeNeonClient`, retrieval, impact engine, judge and graph. Rejections and approvals go through `runner.resume`. Expiry uses a graph whose clock is behind, so `request_review` stores an already-past `expires_at`. Because of this, `approved` and `drifted` are not seeded: the real graph cannot rest in them yet.
**How I'd say it in an interview.** "Demo data that is typed in by hand lies about how the product behaves. If I seed through the real graph, a pending action has a real checkpoint, so approving it in the dashboard exercises the real resume path, and the numbers on it were measured by a real preview. The price is that I only seed states the pipeline can really reach, and I wrote that down instead of faking the rest."
**Rejected.** Inserting rows directly (no checkpoint, so Approve would fail) and seeding `approved` or `drifted` rows by hand.

### 2. Scripted models behind the real engines, worded without numbers
**What we did.** Only the model's words are faked. `ScriptedModelFactory` plugs into the same `model_factory` seam the engines already have and picks a script by `action_id`. The scripts contain no digits or number words, so the number guardrail passes on the measured facts, and every number a reviewer reads comes from `actions.impact`. It lives in `app/` because the CLI cannot import `tests/`.
**How I'd say it in an interview.** "Code counts, the LLM explains. So the fake LLM never states a number; it can only say things like 'removes rows and the rows that depend on them'. That keeps the demo honest, because the real guardrail, parsing and cost accounting still run, and the seed cannot drift out of date when the data changes."
**Rejected.** A fake in the test folder (the CLI cannot import it) and wording with numbers (it would go stale and fail the guardrail).

## Step 16: the commit (D-199 to D-205)

### 1. Commit only what was previewed: check, run, compare, then commit
**What we did.** The commit opens one company transaction and, before anything is applied, locks the approved rows and compares each row's hash with the preview, then counts the rows the foreign keys would remove or null and compares those too. Only then does it run the approved SQL, with a `RETURNING` that reports every row it touched (old and new key and hash), and compares those keys and counts with the preview. Any difference rolls back, previews again once and sends the write back to a person; a second difference fails it. Numbers only are stored about a drift, never keys or values.
**How I'd say it in an interview.** "The owner approved a picture of the data, not a statement. Between the preview and the commit the data can move, so the commit has to prove the world still looks like the picture, and if it does not, it must change nothing. I check before running, check again on what actually happened, and only then commit. After a drift the new picture always goes to a person: the old approval was for different data."
**Rejected.** Trusting the row count alone (a nulled child row changes nothing in the count), `RETURNING <pk>` only (an update that changes nothing would look like drift), and auto-approving after a re-preview.

### 2. The marker row decides what happened, so a crash can never apply a write twice
**What we did.** The first statement inside the company transaction inserts the action's id into `blastproof.applied_actions`, so the marker commits or rolls back with the write itself. Before-images are saved to Blastproof first. If the process dies, or the COMMIT answer is lost, the action stays `committing` and recovery looks for the marker: present means it happened (just write `committed`), absent means it did not (run the whole commit again, which is safe). A failure writing `committed` afterwards never marks the action failed.
**How I'd say it in an interview.** "When a connection drops during COMMIT you genuinely do not know whether it happened. Guessing either way is wrong: calling it failed could hide a write, and retrying could double it. So I make the answer something I can look up: a row written in the same transaction as the change. The database tells me what happened, and the commit is idempotent by construction."
**Rejected.** Marking the action failed on any COMMIT error (it may have happened), an in-memory flag (lost in a crash), and an exclusive lock for the commit (it would serialise reads; a shared lock keeps clones out, which is all that is needed).

## Step 17: wiring up `query_database` (D-206 to D-214)

### 1. One function turns stored state into the agent's message, and it is shared
**What we did.** `query_database` and `get_action_status` both read the same small snapshot of the action (`AgentView`: status, reviewer note, drift count, whether the AI check was unavailable, the committed row count) and pass it to one pure function, `outcome_for`. That function picks the template. Nothing is remembered in the request: if the agent asks again ten minutes later, or the server restarted in between, it gets the same wording the first call would have. The numbers in "Done: 3 rows deleted in users" come from the commit's own count, or the computed preview, never from a model.
**How I'd say it in an interview.** "The agent's reply is a function of what is in the database, not of what happened to be in memory during one request. So the two tools cannot disagree, a restart cannot change the story, and I can test every outcome with a table of stored states and no Docker. It also keeps the rule 'code counts, the LLM explains' honest: the number the agent sees is the number we stored."
**Rejected.** Building each message inside the code path that produced it (two tools would drift apart, and a poll after a restart would have nothing to say), and letting the summary model word the reply (it could state a wrong number).

### 2. The write rate limit counts what was recorded, under a lock, and records nothing when it says no
**What we did.** Each `query_database` call marks its `received` audit event as a read or a write. Before a write creates its action, the call takes a per-agent database lock, counts that agent's writes in the last 60 seconds (using the database's clock), and either refuses with `busy` and the exact seconds to wait, or creates the action in the same transaction. Over the cap, no action is created. The same "record nothing" rule applies when the workflow is not accepting writes (starting up or shutting down).
**How I'd say it in an interview.** "A looping agent must not be able to fill the audit table, so a refused call leaves no row. But counting and inserting are two steps, and thirty calls at once would all see 'twenty-nine' and pass. A lock keyed on the agent makes them one step, and a test fires six calls at a cap of three and checks that exactly three actions exist. I count the event's marker rather than the statement type because a SELECT that calls an unknown function is a write."
**Rejected.** Counting by `statement_type` (misses disguised writes), an in-memory counter (lost on restart, wrong with two processes), and recording refused calls as failed actions (the flood we wanted to stop).

## F1: the dashboard scaffold (D-215 to D-218)

### 1. Every rule about the scaffold is a test, not a note
**What we did.** The things that must stay true about the dashboard's foundation are checked by small pure functions that return a list of problems: every dependency is an exact version, TypeScript is strict, the security headers cover every route, `.env.example` holds exactly three server-side names and none starts with `NEXT_PUBLIC_`, and nothing under `src/` uses `dangerouslySetInnerHTML` or `localStorage`. Each function also runs on a deliberately bad example and must complain, so a check that can never fail cannot sneak in. The CI workflow has the same treatment in a backend test: no secrets, actions pinned to a commit, steps in the right order.
**How I'd say it in an interview.** "A rule in a markdown file is a wish. The rules for the frontend are about not leaking secrets to the browser and not rendering agent text as HTML, and those are exactly what a tired developer breaks by accident later. So each one is a test that fails the build, and each test is proven able to fail by feeding it a bad example. I broke four of them on purpose and watched them go red."
**Rejected.** Writing the rules only in CLAUDE.md (nothing enforces them) and one big snapshot test (it fails for unrelated changes and teaches people to update the snapshot without reading it).

### 2. Pinned exactly, with the CI job in its own workflow file
**What we did.** `.npmrc` makes `npm install` save exact versions, and a test rejects any `^` or `~`. The lockfile pins everything below that. The frontend job lives in `frontend.yml`, with a Node version taken from `.nvmrc` and actions pinned to commit hashes, instead of being added to the backend `ci.yml`, whose tests allow exactly two jobs.
**How I'd say it in an interview.** "This tool sits between an AI agent and a customer's database, so a surprise dependency update is a security risk, not just an annoyance. Exact versions mean the code I tested is the code that ships, and an update is a visible diff someone reads. A separate workflow file meant I added a job without loosening a guard on the backend one."
**Rejected.** Caret ranges with a lockfile alone (a plain `npm install` would still move them) and putting the job in `ci.yml` (it would have meant editing a test that protects the backend job list).

## F2: the typed API client (D-221 to D-223)

### 1. The backend's OpenAPI file is the only source of API types, and the compiler is the test
**What we did.** `npm run gen:api` turns the running backend's `/openapi.json` into `schema.d.ts`, and the `openapi-fetch` client is typed from it. A wrong path, a missing parameter or a misspelled field is a TypeScript error. The tests include `@ts-expect-error` lines that must keep failing to compile (the old plan path `{id}`, an unknown path, a missing `action_id`, a field that does not exist).
**How I'd say it in an interview.** "We never write API types by hand, because hand-written types drift from the server and you find out in a reviewer's browser. The backend already describes itself, so we generate the types from that description and let `tsc` catch mistakes. I also test the negative: lines that must not compile, so if the types ever become loose the typecheck goes red."
**Rejected.** Hand-written interfaces (drift), a shared types package (the backend is Python), and runtime schema validation on every response (cost, and it checks the wrong moment).

### 2. One query client per server request, one shared in the browser, with refetch rules written down
**What we did.** `getQueryClient` returns a fresh client on the server and a single cached one in the browser. `refetchOnWindowFocus: true` and `refetchIntervalInBackground: false` are set explicitly even though they are TanStack's defaults, and tests prove polling stops while the tab is hidden and resumes on focus.
**How I'd say it in an interview.** "A server-side cache that outlives a request could hand one user's data to another, so on the server every render gets its own client. In the browser one shared client is what makes the cache work. The two refetch options are defaults today, but this is a review dashboard that must not poll a hidden tab, so I pin them in code and test them rather than trusting a library default to stay put."
**Rejected.** A module-level client everywhere (cross-user leak risk on the server) and relying on the defaults without a test.

---

## Step F3: Proxy and login

### 1. The browser never holds the token: a same-origin proxy keeps it in an httpOnly cookie

**What I did.** The dashboard never calls the backend directly. The browser talks only to its own
origin, at `/api/blastproof/*`. A small server-side route there reads the session cookie, adds
`Authorization: Bearer <token>`, forwards the call to the backend, and hands back the backend's
answer unchanged. Login works the same way: the login route calls the backend, puts the token in
a cookie marked `HttpOnly`, `SameSite=Lax` and (in production) `Secure`, and returns only the
person's name and role to the page. Page JavaScript cannot read the token at all.

**Why.** If the token sat in `localStorage` or a normal cookie, one XSS bug in any page would let
an attacker walk off with the whole session. With `HttpOnly`, the worst a script injection can do
is make requests while the page is open, not steal the login. The proxy is also where I keep the
backend address, so the browser never learns it, and where I enforce two cheap guards: only `/v1/*`
can be reached (so `/docs`, `/mcp` and friends are not exposed), and every POST or DELETE must
carry an `Origin` header equal to the dashboard's own, which stops another website from making
your browser act on your behalf (CSRF).

**What I rejected.** Calling the backend straight from the browser with a token in memory or
`localStorage`. It is less code, but it makes every XSS bug a session theft and needs CORS
configured on the backend. I also rejected forwarding the browser's own headers: the proxy uses an
allowlist (content type, accept, and the Bearer it adds itself), so a cookie, a fake
`Authorization` or an `X-Forwarded-For` from the browser can never reach the backend.

**Trade-off and limits.** The dashboard server is now in the path of every call, so there is one
more hop and one more thing that can be down (it answers 502 or 504 with a clear error). The
cookie lasts a fixed 8 hours and the dashboard never checks the token itself: the backend does,
and when it says 401 the proxy clears the cookie so a dead session cannot loop. Logging out only
clears the cookie; the token stays valid on the backend until it expires (D-041). All logins look
like one IP to the backend's rate limiter until the deploy step forwards the real one.

### 2. Two gates, and each one fails closed: a cheap redirect up front, the real check in the backend

**What I did.** A proxy file (`src/proxy.ts`) runs before every `/dashboard` request and only asks
"is there a session cookie?". No cookie: redirect to `/login?next=...`. It does not validate the
token. It also stamps each response with a fresh random nonce in a Content-Security-Policy, so only
scripts Next itself marked with that nonce can run. The real authorization stays in the backend:
every call is checked there, and a 401 anywhere sends the user back to login. If the session
config is broken, the proxy treats it as "no session", never as "let them in".

**Why.** The proxy gate is for experience (no flash of a dashboard you cannot use), not security.
Putting token validation in two places would mean two places to get wrong and a second copy of the
auth rules. The CLAUDE.md rule is that the dashboard holds no business logic and the backend
enforces every permission, so the proxy is deliberately dumb. The nonce CSP is the second layer
for the XSS case from choice 1: even if someone injects markup, an inline script without the
nonce will not run.

**What I rejected.** Verifying the JWT in the proxy (duplicate logic, needs the signing secret in
the dashboard). And a strict style nonce: the docs example nonces styles too, but then
`'unsafe-inline'` is ignored and libraries that inject `<style>` (toasts, devtools, Radix) break.
I kept `'unsafe-inline'` for styles only, because script injection is the real risk.

**Trade-off and limits.** A user with an expired cookie gets through the proxy once, and is bounced
by the first API 401. That is a harmless extra step. Pages must be rendered per request to get a
nonce, so they cannot be static: the public landing page stays static and gets its own, weaker CSP
in F13. I verified this with the production build and `curl`, but not yet in a real browser's
console.

## F4. Layout and navigation

### 1. The sidebar's role-based links are a convenience, and they fail toward "less", not "more"

**What I did.** Knowledge and Users are marked `adminOnly` in one nav table, and `navFor(role)`
filters on it. While the user's role is still loading, or the `/me` call failed, the role is
`undefined`, and `undefined` gets the member view. So an admin link can never flash up before we
know who the user is.

**Why.** CLAUDE.md says the dashboard holds no business logic and the backend enforces every
permission. Hiding a link is only to avoid showing a member a page that would say "forbidden". If
the role check is wrong or missing, the worst case is a visible link that the backend still
refuses. That is why the default for "unknown" is the smaller menu.

**What I rejected.** Trusting the role for anything else (for example, skipping a backend call), and
showing all links until the role arrives (a flash of admin links is a small leak of what exists).

**Trade-off and limits.** An admin sees a shorter menu for a split second on first load. I accepted
that over the reverse. The stub pages behind the links do no authorization yet; the real pages in
later steps rely on the backend answering 403 or 404.

### 2. The badge is a cheap query under the shared `["actions"]` key, and logout only navigates after the server says yes

**What I did.** The pending count asks for one row (`limit=1`) with `status=awaiting_review` and
reads the `total`, so the backend counts and the dashboard computes nothing. It polls every 5 s
and is stored under `["actions", "pending-count"]`, so when F7 approves or rejects something and
invalidates `["actions"]`, the badge refreshes at once instead of waiting for the next poll.
Logout posts to the route and goes to `/login` only if the answer is a 2xx; otherwise it stays put
and shows an error toast.

**Why.** Polling is simple, works through the proxy, and needs no new infrastructure; the plan sets
5 s. Reusing the key prefix means one invalidation updates every list and the badge. For logout,
the dangerous failure is a UI that looks logged out while the cookie is still live, so the UI never
claims a logout the server did not confirm.

**What I rejected.** Server-sent events or WebSockets (more moving parts for an MVP), counting rows
in the browser (it would break the rule that numbers come from the backend), and navigating to
`/login` regardless of the result (looks tidy, can hide a failure).

**Trade-off and limits.** The badge can be up to 5 s stale, and polling costs one small request per
open tab (it pauses in a hidden tab). An admin's badge counts every user's pending actions because
the backend widens admin visibility; I did not change that. Logout still only clears the cookie;
the token stays valid on the backend until it expires (D-041).

## F5 · Shared components

### 1. Times start as UTC on the server and switch to local time in the browser, from one shared clock

**What I did.** `useNow()` is a tiny shared clock built on React's `useSyncExternalStore`. On the
server and during hydration it returns `null`, so `TimeAgo` and `LocalTime` print the time in UTC.
Once the browser takes over it returns a real `Date`, and the same components switch to the user's
own time zone and to text like "3 minutes ago". One `setInterval` (30 s) serves every timestamp on
the page; the first component to mount starts it and the last to unmount clears it. All formatting
goes through `lib/format.ts`, and a source-rule test fails if any other file calls `Intl` or
`toLocale*`.

**Why.** The server does not know the user's time zone. If it printed one thing and the browser
another, React would report a hydration mismatch. Starting from a value that is the same on both
sides avoids that without hacks. A single shared timer avoids a list of 25 rows each running its
own timer.

**What I rejected.** Next's documented fix is an inline script with `dangerouslySetInnerHTML`, which
breaks our nonce CSP and our own source rule. `suppressHydrationWarning` alone hides the symptom.
`date-fns` would add a dependency for something `Intl` already does.

**Trade-off and limits.** For one render the user sees UTC before local time. Today that cannot be
seen anyway, because data only arrives after the page loads. Relative text can be up to 30 s stale.
A timestamp with no `Z` or offset is shown as "—" rather than guessed, so a backend contract break
shows up instead of silently displaying the wrong hour.

### 2. Components never trust what they are given: unknown values show raw, errors show only what the backend said, text is always text

**What I did.** `STATUS_META` and `RISK_META` are typed `Record<ActionStatus, ...>`, so when the
backend adds a status and we regenerate the API types, `typecheck` fails until we label it. At
runtime, an unknown status or risk level still renders (raw text, neutral colour) instead of
crashing. `ErrorState` prints an `ApiError`'s message (the backend's own envelope text) but gives any
other error the generic "Something went wrong.", so an internal message or stack never reaches the
screen. `DataTable` keeps old rows visible when a background refresh fails and adds an alert,
instead of blanking the page. `SQLBlock` and `AuditTimeline` render agent-provided text as React
text, never as HTML.

**Why.** The dashboard shows data written by agents, and the backend can be newer than the
frontend. A page that breaks or leaks detail on surprising input is worse than one that shows a
slightly plain badge. The compile-time check makes the frontend and backend drift loud while
building, and the runtime fallback keeps it harmless in production.

**What I rejected.** Showing `error.message` for every error (can leak internals), clearing rows on
a failed poll (the review queue would flash empty), and a default colour for unknown statuses that
looks meaningful (it would imply a state we do not know).

**Trade-off and limits.** Colour is only a hint: the label always carries the meaning. Dark-mode
and contrast checks are F12. Nothing here enforces permissions; the backend does.


## Step 19 · Dashboard API (D-239 to D-247)

### 1. The upload is checked in a fixed order, and the body is not read until the caller has proved who they are and how big the file is

**What I did.** `POST /v1/knowledge` has no `File` parameter. The route first checks that the caller
is an admin, then reads the `Content-Length` header (missing is 411, over 1 MB plus 64 KB is 413),
and only then asks Starlette to parse the multipart body, with a limit of one file and 21 fields.
After that it checks the file name (a plain `.md` or `.txt` name, never `schema_scan`), the 1 MB
file size, UTF-8, the table tags, and that the document is not empty.

**Why.** FastAPI reads and parses a body before it runs the dependencies that check who is calling,
and Starlette puts file parts in a temporary file with no size cap. With a normal `File` parameter,
a stranger with no login could make the server write a huge file to disk before it said "403". Doing
the checks by hand costs a few lines and a hand-written OpenAPI description, and it means the cheap
checks (who, how big) always run before the expensive one (reading the body). An empty document is
refused because "replace this source with nothing" would silently delete it.

**What I rejected.** A `File` parameter followed by a size check (too late), and trusting the proxy
to limit size (the API must protect itself).

**Trade-off and limits.** A chunked upload with no length is refused (411), so the dashboard's
proxy has to buffer and send a length. A client that lies about its length is stopped by the web
server, not by this code.

### 2. Who may see what is decided inside the query, and a test fails if a route has no row in the permission table

**What I did.** Agents are read through `list_visible_agents` and `get_visible_agent`, which add
"owned by you" unless you are an admin, exactly like `visible_actions` for actions. Someone else's
agent therefore looks the same as an agent that does not exist: 404, never 403. Stats are computed
from `visible_actions`, so a member's numbers can only ever count their own actions. Admin-only
routes (users, knowledge changes, system scans) use the `require_admin` dependency. Every route has a
row in the authorization matrix (anonymous, owner, another member, admin), and a guard test fails if
a new `/v1` route has no row.

**Why.** If each route remembers to filter, one forgotten `if` leaks data. Putting the filter in
the one query function that every route uses means a route cannot read the data without it. Answering
404 for both "not yours" and "not there" means an attacker cannot learn which IDs exist. The
matrix test makes the permission table something the build checks, not something people remember.

**What I rejected.** Loading the row first and comparing the owner in the route (easy to forget, and
leaks existence through 403), and showing every user's email to every signed-in user (BE-118).

**Trade-off and limits.** Admins see emails on `/v1/users`; that is a deliberate exception (D-243).
The matrix proves each route's answer for four roles, not that every query inside is correct, so
each list route also has its own test. Hiding buttons in the UI is a convenience; this is the
enforcement.

## Step 20 · Bootstrap, OpenAPI export and README (D-248, D-249)

### 1. `bootstrap` is a chain of get-or-create steps, so it is safe to run again

**What I did.** One command creates the first admin, an agent, the `company` system, the schema
scan and the schema docs, in that order. Every step first looks for what it would create. An
existing active admin is reused (and the password is never asked for), an existing agent is reported
by its key prefix only, and the scan and docs replace their own chunks instead of adding more. If the
company database is down at the scan, everything before it is already saved, and running the command
again finishes the job. Two cases stop with a clear message instead of "fixing" things: an existing
user that is not an active admin, and a main system stored with a different connection profile.

**Why.** First-run setup fails half way in real life (a database not up yet, a mistyped URL). If the
command could only run once on an empty database, a failure would leave a person hand-deleting rows.
Making each step safe to repeat turns "it broke" into "run it again". The key is the other reason:
only a hash is stored, so it can be shown once, at creation. A re-run must therefore never pretend to
create it again.

**What I rejected.** A "reset and start over" flag (it deletes data, which this project asks
permission for), and quietly rewriting a system row with the wrong profile (actions recorded against
one company database would then look as if they belonged to another).

**Trade-off and limits.** A lost key cannot be recovered by bootstrap; you create another agent. Two
bootstraps at the very same moment could race on the admin email; the unique index makes one of them
fail with a clear error, not create a duplicate.

### 2. The API contract is a committed file, and each CI job checks its own side of it

**What I did.** `export-openapi` builds the app with fixed settings (no `.env`, no database) and
writes `backend/openapi.json` in a stable format. A fast backend test fails if that file differs from
what the routes produce. The frontend job regenerates `schema.d.ts` from the same file and fails on
any diff. So a route change without a re-export fails the backend job, and a re-export without
regenerated types fails the frontend job.

**Why.** The frontend's types are generated from the backend, and nothing else notices when they
drift apart until the dashboard breaks at runtime. Generating from a file, not a running server,
means neither CI job needs the other's tools (no Python in the Node job, no server in either). The
fixed settings make the output the same on every machine, so a diff always means a real change.

**What I rejected.** Starting the backend in the frontend job (slow, needs a database), and checking
only the types (it would not say which side forgot to update).

**Trade-off and limits.** The file is one more thing to commit after a route change; the failing
test names the exact command to run. Line endings matter: the fresh-clone check found that Windows
turned the file into CRLF, so `.gitattributes` now pins it to LF.

---

## Evals stage (D-250 to D-254)

### 1. Two eval modes: a cheap deterministic one that gates every pull request, a live one that measures

**What I did.** `evals.run` has a rules mode and a live mode. Rules mode needs no Docker and no
model. It takes each scenario, runs the real classifier and the real policy rules, and for the second
rules pass it uses the row counts the scenario file already states (those counts are proven against
the real seed by other tests). Hook scenarios go through the real hook rules. It runs in a few
seconds, so CI runs it on every pull request and fails if any incident replay is not caught.
Live mode runs the whole pipeline: real preview on a copy of the database, retrieval, the summary
model and the judge. It is slow and costs money, so it runs nightly and by hand.

**Why.** A safety gate that people skip because it is slow or costs money is not a gate. The part
that must never regress (the rules catching the incidents) needs no model at all, so it can run
everywhere for free. The part that depends on a model needs a real run, but it can run less often.

**What I rejected.** Running a real preview for every scenario on every pull request (10 to 15
seconds each), and a gate that also needed a model key (a fork's code could then reach the key).

**Trade-off and limits.** Rules mode trusts the labeled counts instead of measuring them; a test
that measures them for real (`test_shadow_scenarios`) keeps that honest. An error, a timeout or a
missing label counts as a failure, never as a pass. The live mode has not yet produced real
explanation or judge numbers: the model API answered "quota exhausted" on every call.

### 2. The live eval changes nothing and cannot touch production

**What I did.** Live mode refuses to start in production, on any Neon profile, with a seed whose
anchor date differs from the scenarios, or with an empty knowledge base. It uses the stub commit, so
the company database is never changed, and it acts as its own member account (`evals@example.com`),
never as a real user. Time is frozen at the scenario date so "six months ago" means the same thing
the labels were measured with.

**Why.** An eval that runs real writes is itself a risk. The rule is: measure the pipeline, not the
data. Failing before connecting, with a message that holds no URL, is safer than a half-run.

**What I rejected.** Reusing a demo user's account (it would pollute their history) and a "dry run"
flag (a flag can be forgotten; a refusal cannot).

**Trade-off and limits.** The eval's actions do appear in a local admin's History. Write latency is
Blastproof's own work time (classify to decision), not the real commit or the tool layer, and read
overhead on my machine (165 ms at p95) misses the 50 ms target.


## Security pass and observability (D-256 to D-258)

### 1. A redactor behind the allowlist, not instead of it

**What I did.** Call sites still log and send only fields chosen for the purpose. Behind them, one
redactor (`app/services/redaction.py`) runs on every log line after the traceback is formatted, and
on every LangSmith input, output, metadata field and error string. It replaces secrets by shape (URL
passwords, `bp_` keys, JWTs, `Bearer`, `password=` pairs), secrets by value (every secret the process
holds), and emails and phone numbers.

**Why.** An allowlist protects against what we thought of. Uvicorn, httpx and langsmith write their
own text, and a future call site can slip. A second layer that does not trust the first means one
mistake is not a leak. Matching known values exactly catches a key in a shape no pattern knows.

**What I rejected.** Redacting only at call sites (nothing catches a slip), and keeping the old rule
that forbade pattern masking of logs (it left third-party tracebacks unprotected).

**Trade-off and limits.** Over-masking: a 7 to 15 digit run in a trace may become `[phone]`. Secrets
under 12 characters are not registered as values, or a password like `company` would blank ordinary
words. The LangSmith dataset examples are not covered; they come from committed, synthetic data.

### 2. One tracing client, installed always

**What I did.** `configure_tracing` builds a single LangSmith client with the anonymizer and installs
it as the process default, whether tracing is on or off. Every LangChain and LangGraph run then uses
it without being told.

**Why.** The switch is an environment variable that can change later. If the masked client only
existed when tracing was on, a trace could go out through LangChain's default, unmasked client.
Installing it unconditionally makes the safe path the only path.

**What I rejected.** Wrapping each model call with its own masking (easy to forget on the next one),
and building the client only when `LANGSMITH_TRACING` is set.

**Trade-off and limits.** The client starts a background thread, so it is created once per process.
A real-LangSmith check is still a manual step: tests prove nothing leaves the client unmasked, but
only opening a live trace shows it end to end.


---

## Deploy: the backend on Fly.io

### 1. A tiny, locked-down image that cannot leak what it never contained

**What I did.** The backend ships as a Docker image built from an *allowlist*. The `.dockerignore`
starts with `*` (exclude everything) and then names only the folders the app needs. It runs as a
normal user, not root. The embedding model is downloaded while the image is built, so the running
server never needs the internet to start. Every base image is pinned to an exact digest. A smoke test
starts the image with no network and no secrets and checks that it answers, that production refuses
to start without its secrets, and that no `.env`, tests or evals are inside.

**Why.** Blastproof's promise is that credentials stay out of places where they can leak. A normal
`.dockerignore` lists what to leave out, and the one thing you forget is the one that ends up in the
image. An allowlist turns that around: a new file stays out until someone decides it belongs.

**What I rejected.** A blocklist `.dockerignore`, and downloading the model on first start (slow, and
it needs the network, so a Hugging Face outage could keep the server from starting).

**Trade-off and limits.** Rebuilding is slower when the dependencies change. A few of the tests only
read the Dockerfile; the real proof is the smoke script in CI and the live probe.

### 2. Exactly one machine, with a graceful stop and a health check that does not depend on anyone else

**What I did.** `fly.toml` runs one machine, always on, and every deploy passes `--ha=false`. When Fly
stops it, it sends SIGTERM and waits 60 seconds. Uvicorn takes 10 for open connections and the app
gives running writes 45 to finish. Fly's health check calls `/health`, which only says the process is
alive. It does not call `/ready`, which also checks Neon and the models.

**Why.** Blastproof keeps state in one process: the write runner, recovery and the sweep. A second
machine would run a second copy of each and they would trip over each other. Stopping gently means a
deploy does not cut a write in half. Keeping the health check shallow means a Neon outage cannot make
Fly restart the app in a loop and make a small outage worse.

**What I rejected.** Two machines for high availability (needs a real lock design), and pointing the
health check at `/ready`.

**Trade-off and limits.** One machine means a short gap during each deploy and no failover. That is
fine for a demo, and the first live run showed the cost of a mistake here: on 512 MB the server was
killed for lack of memory, so the machine stays at 1 GB.

## F6: Review queue (D-270)

### 1. The page polls every 5 seconds, and all filtering and ordering stays in the backend

**What I did.** The queue asks `GET /v1/actions?status=awaiting_review&order=oldest&limit=25` and
repeats the request every 5 seconds with TanStack Query's `refetchInterval`. When the answer changes,
React redraws the table. The page sends no user or owner parameter, does not re-sort and does not
filter the rows.

**Why.** A held write has to show up without anyone pressing refresh, and polling is the simplest way
that needs no new backend work. Keeping the filtering in the backend follows the rule that the
dashboard holds no business logic: `visible_actions(user)` already decides whose actions a person
sees, so the page cannot show something it should not, even if the front-end code has a bug.

**What I rejected.** Server-sent events or WebSockets (instant, but they need a new backend channel
and more moving parts for a demo), and filtering by owner in the browser (a second place to get
permissions wrong).

**Trade-off and limits.** A new row can be up to 5 seconds late, and every open page makes a small
request every 5 seconds. An admin sees everyone's actions with no owner column, because the API does
not return one.

### 2. One shared list hook under the `["actions"]` key, with the old page kept while the next loads

**What I did.** `useActions` is a shared hook whose query key is `["actions", "list", query]` and
which uses `keepPreviousData`. F7 will invalidate `["actions"]` after an approve or reject, which
refreshes the queue and the sidebar badge together. If the owner clicks Next, the current rows stay
on screen until the next page arrives. If decisions shrink the queue below the page being viewed, the
page steps back to the last page that exists.

**Why.** One key prefix means one invalidation updates every list, so the badge and the queue cannot
disagree for long. Keeping the old rows avoids a flash of skeleton lines on each click and each poll.
The hook is reused by History (F8) and Overview (F9) rather than copied.

**What I rejected.** A separate request key per page, with no shared prefix (each decision would
need to know about every list), and putting the offset in the URL (the queue has no filters, so F8
builds URL state once, where it is needed).

**Trade-off and limits.** The offset is local state, so reloading the page returns to page 1.
"Waiting" counts from `created_at` because the API has no review-requested time.

## F7: Change request page (D-271)

### 1. The page keeps polling until the action is final, not just while it is pending

**What I did.** The page asks `GET /v1/actions/{id}` every 5 seconds, and stops only when the
status has no way forward (rejected, expired, blocked, committed, executed, failed) or the answer is
a 404. A table in the code lists every status as final or not, typed so that a status the backend
adds later breaks the build instead of silently polling forever. Approve and Reject both invalidate
the whole `["actions"]` key, on success and on error, so the page, the queue and the sidebar badge
refresh together.

**Why.** The reviewer's real question after clicking Approve is "did it land?". Stopping at
"not pending" would freeze the page on "Approved" and hide the commit. Refreshing on error too
matters: a 409 or 404 means the page is out of date, so it should fix itself.

**What I rejected.** Polling only while awaiting review (hides the commit), and a manual refresh
button (the reviewer would be looking at stale facts while deciding).

**Trade-off and limits.** Up to 5 seconds of staleness, and a click just after expiry gets a 409
and a refresh instead of a prevented click. A `drifted` write goes back to previewing in the
backend, so it counts as not final.

### 2. The backend decides and words everything; the page only shows it

**What I did.** The decision bar appears only when the backend says `can_decide` is true and the
status is awaiting review. When two people decide at once, the loser's toast shows the backend's
409 message word for word ("Already approved by Demo User 2."). I do not build that sentence from
`decided_by`. A 409 on reject also toasts, because the refresh that follows removes the dialog.

**Why.** CLAUDE.md says the dashboard holds no business logic. Permission and "who won" are
database facts; if the page re-derived them it could disagree with the server. The backend
re-checks on every POST anyway, so hiding the button is a courtesy, not the security boundary.

**What I rejected.** Computing "can I decide" from role and owner in the browser, and composing
the 409 text from the action's decider (it would also miss the expiry wording).

**Trade-off and limits.** `can_decide` is computed when the page loads, so it can be up to 5
seconds old; the 409 path covers that. A fast double click is stopped by a flag set at once,
because the mutation's own pending state arrives one tick late.
