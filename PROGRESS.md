# Progress

## Current focus
Next: Spike: Claude Code hook (Stage 1), then step 7 (remove direct access). Step 6 (MCP server)
is done on `feat/step-6`, PR into `develop`.

Stage 0 (setup) complete: plan audit, REQUIREMENTS.md and outside setup, decisions D-001 to D-062.
Step 1 (repo and skeleton) done and committed 2026-10-24, decisions D-063 to D-071.
Step 2 (CI) done 2026-10-24, decisions D-072 to D-075.
Step 3 (demo company database and eval scenarios) done 2026-10-24, decisions D-076 to D-081.
Step 4 (Blastproof data model) done 2026-10-24, merged into `develop` (PR #4), decisions D-082 to
D-087.
Step 5 (Auth) done 2026-10-25 on `feat/step-5`, PR #5 into `develop` (CI green), decisions D-088 to
D-095.
Spike: MCP mounting done 2026-10-25 on `spike/mcp-mounting`, decisions D-096 to D-101.
Step 6 (MCP server) done 2026-10-25 on `feat/step-6`, decisions D-102 to D-112.

### Step 6, done 2026-10-25
- `app/mcp/auth.py` (pure-ASGI `AgentAuthMiddleware` on `/mcp` exactly: 401 `invalid_agent_key`,
  503 `unavailable`; `current_agent` contextvar, `require_agent()`), `app/mcp/server.py`
  (`build_mcp_server` with the four tools, `transport_security`), `app/services/agent_calls.py`
  (record `received`, run, record `executed`/`failed` with `total_ms`), `agent_messages.py`,
  `reads.py` (temporary `check_read`, `run_read` fetching 501, `list_tables`, `describe_table`),
  `company_db.py` (one reader connection per call), `systems.py` (`ensure_main_system`),
  `app/db/actions.py` (`visible_actions`, `create_action`, `set_status`), `app/db/systems.py`,
  `GET /ready`, `app/schemas/{mcp,ready}.py`.
- `create_app` builds one MCP server, adds its `/mcp` route to FastAPI's router (stateless, SSE) and
  enters `session_manager.run()` in the lifespan. Logging: `mcp.*` records lose arguments and
  tracebacks, `mcp` at WARNING, `sqlglot` at ERROR (it echoed SQL), and `Settings` hides rejected
  values in errors (D-110).
- Config: `PUBLIC_BASE_URL`, `COMPANY_CONNECTION_PROFILE` (`neon` default), `COMPANY_DB_READER_URL`
  (now read by the app), `LOCAL_COMPANY_READER_URL`, `NEON_PROJECT_ID`, `NEON_PARENT_BRANCH_ID`.
  Production also needs the reader URL and refuses the `local` profile. Empty names appended to
  `backend/.env`; `.env.example` documents them.
- Tests: 459 in total (128 new): every template word for word, 52 read-check cases, MCP log
  filtering, config (host parsing, `channel_binding`, hidden inputs), mounting (one SDK route, 404
  and OpenAPI unchanged, two apps in a row), DNS rebinding (421/403), seven bad-key cases → 401,
  database down → 503, two agents interleaved, a key change between calls, nine overlapping calls,
  a tool with no agent, all four tools, truncation at 500/501, the 5 s timeout, query errors, writes
  refused with `company_main` unchanged, Postgres refusing a write `run_read` is handed directly,
  JSON-safe values, recording and audit events, owner-scoped `get_action_status` (including an
  admin's agent), a raising tool leaving no raw value in the logs, `/ready` up and down.
  Mutation checks: removing each of 20 guards turned a test red (log filter, `mcp` and `sqlglot`
  levels, `require_agent`, the 401, the exact path, `visible_actions`, admin widening off, refused
  nodes, side-effect functions, the read-only transaction, fetch 501, the statement timeout, the
  profile check, hidden inputs, `channel_binding`, both production checks, class-only error logs,
  the failed status).
- Done-when, automated: `test_done_when_pro_users_count` gets 459 from the local seed through
  `query_database`, with an `actions` row under the owner.
- Done-when, by hand against Neon dev: uvicorn on :8000, `/ready` 200 (both databases ok). Agent
  `step6-check` (owner `step5-check@example.com`) created by a script that wrote the key only into
  a `--mcp-config` file (deleted after). `claude -p "how many pro users do we have?"` (Claude Code
  2.1.310) called `list_tables`, `describe_table` twice and `query_database`, and answered 459. Four
  actions on local `blastproof`: owner's `user_id`, system `company` (profile `neon`),
  `session_id` null, audit events `[received, executed]`, about 3 s each. The server log (27 lines)
  held no key, no SQL and no row values.
- Fixed after the verify review (D-112): the `wait_seconds` maximum comes from
  `STATUS_MAX_WAIT_SECONDS` instead of a literal 50; `get_action_status` on a failed action repeats
  the message the agent got (`not_supported`, `table_not_found`); the `visible_actions` rule is
  recorded as covering reads made for a user or agent, not internal updates by ID. Both code fixes
  were mutation-checked (a test turns red without each).
- Requirements ticked: BE-17, BE-19, BE-20, BE-21, BE-22, ARCH-13, DW-B06. Done for step 6 but
  left open: SEC-33 (hook half, step 18), BE-18 (knowledge notes in
  step 11, long poll and `retry_of` in step 17), BE-23 (Neon check, step 10), BE-100 (the write
  templates, step 17), BE-16 and OBS-06 (write path), ARCH-10 (graph thread test, step 14),
  SEC-34 (`retry_of`, step 17), CONV-03 (step 19), SEC-14 (masking, steps 8-13).

### Spike: MCP mounting, done 2026-10-25
- Throwaway code and findings in `spikes/`: `mcp_mounting.py` (`probe` runs 10 questions in about
  90 s; `serve --mode stateful|stateless|json` for Claude Code), `mcp_mounting.md` (answers and
  real output), `mcp-spike.json` and `mcp-spike-wrong-key.json` (`--mcp-config` files, fake keys).
  No `backend/app` change; 331 backend tests still pass.
- Findings: add the SDK's `/mcp` route to FastAPI's router (both `app.mount` forms broke 404s or
  redirected `POST /mcp`); one MCP server per `create_app()` (`session_manager.run()` is single-use);
  Claude Code 2.1.310 speaks the session-less 2026-07-28 protocol; run stateless with SSE (15 s
  pings; JSON mode is silent for a whole long poll); contextvar, request state and headers all
  carried the right agent; a stateful session accepted another agent's key; DNS-rebinding
  protection rejects `testserver` and unlisted hosts with 421; the SDK logs raw exception text.
- Checked through Claude Code itself (`claude -p --mcp-config`, four modes): 25 s and 55 s tool
  calls returned; a wrong key showed `AUTH_HEADER_REJECTED, HTTP 401` with our message.
- Plan edits: backend plan step 6 rewritten, §6 `session_id` and the timeout note, the tech table
  and layout. `docs/design-choices.md` gained the spike's two choices.
- Requirements ticked: SPIKE-01.

### Step 5, done 2026-10-25
- `app/core/security.py` (agent keys, argon2 via pwdlib, HS256 tokens), `app/core/auth.py`
  (`get_current_user`, `require_admin`, `get_current_agent`, plain `authenticate_agent_key` for
  step 6), `app/core/errors.py` (`ApiError` and the error envelope), `app/db/session.py` (asyncpg
  engine, refuses any role but `blastproof_app`, D-091), `app/db/{users,agents,login_attempts}.py`,
  `app/services/auth.py` (login with rate limit, create user, create agent),
  `app/api/auth.py` (`POST /v1/auth/login`, `GET /v1/auth/me`), `app/schemas/auth.py`, `app/cli.py`.
- Config: `BLASTPROOF_DATABASE_URL` and `JWT_SECRET` (at least 32 bytes; both required when
  `APP_ENV=production`). Added to `.env.example`; the owner has set both in `backend/.env`.
- CLI: `uv run python -m app.cli create-user --email ... --display-name ... --role admin` (asks for
  the password; `--password-stdin` for scripts) and `create-agent --owner-email ... --name ...`.
- Tests: 331 in total (93 new): key, password and token unit tests (alg none, HS512, every
  missing claim, expiry boundary), login, `/me`, agent auth, admin-only route, rate limits
  (including 10 parallel failures counting exactly 5), logs never holding secrets or emails, the
  owner-role refusal, offline startup with a database URL, production settings, the CLI as a
  subprocess. Mutation checks: removing each of 10 guards (lock, token_version, inactive user,
  inactive agent, inactive owner, both limits, admin check, role check, inactive login) turned a
  test red.
- Done-when checked by the `test_done_when_*` tests: wrong or missing key or token, a user token used
  as a key, a deactivated agent → 401; inactive user and bumped `token_version` → 401; the sixth
  attempt from one IP in 15 min → 429 even with the right password; the 21st across six IPs in an
  hour → 429; a member on an admin-only route → 403. By hand on local `blastproof`: `create-user`
  and `create-agent` worked, login and `/v1/auth/me` worked through uvicorn, a bad token and a
  wrong password got the envelope, and the server log held no password or token. This left a
  user `step5-check@example.com` (admin) and agent `step5-check` in the local dev database.
- CI: PR #5's `backend` check passed on commit `af26ff7` (1 min 46 s), the first Linux run of the
  auth tests and the CLI subprocess tests.
- Fixed after the verify review: 422 responses use the envelope and never repeat the input (an
  over-long login password was echoed back; D-094); D-089 corrected (uvicorn trusts
  `X-Forwarded-For` from loopback by default); the tests no longer import `httpx2` directly.
- Requirements ticked: SEC-01 to SEC-07, DW-B05. Done for step 5 but left open: SEC-33 (`/mcp` in
  step 6, hook in step 18), BE-108 (verified by TEST-03 in step 19), SEC-09 (app side done, D-091;
  checkpointer in step 14), CONV-07 (429 added; rest in step 19), CONV-04 (422 done, D-094; 404 in
  step 19).

### Step 4, done 2026-10-24
- `app/db/`: `base.py` (naming convention, `enum_check`), `enums.py`, `models.py` (the eleven
  tables of §5 plus `actions.pinned_values`, `actions.failure_reason`, `created_at` on
  `preview_rows` and `before_images`, D-084), `urls.py` (`migrator_url`).
  `config.py` gains `blastproof_migrator_url` (SecretStr).
- Alembic: `backend/alembic.ini`, `migrations/env.py` (sync psycopg 3, URL from
  `BLASTPROOF_MIGRATOR_URL`, never logged; refuses any role but `blastproof_owner`; JSON logs from
  the command line, D-087), `versions/0001_initial_schema.py` (autogenerated, then reviewed: import
  fixed, extension, grants and trigger added).
- `docker/blastproof-init/02-extensions.sql` creates `vector` as superuser (pgvector is not trusted,
  D-082); run once on the existing dev volume by hand.
- `audit_events`: app has SELECT, INSERT only; statement-level trigger blocks UPDATE, DELETE and
  TRUNCATE for everyone (D-083).
- CI runs `uv run alembic upgrade head` before pytest (local dev owner URL, no secret).
- Tests: 238 in total (67 new): model shape and enum CHECKs, `alembic check` (models equal the
  migration), database CHECKs equal the models, downgrade/upgrade round trip, missing-extension
  failure leaves nothing, wrong migrator role (app, superuser) changes nothing, command-line logs
  are JSON with no SQL, scratch databases set up like the dev one, a failed connection never
  prints the password, the full app privilege matrix, app and owner refused
  UPDATE/DELETE/TRUNCATE on audit events, constraint violations, ORM round trip as the app.
  Mutation checks turned the matching tests red: trigger removed and audit grant widened (10),
  enum value without a migration (1), role check removed and scratch setup changed (3).
- Fixed after the verify review (D-087): migrations refuse any role but the owner; Alembic logs
  JSON; `env.py` no longer reads `backend/.env` when tests pass a URL; a drift test guards the
  scratch-database helper; a test compares the database's CHECK constraints with the models.
- Done-when checked: `alembic upgrade head` on local `blastproof` created all 11 tables (owner
  `blastproof_owner`, head `0001`); on that database `blastproof_app` gets "permission denied" and
  `blastproof_owner` gets "audit_events is append-only" for UPDATE and DELETE. CI not yet run (no PR).
- Requirements ticked: BE-13, BE-14, BE-15, SEC-08, DW-B04. Done for step 4 but left open: CONV-10
  (step 14's migration too), CONV-06 (API side in step 19), OPS-01 (evals still to add).

### Step 3, done 2026-10-24
- `company-db` in Compose (`postgres:18`, digest-pinned, loopback `:5433`, volume
  `company-data`); `docker/company-init/01-roles.sql` creates `blastproof_reader` and
  `blastproof_writer`.
- `demo_company/schema.sql`: 11 tables with identity PKs, FKs per BE-05, no triggers, the
  `blastproof` schema (`applied_actions`, `seed_info`), grants (writer gets TEMPORARY, D-076).
- `demo_company/seed.py` (`--target local|neon`), `rebase_dates.py`, `_data.py` (deterministic
  generator, D-078), `_targets.py` (URLs, `--confirm <host>` guard, password scrubbing, D-080),
  `schema_docs.md`. Run from the repo root: `uv run --project backend python demo_company/seed.py
  --target local` (about 16 s, 272,770 rows; byte-identical on every run).
- `backend/evals/schema.py` and `backend/evals/scenarios/{incidents,labeled}.yaml`: 12 incident
  replays covering all six MVP incidents, 23 labeled actions (5 allow, 8 review, 10 block); 16
  scenarios carry expected counts measured on the seed (D-079).
- CI lints `demo_company/` and runs the local seed before pytest; `REQUIRED_RUN_ORDER` extended.
- Tests: 171 in total (107 new): scenario schema, company roles and privileges, schema shape,
  exact counts, every scenario's measured impact, rebase, the Neon guard (pointed at a local
  copy), errors never printing passwords, schema docs vs schema.
- Done-when checked: the seed ran on local `company-db` and on Neon `blastproof-company-dev`
  (`ep-patient-butterfly-...`, 35 s, roles created with SQL and in no group); count by plan as
  `blastproof_reader` gave enterprise 153, free 49,388, pro 459 on both, and the inactive split
  48,201 / 9 / 3 on both; both scenario files validate.
- CI: the PR's `backend` check passed on commit `08fa5e9` (93 s, up from 35 s with the seed), the
  first Linux run of the seed and the subprocess tests.
- Requirements ticked: BE-02, BE-03, BE-04, BE-06, BE-07, BE-08, BE-09, BE-10, EVAL-01, EVAL-03,
  EVAL-04, DW-B03. Left open: BE-05 (verified by step 10's cascade counts; FK test already
  passes), BE-11 (the owner's `blastproof-company` load at deploy), BE-12 (verified by step 11's
  retrieval), EVAL-02 (50 entries at the Evals stage), OPS-01 (Alembic and evals still to add),
  OPS-07 (Deploy).
- Fixed after the verify review: error messages scrub only the passwords of the target in use (the
  local dev password `company` had blanked the word "company" in a Neon message); tests read the
  anchor date and the demo's headline counts from `incidents.yaml` (D-052).

### Step 2, done 2026-10-24
- `.github/workflows/ci.yml`: one `backend` job on every pull request and on pushes to `develop` and
  `main`. It runs `uv sync --locked`, `ruff check`, `ruff format --check`,
  `docker compose up -d --wait` (D-072) and `pytest` (unit + integration). There are no secrets,
  the token is read-only, actions are SHA-pinned and the runner is `ubuntu-24.04` (D-075).
  `APP_ENV=test`, `NEON_CLIENT=fake`.
- uv pinned once with `required-version = "==0.13.5"` in `backend/pyproject.toml` (D-073).
- `.github/dependabot.yml`: weekly grouped PRs for uv, GitHub Actions and the Compose image
  digest, into `develop` (D-074).
- `backend/tests/test_ci_workflow.py`: 13 unit tests guarding the workflow and Dependabot config
  (64 tests in total).
- Done-when checked: PR #1 (`ci/step-2` → `develop`) shows a green `backend` check (commit
  `595f1cb`, 35 s). The first Linux run cleared two step 1 risks: publishing on `[::1]` works on the
  runner, and the `no_network` guard passes on Linux.
- Requirements ticked: DW-B02, ARCH-21. Left open: SEC-21 (alerts not confirmed on yet), OPS-01
  (seed, Alembic and evals are added to CI by steps 3, 4 and the Evals stage), TEST-01 and TEST-04
  (ongoing; CI holds no secrets and sets `NEON_CLIENT=fake`, which nothing reads yet).

### Step 1, done 2026-10-24
- Backend skeleton in `backend/`: `pyproject.toml` with the whole MVP stack pinned (D-065),
  `uv.lock`, Python 3.13; `config.py`, structlog JSON logging, pure-ASGI request-ID middleware
  (D-066), `GET /health`; Ruff with the `demo_company` import ban (TID251, also matches
  submodules); pytest (51 tests, 6 of them `integration`).
- `docker-compose.yml` with `blastproof-db` (`pgvector/pgvector:pg18`, digest-pinned, loopback
  only) and `docker/blastproof-init/01-roles.sql` creating `blastproof_owner` and
  `blastproof_app` (D-067, D-070).
- Done-when checked by hand: `docker compose up -d`, `uv run uvicorn app.main:app --reload`,
  `/health` returned `{"status":"ok"}` with an `X-Request-ID` header and JSON logs.
- Fixes after the verify review (D-068 to D-071): a crash now returns a 500 with
  `X-Request-ID`; the test network guard also covers asyncio numeric-IP connections and
  psycopg; the database port listens on loopback only; the image is digest-pinned; the no-temp-
  tables rule is deliberate and tested; `LOG_LEVEL` is validated; importing `app.main` no longer
  reads `.env`; `--log-config log_config.json` makes every dev log line JSON; integration tests
  fail fast with a clear message when Postgres is down.
- Requirements ticked: BE-01, BE-125, BE-126, TEST-07, OBS-04, DW-B01. Done for step 1 but left
  open because later steps finish them: BE-02 and BE-03 (`company-db`, step 3), ARCH-21 (rule
  configured and tested; "in CI" is step 2), OBS-02 and CONV-09 (`action_id` and stage timings,
  steps 8-16), CONV-16 (`package-lock.json`, F1), SEC-21 (Dependabot, step 2).

### Outside setup (Prompt 0D), done 2026-10-24
Checked without printing secrets:
- GitHub repo `Mrinanka/blastproof` reachable; Vercel GitHub App limited to that repo.
- Gemini API key (`GOOGLE_API_KEY`) connected OK (D-056).
- Neon: three projects; the dev project's project-scoped key, project ID and branch ID checked
  (Postgres 18, `aws-us-east-2`, default branch `production`; D-057, D-058).
- LangSmith key connected OK; project `blastproof-dev` created through the API.
- Embeddings: fastembed `BAAI/bge-small-en-v1.5` (D-059).
- Python 3.13 (`C:\Python313`, D-060), uv 0.13.5, Docker Desktop 29.9.2 with Compose v5.6.1,
  `psql` 18.6 through the `postgres:18` image, VS Code with the Python and Ruff extensions.
- Node 24.22.1 and npm 11.19.1 (D-061, D-062), Claude Code 2.1.310, React Developer Tools.

Moved: Playwright browsers install from `frontend/` once `@playwright/test` is added (frontend plan
section 2), so they match its pinned version.

Needed later (skipped now): Fly.io account and `flyctl` (deploy, Stage 4); Vercel AI Gateway key and
payment card (right after the MVP, for the Jev judge); the `blastproof-company` Neon API key,
created at deploy time straight into Fly.io secrets (D-058).

## Known issues
- Step 6: company reads open one connection per call (D-109). Measured on Neon dev from the dev
  machine: connect 1.45 s, query 0.5 s, close 0.24 s, so a tool call takes about 3 s. A pool for
  the `neon` profile would save about 1.7 s per call.
- Local dev database now also holds agent `step6-check` (owner `step5-check@example.com`) and the
  `company` system row (profile `neon`) from the step 6 manual check.
- Step 6: 500 wide rows can pass Claude Code's 25k-token tool output limit (text over 50,000
  characters is saved to a file). The cap stays 500; measure on real queries.
