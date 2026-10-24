# Progress

## Current focus
Next: step 4 (Blastproof data model). Read backend plan §8 step 4 and §5 (the eleven tables,
roles and the audit trigger) first, and the known issue about `CREATE EXTENSION vector` as
`blastproof_owner`.

Stage 0 (setup) complete: plan audit, REQUIREMENTS.md and outside setup, decisions D-001 to D-062.
Step 1 (repo and skeleton) done and committed 2026-10-24, decisions D-063 to D-071.
Step 2 (CI) done 2026-10-24, decisions D-072 to D-075.
Step 3 (demo company database and eval scenarios) done 2026-10-24, decisions D-076 to D-081.

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
- Step 4 must check whether `CREATE EXTENSION vector` works as `blastproof_owner` (not a
  superuser). If not, the init script creates the extension instead.
- The MCP SDK is v2 and the plan describes v1 (D-063); the MCP spike must use the v2 API.
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
- CI is partial (OPS-01): `company-db` and the local seed were added in step 3; still to add:
  `alembic upgrade head` (step 4), and incident replays plus rule-only evals (Evals stage). Update
  `REQUIRED_RUN_ORDER` in `tests/test_ci_workflow.py` when steps are added.
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
- 404 and 422 responses still use FastAPI's default `{"detail": ...}` body, not CLAUDE.md's
  `{"error": {...}}` envelope. Only the crash 500 uses the envelope (D-068). Fixed in step 19.
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
  (`terminate-backends`) expects block.
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
- The GitHub CLI (`gh`) is not installed, so PRs are opened by hand in the browser. `test_checks_catch_a_bad_workflow[pytest-before-postgres]`
  now also trips on the missing seed step; `test_order_check_catches_a_seed_before_postgres`
  covers the new step's order.

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
- [ ] 4. Blastproof data model
- [ ] 5. Auth
- [ ] Spike: MCP mounting
- [ ] 6. MCP server
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
- [ ] Pull forward: /v1/auth/me, GET /v1/actions, GET /v1/actions/{id}
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