- Step 6: `sql_display` and `pinned_sql` are null on read actions until step 8, so the dashboard
  will show no SQL for these rows; seeded and later actions have it.
- Step 6: an SDK argument-validation failure (e.g. `wait_seconds` 51) never reaches our code, so
  it is not recorded as an action.
- Step 6: the `systems` row named `company` is created by the first tool call (D-102); switching
  `COMPANY_CONNECTION_PROFILE` on a database that already has it makes every tool call fail
  closed until the row is fixed (step 20's bootstrap owns this).
- Deploy: Fly's proxy is said to drop connections idle for about 60 s (community forum, not docs).
  `/mcp` streams SSE pings every 15 s (D-097); measure a 50 s `get_action_status` long poll on the
  real machine (D-101).
- The MCP route is taken from the SDK app's route list (D-096). If the SDK app ever gains more
  routes or middleware (for example by turning on SDK auth), the step 6 route-count test fails.
- Account admin has no tools yet (D-095): no route or command bumps `token_version` (log out
  everywhere), fixes a mistyped email, deactivates a user or resets a password. Today these take SQL
  on the dev database. Step 19 adds user routes; decide then which of these it covers.
- Accounts are admin-created only and emails are never verified (D-095). Self sign-up, email-based
  password reset or Google sign-in would each need verification or SSO first.
- Step 18 must use `authenticate_agent_key` / `get_current_agent` to finish SEC-33 (step 6's
  `/mcp` middleware does).
- Local dev database still holds the step 5 manual-check user `step5-check@example.com` and agent
  `step5-check` (owner to decide whether to delete them).
- Login rate limit uses `request.client.host` (D-089). Locally uvicorn trusts `X-Forwarded-For`
  from loopback by default, so a local process can pick its IP. Behind the dashboard's login route
  (Vercel) and Fly's proxy, every user shares one IP. F3/Deploy: have the login route forward the browser IP and
  trust it only from the proxy (uvicorn `--forwarded-allow-ips` or a shared header secret).
- Anyone can lock a known email out of login for up to an hour (per-email limit by design); there
  is no admin unlock in the MVP.
- Accepted dev limitation (D-002): on the dev machine, the agent could reach the dev `.env` by a
  route the hook and `permissions.deny` miss. It only unlocks the dev company project. Full
  isolation is verified on the deployed demo.
- Open until spikes run: the latency target for previewed writes and whether asyncpg prepared
  statements work through Neon's pooled endpoint (Neon spike; the plan assumes the direct
  endpoint), the hook timeout behaviour (hook spike), and the Claude Code MCP tool timeout vs the
  50 s long poll (MCP spike).
- Landing page CSP allows `'unsafe-inline'` scripts (D-028); verify the real headers in F1.
- Confirm `blastproof-company` and `blastproof-app` are on Postgres 18 in `aws-us-east-2` like the
  dev project (D-057); a project's version cannot change later.
- Gemini tier (free or paid with a spend cap) not yet confirmed; the free tier's rate limits can
  skew eval runs.
- Resolved in step 4: `CREATE EXTENSION vector` does not work as `blastproof_owner` (pgvector is
  not trusted); `02-extensions.sql` creates it (D-082). A fresh clone gets it from the init script;
  an older volume needs the one-off `psql -f` shown in that file.
- Alembic's command line does not catch errors, so a wrong migrator role or a failed connection
  prints a Python traceback ending in the clear message. It holds no password (tested).
- Deploy: Neon `blastproof-app` needs `CREATE EXTENSION vector` (already in §15) plus the
  database- and schema-level setup that `01-roles.sql` does locally (CONNECT and USAGE for
  `blastproof_app`, owner owning `public`); the migration only grants table privileges.
- The owner can still drop or disable the audit trigger (deliberate DDL); only the app is fully
  locked out (D-083).
- Step 11: the HNSW index is `vector_cosine_ops`, so searches must use `<=>` to use it (D-085).
- Steps 11/19: `DELETE /v1/knowledge/{id}` "removes a source", but sources have no table or id
  (`knowledge_chunks.source` is text). Define what `{id}` is before building it.
- Steps 10/16: `preview_rows` has a composite primary key; a re-preview after drift must delete the
  action's old preview rows first (D-084).
- Adding a step to `REQUIRED_RUN_ORDER` breaks the exact-message check in
  `test_order_check_catches_a_seed_before_postgres`; step 4 checked its CI step in a separate test
  instead (D-086).
- The MCP SDK is v2 (D-063); the MCP spike and step 6 use the v2 API (D-096 to D-111).
- Without `--log-config log_config.json`, uvicorn's reloader prints three plain-text lines
  before the app loads (the plan's Done-when command still works; CLAUDE.md uses the flag).
- `mcp` v2 pulls in `httpx2` alongside the plan's `httpx`; harmless. The test client logs each
  request through `httpx2` at INFO.
- The test network guard (`no_network`) covers Python sockets, asyncio and psycopg. A C
  extension that opens its own sockets would not be caught; none runs at startup today. Re-check
  when startup gains work (fastembed in step 11, Neon in step 10).
- The Postgres port is published on `127.0.0.1` and `[::1]`. Publishing only IPv4 made every
  `localhost` connection on Windows wait about 2 s for the IPv6 attempt to fail.
- Partial requirements from step 1, finished by later steps: OBS-02 and CONV-09 (`action_id`
  binding and stage timings, steps 8-16), CONV-16 (`package-lock.json`, F1). BE-02 and BE-03
  were finished in step 3.
- SEC-21 needs the owner to turn on Dependabot alerts and security updates (Settings → Advanced
  Security). Tick it once that is confirmed.
- PR #1 merged into `develop` on 2026-10-24, so Dependabot now reads `dependabot.yml`. Its config
  is not yet checked: look for errors under Insights → Dependency graph → Dependabot.
- CI is partial (OPS-01): `company-db` and the local seed were added in step 3, `alembic upgrade
  head` in step 4; still to add: incident replays plus rule-only evals (Evals stage).
- CI cannot use `ubuntu-latest`. When 24.04 nears end of life, move the pin and its test together.
  Docker Compose on the runner is 2.38 while dev runs v5; keep `docker-compose.yml` to features
  both support.
- CI's Python is the latest 3.13 patch, which may differ from the dev machine's patch.
- `cancel-in-progress` means that when two pushes land on `develop` quickly, the older commit's run
  is cancelled and that commit gets no check.
- Branch protection (making `backend` a required check on `develop`) is not set; owner's choice.
- uv upgrades now start in `backend/pyproject.toml` (`required-version`); uv refuses to run at any
  other version.
- If anyone runs Compose on Linux without IPv6, check that publishing on `[::1]` still works (it
  does on GitHub's `ubuntu-24.04` runner).
- 404 responses still use FastAPI's default `{"detail": ...}` body, not CLAUDE.md's
  `{"error": {...}}` envelope (fixed in step 19). The crash 500 (D-068), auth errors (D-088) and
  422 (D-094, brought forward in step 5) already use it.
- The crash path logs the full exception text. Nothing handles sensitive data yet, but once
  database code exists, a Postgres error can contain raw values (`invalid input syntax ...:
  "<value>"`). The masking work must cover exception text in logs too.
- `log_config.json` sets the reloader process's level to INFO, so that process ignores
  `LOG_LEVEL` (the app process respects it). Dev only.
- Test helpers (`NetworkGuard`, database URLs) live in `tests/conftest.py` and are imported
  from it (hence `tests/__init__.py`). Move them to `tests/support.py` when the suite grows.
- `FastAPI(version="0.1.0")` duplicates the version in `pyproject.toml`.
- Step 8: the frozen `Clock` must pin to exactly 2026-10-01T00:00:00Z. Only `last_login_at` has
  a margin around its cutoff; other expected counts (e.g. tickets closed a year ago) are exact
  only at that instant (D-079).
- Steps 8-9: no rule in backend plan §9 blocks a side-effect function such as
  `pg_terminate_backend`; as written it would go to review and be shadow-run. `labeled.yaml`
  (`terminate-backends`) expects block. Step 6's temporary `check_read` refuses them (D-103); keep
  that list when the classifier replaces it.
- Step 9: `tests/test_seed_counts.py` repeats the `users.paying` derived group in
  `DERIVED_GROUPS`; switch it to read `policies/rules.yaml`.
- Step 10: `CREATE DATABASE ... TEMPLATE` does not copy database-level rights, so
  `FakeNeonClient` must grant CONNECT (reader) and CONNECT, TEMPORARY (writer) on each
  `shadow_*` database, as `seed.py` does for `company_main`.
- Step 6 onward: asyncpg sends unknown URL query parameters to Postgres as settings, so Neon's
  `channel_binding=require` must stay out of the company URLs (keep `sslmode=require`).
- Hook spike: MultiEdit is no longer listed in Claude Code's tools reference; confirm the matcher
  list in BE-103/BE-104.
- Two labeled actions disagree with rules alone on purpose (`close-one-ticket`,
  `delete-inactive-enterprise-customer`); the evals will count them as a false block and a miss
  until the judge handles them.
- Running `seed.py --target local` drops and rebuilds `company_main` with `WITH (FORCE)`,
  disconnecting anything using it. Rebasing local `company_main` breaks the anchor test; re-seed
  instead. The scripts cannot tell Neon projects apart: the `--confirm <host>` guard is the only
  check against loading the wrong one.
- CI now takes about 93 s (35 s before step 3): the seed plus the scratch-database tests.
- The GitHub CLI (`gh` 2.103.2) was installed with winget on 2026-10-25 and is logged in as
  Mrinanka; PRs are now opened with `gh pr create` (step 4 is PR #4). Shells started before the
  install need a restart, or the full path `C:\Program Files\GitHub CLI\gh.exe`.
- `test_checks_catch_a_bad_workflow[pytest-before-postgres]` now also trips on the missing seed
  step; `test_order_check_catches_a_seed_before_postgres` covers the seed step's order.

## Stage 0: setup
- [x] Plan audit done (Prompt 0A): `docs/plan-audit.md`
- [x] Plans fixed with the audit decisions: `docs/audit-response.md`, `docs/decisions.md`
- [x] docs/REQUIREMENTS.md drafted (Prompt 0B)
- [x] docs/REQUIREMENTS.md created and reviewed, IMP items settled (Prompt 0C)
- [x] Outside setup done (backend plan section 2, frontend plan section 2) (Prompt 0D)
- [x] Repo files committed (CLAUDE.md, docs/, PROGRESS.md) (Prompt 0E)

## Stage 1: foundation
- [x] 1. Repo and skeleton
- [x] 2. CI
- [x] 3. Demo company database and eval scenarios
- [x] 4. Blastproof data model
- [x] 5. Auth
- [x] Spike: MCP mounting
- [x] 6. MCP server
- [ ] Spike: Claude Code hook
- [ ] 7. Remove direct access
- [ ] GATE AUDIT: Claude Code reads the demo DB only through Blastproof

## Stage 2: judging parts
- [ ] 8. Classifier
- [ ] 9. Policy rules
- [ ] Spike: Neon branching
- [ ] 10. Neon client and shadow executor
- [ ] 11. Knowledge and RAG, swappable vector store
- [ ] 12. Impact engine
- [ ] 13. Risk judge, pluggable
- [ ] GATE AUDIT: exact diffs and correct retrieval

## Stage 3: the flow + dashboard start
- [ ] Pull forward: GET /v1/actions, GET /v1/actions/{id} (`/v1/auth/me` done in step 5, D-092)
- [ ] Spike: LangGraph interrupt and resume
- [ ] 14. LangGraph workflow
- [ ] 15. Workflow runner
- [ ] Pull forward: POST /v1/actions/{id}/approve and /reject (D-053)
- [ ] Pull forward: seed-demo-actions
- [ ] 16. Commit
- [ ] 17. Wire up query_database
- [ ] F1. Scaffold
- [ ] F2. Typed API client
- [ ] F3. Proxy and login
- [ ] F4. Layout and navigation
- [ ] F5. Shared components
- [ ] GATE AUDIT: Gate 1, Replit case held for review and approvable

## Stage 4: finish backend + core dashboard
- [ ] 18. Claude Code hook
- [ ] 19. Dashboard API (remaining routes)
- [ ] 20. Bootstrap, dev commands and README
- [ ] Evals in CI, security pass, observability (backend plan sections 12-14)
- [ ] Backend deployed to Fly.io (backend plan section 15)
- [ ] F6. Review queue
- [ ] F7. Change request page
- [ ] F8. History
- [ ] F9. Overview
- [ ] GATE AUDIT: Gate 2, evals green and backend deployed

## Stage 5: complete and ship
- [ ] F10. Knowledge
- [ ] F11. Agents, users and rules
- [ ] F12. Polish
- [ ] F13. Landing page
- [ ] Dashboard deployed to Vercel
- [ ] FINAL AUDIT against both MVP done checklists
- [ ] Demo video recorded
