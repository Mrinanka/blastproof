# Requirements

Every concrete requirement from `docs/spec.md`, `docs/backend-plan.md`, `docs/frontend-plan.md`,
the architecture rules and conventions in `CLAUDE.md`, and `docs/decisions.md`. Built from the
plans as of 2026-10-24 (Prompt 0B), spot-checked and IMP items settled in Prompt 0C (D-030 to
D-055). Tick a box only when its verification has passed.

**How to read a row:** ID · requirement · source · build step · how it is verified.

- **Sources:** `SPEC §n`, `BE §n` (backend plan), `FE §n` (frontend plan), `CL` (CLAUDE.md),
  `D-nnn` (decisions.md), `WF` (WORKFLOW.md).
- **Steps:** `B1`-`B20` are backend steps, `F1`-`F13` frontend steps. `Evals`, `Security`,
  `Deploy` are the Stage 4 work from backend plan sections 12-15. `All` means every step.
- **Verification:** plain text is stated in the plans. *Italics* means the plans name no check,
  and the entry is the test type CLAUDE.md's testing rules call for; confirm it when planning the step.
- Phase-2 items ("Later", "after the MVP", deferred lists) are left out on purpose; see the end.

---

## ARCH: architecture rules (CLAUDE.md)

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [ ] | ARCH-01 | Any error, timeout or unparseable SQL blocks the write (fail closed). | CL; BE §7, §12; SPEC §10, §13; D-008 | B8, B10, B14, B16 | DW-B08, DW-B14; *integration tests for each failure path* |
| [x] | ARCH-02 | The one exception: if only the AI judge fails, the write goes to human review; a failed check never lets a write through. | CL; BE §7, B13; D-008 | B13, B14 | DW-B13, DW-B14 |
| [ ] | ARCH-03 | `pinned_sql` is what is previewed, approved and committed. | CL; BE §5, §7, B8; D-005 | B8, B10, B16 | DW-B08, DW-B16 |
| [ ] | ARCH-04 | The commit must reproduce the approved diff or roll back. | CL; BE B16; D-005 | B16 | DW-B16 |
| [ ] | ARCH-05 | The security boundary is credential isolation; hook rules are guardrails only. | CL; SPEC §13; BE §12; D-001 | B7, B18, Deploy | DW-B07, MVP-B01 |
| [x] | ARCH-06 | Numbers come from the database, never from a model ("code counts, the LLM explains"). | CL; BE B12 | B10, B12 | DW-B10, DW-B12 |
| [x] | ARCH-07 | Every number in an LLM summary must match the computed facts. | CL; BE B12 | B12 | DW-B12 (guardrail tests) |
| [x] | ARCH-08 | Rules run before the judge. | CL; SPEC §13; BE §7 | B9, B13, B14 | DW-B14 |
| [x] | ARCH-09 | The judge can only make a decision stricter, never looser. | CL; SPEC §13; BE B13; D-008 | B13 | DW-B13 |
| [ ] | ARCH-10 | Reads take a fast path outside the LangGraph workflow; only writes enter the graph. | CL; BE §7, B14, B17 | B6, B14, B17 | *integration test: a read creates no graph thread* |
| [x] | ARCH-11 | One SQL statement per write. | CL; BE B8, B9 ("One statement only") | B8, B9 | DW-B08 (multi-statement case) |
| [x] | ARCH-12 | BEGIN, COMMIT, ROLLBACK (and SAVEPOINT, SET TRANSACTION) from agents are rejected. | CL; BE B8, B9 | B8, B9 | DW-B08 (transaction-control case) |
| [x] | ARCH-13 | Every action stores the agent owner's `user_id`. | CL; BE §5 Ownership, B6 | B6 | DW-B06; TEST-03 |
| [x] | ARCH-14 | All action queries go through `visible_actions(user)`, which filters by `user_id` unless the user is an admin. | CL; BE §12, B19 | B19 (pulled forward with `GET /v1/actions`) | TEST-03 |
| [x] | ARCH-15 | Another user's action returns 404, never 403. | CL; BE §6, §12, B19 | B19 (pulled forward with `GET /v1/actions/{id}`) | TEST-03; FE e2e step 6 (TEST-09) |
| [ ] | ARCH-16 | Company database credentials live only in the backend's environment: never in agent workspaces, logs, traces, prompts or API responses. | CL; SPEC §9, §13; BE §12 | B7, B12, B13, Deploy | DW-B07, MVP-B01; *log/trace review at Security pass* |
| [ ] | ARCH-17 | Sensitive columns are masked before data reaches logs, prompts or the UI. | CL; SPEC §13; BE §12 | B8, B9, B10, B12 | *unit tests on masking; check in Security pass* |
| [x] | ARCH-18 | `Judge`, `VectorStore` and `Embedder` are interfaces. | CL; BE B11, B13 | B11, B13 | DW-B13 (switch provider); TEST-12 |
| [x] | ARCH-19 | LLMs are created from `provider:model` config strings; no provider is hard-coded. | CL; BE §3 Models, §11 | B12, B13 | DW-B13 |
| [x] | ARCH-20 | Knowledge text lives in Postgres (`knowledge_chunks`); vector stores hold only vectors. | CL; BE §5, B11 | B11 | TEST-12 |
| [x] | ARCH-21 | The backend never imports from `demo_company/`. | CL; BE B1; D-047 | B1 (config), All | Ruff TID251 (banned imports) in CI |
| [ ] | ARCH-22 | The dashboard holds no business logic; the backend enforces every permission. | CL; FE §1, §11 | All F steps, B19 | TEST-03; MVP-F06 |

## CONV: conventions

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [ ] | CONV-01 | Backend uses Python 3.13, type hints everywhere and async I/O. | CL | All B | *ruff in CI; code review* |
| [ ] | CONV-02 | Routes are thin; logic lives in `app/services/`, database code in `app/db/`. | CL; BE §4 | All B | *gate audit* |
| [ ] | CONV-03 | Every route declares a Pydantic response model. | CL; BE §6; FE §5 | B6, B19 | OPS-05 (contract check) |
| [ ] | CONV-04 | Every error, including validation errors, uses `{"error": {"code", "message"}}` (a custom handler replaces FastAPI's 422 shape). | CL; BE §6; FE §5 | B19 | *integration test per error type* |
| [ ] | CONV-05 | List endpoints return `{"items", "total", "limit", "offset"}`. | CL; BE §6; FE §5 | B19 | *integration test* |
| [ ] | CONV-06 | IDs are UUID strings; timestamps are ISO 8601 UTC. | CL; BE §6; FE §5 | B4, B19 | *integration test* |
| [ ] | CONV-07 | Status codes: 401 not signed in, 403 wrong role, 404 not found, 409 already decided, 422 invalid input, 429 too many failed logins (D-089). | FE §5; D-089 | B19 | TEST-03 |
| [x] | CONV-08 | A 409 on approve or reject names who already decided, by `display_name`. | BE §6, B19; FE §5 | B15, B19 | DW-B15; DW-F07 |
| [ ] | CONV-09 | Logging uses structlog with `request_id` and `action_id` bound. | CL; BE §13 | B1, all B | OBS-02 |
| [x] | CONV-10 | Autogenerated Alembic migrations are read and fixed before being applied. | CL | B4, B14 | *review of migration diff* |
| [ ] | CONV-11 | Frontend uses TypeScript strict mode. | CL | F1 | `npm run typecheck` in CI |
| [ ] | CONV-12 | Server data lives only in TanStack Query; filters live in the URL; everything else is local component state. | CL; FE §3 | F2-F11 | DW-F08; *code review* |
| [ ] | CONV-13 | `src/lib/api/schema.d.ts` is generated by `npm run gen:api`, committed, and never edited by hand. | CL; FE §5, F2 | F2 | OPS-05 (contract check) |
| [ ] | CONV-14 | The browser calls only `/api/blastproof/*`; the proxy (plus the login route via the same helper) is the only code that knows `BLASTPROOF_API_URL`. | CL; FE §4, §10 | F2, F3 | *grep for `BLASTPROOF_API_URL`; proxy unit tests* |
| [ ] | CONV-15 | shadcn/ui components are used before writing custom ones. | CL; FE §3 | F1-F13 | *code review* |
| [ ] | CONV-16 | Library versions are pinned exactly (`uv.lock`, `package-lock.json`); no new dependency without asking. | CL; BE §12 | All | *review of lockfile diffs* |
| [ ] | CONV-17 | Dates display in the user's local time (converted from UTC); number and date formatting live in `lib/format.ts`. | FE §5, F5 | F5 | DW-F05 |

## TEST: testing rules

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [ ] | TEST-01 | Unit tests use no network; LLM calls and the Neon client are faked. | CL; BE §14 | All B | *CI runs with no network secrets* |
| [ ] | TEST-02 | Integration tests run against real Postgres from Docker Compose (`blastproof-db` and `company-db`). | CL; BE §14 | B2+ | CI |
| [ ] | TEST-03 | An authorization matrix test calls every endpoint as owner, as another user and as admin, and checks the expected result; the hook endpoint (agent key, not a user login) is called with a valid, a wrong and a deactivated key. | CL; BE §14, B19; D-031 | B19 | DW-B19 |
| [x] | TEST-04 | Integration tests and CI use the local `company-db` and `FakeNeonClient` (`NEON_CLIENT=fake`) and never call Neon. | CL; BE §11, §15; D-003 | B2, B10 | CI config review |
| [ ] | TEST-05 | Test setup recreates `company_main` from the never-connected `company_template`. | BE B10, §14; D-019 | B10 | DW-B10 (two previews in a row) |
| [x] | TEST-06 | Tests and evals freeze the classifier's `Clock` at `ANCHOR_DATE`. | BE B3, B8, §14; D-017 | B8 | DW-B08 (frozen-clock case) |
| [x] | TEST-07 | App startup does not call Neon or download models when `APP_ENV=test`. | BE B1 | B1 | *test that startup succeeds offline* |
| [ ] | TEST-08 | Agent message templates are tested word for word. | BE §6 | B17 | *unit tests per template* |
| [ ] | TEST-09 | A Playwright e2e test runs against the real local backend with `seed-demo-actions` data: log in as demo user 1, open the seeded pending action, see headline and numbers, reject with a reason, find it in History as rejected with your name, then as demo user 2 confirm it is absent and its URL shows "not found". | FE §12 | F7-F9 (after B20) | OPS-06 (nightly/manual workflow) |
| [ ] | TEST-10 | Frontend unit tests (Vitest + RTL) cover formatting helpers, status and risk badges, reject-dialog validation, and proxy header handling (cookie in, Authorization out, incoming cookies dropped, bad Origin rejected). | FE §12 | F3, F5, F7 | `npm test` in CI |
| [ ] | TEST-11 | No snapshot tests and no mocking of the whole backend in the frontend. | FE §12 | F steps | *code review* |
| [x] | TEST-12 | One contract test suite runs against every `VectorStore` implementation. | BE B11 | B11 | pytest |
| [ ] | TEST-13 | A manual weekly end-to-end run of the demo in Claude Code against real Neon, reviewed in the dashboard. | BE §14; FE §12 | from B17 / F7 | Manual |
| [ ] | TEST-14 | The Claude Code hook has integration tests (hook format changes across versions). | SPEC §19 | B18 | DW-B18 |

## BE: backend functional requirements

### Foundation (B1-B7)

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [x] | BE-01 | FastAPI app (`main.py`) with a `/health` route, `config.py` using pydantic-settings, structlog JSON logging, Ruff and pytest. | BE B1 | B1 | DW-B01 |
| [x] | BE-02 | `docker-compose.yml` runs `blastproof-db` (`pgvector/pgvector:pg18`, port 5432) and `company-db` (`postgres:18`, matching Neon's version, port 5433). | BE B1, §15 | B1, B3 | DW-B01, DW-B03 |
| [x] | BE-03 | Init scripts in `docker/` create `blastproof_owner` and `blastproof_app` on `blastproof-db`, and only the reader and writer roles on `company-db`. | BE B1, §15 | B1, B3 | *integration test connects as each role* |
| [x] | BE-04 | `demo_company/schema.sql` has about 11 tables (`users` with plan free/pro/enterprise and `last_login_at`, `organizations`, `subscriptions`, `payments`, `invoices`, `products`, `orders`, `order_items`, `support_tickets`, `feature_flags`, `customer_notes`), each with a primary key, and no triggers. | BE B3 | B3 | DW-B03 |
| [x] | BE-05 | Foreign keys: `orders`, `subscriptions`, `payments`, `support_tickets`, `customer_notes` → `users` and `order_items` → `orders` are `ON DELETE CASCADE`; `invoices.user_id` and `users.organization_id` are `ON DELETE SET NULL`. | BE B3; D-010 | B3 | DW-B10 (cascade counts) |
| [x] | BE-06 | The company database has a `blastproof` schema with `applied_actions (action_id uuid pk, committed_at)` and `seed_info (anchor_date)`. | BE §5, B3 | B3 | DW-B03 |
| [x] | BE-07 | `seed.py` uses Faker with a fixed seed, about 50,000 users including inactive paying customers, and every timestamp relative to `ANCHOR_DATE` = 2026-10-01, which it writes to `blastproof.seed_info`. | BE B3; D-017, D-052 | B3 | DW-B03 |
| [x] | BE-08 | `rebase-dates` shifts every timestamp column by (today − anchor), records the new anchor in `blastproof.seed_info`, prints the target project and asks for confirmation; it is owner-run only and never run by Claude Code. | BE B3; D-017, D-029 | B3 | *unit test of the shift; manual confirmation prompt check* |
| [x] | BE-09 | Company roles: `blastproof_reader` (SELECT only) and `blastproof_writer` (SELECT, INSERT, UPDATE, DELETE on company tables, plus INSERT and SELECT on `blastproof.applied_actions`; no DDL, no role changes). | BE B3, §12 | B3 | *integration test of each role's privileges* |
| [x] | BE-10 | `seed.py --target local` (run from host or CI, not an init script) builds `company_template`, closes its connections, locks it (`ALLOW_CONNECTIONS false`) and clones `company_main` from it. | BE B3; D-019 | B3 | DW-B03 |
| [ ] | BE-11 | Step 3 loads the seed into local `company-db` and Neon `blastproof-company-dev` (Claude asks before the dev load); the owner loads Neon `blastproof-company` at deploy time, after the command prints the target and asks for confirmation; Claude Code never runs that load. | BE B3, §15; D-003, D-054 | B3, Deploy | DW-B03; OPS-11 |
| [x] | BE-12 | `schema_docs.md` has one `## table: <name>` heading per table and a `## business rules` section (e.g. paying = pro or enterprise; enterprise must never be deleted). | BE B3 | B3 | DW-B11 (rules retrieved) |
| [x] | BE-13 | SQLAlchemy models for the eleven tables in BE §5 (`users`, `login_attempts`, `agents`, `systems`, `actions`, `audit_events`, `preview_rows`, `before_images`, `hook_events`, `knowledge_chunks`, `chunk_embeddings`) with the listed columns. | BE §5, B4 | B4 | DW-B04 |
| [x] | BE-14 | Migrations run as `blastproof_owner`; the first one runs `CREATE EXTENSION vector`. | BE B4 | B4 | DW-B04 |
| [x] | BE-15 | `chunk_embeddings.embedding` has an HNSW index. | BE §5, B11 | B4 or B11 | *migration test* |
| [ ] | BE-16 | Action statuses follow the canonical state machine in BE §5; "pending" is never stored; `expired` actions have `decided_by` null. | BE §5 | B6, B14-B16 | *unit test of allowed transitions* |
| [x] | BE-17 | A FastMCP server (official MCP SDK, streamable HTTP) is served by FastAPI at the public path `/mcp` (its route is added to the app, D-096). | BE §3, B6 | B6 | DW-B06 |
| [ ] | BE-18 | MCP tools `list_tables` (names with one-line descriptions), `describe_table` (columns and types; plus knowledge notes from B11), `query_database` (`sql`, `intent`, optional `retry_of`) and `get_action_status` (`action_id`, optional `wait_seconds` 0-50, default 30). | BE §6, B6 | B6, B11, B17 | DW-B06, DW-B17 |
| [x] | BE-19 | Middleware checks the agent key on `/mcp` and puts the agent and owner into a `contextvar` (or the SDK's own mechanism) for tools to read. | BE B6 | B6 | *integration test: bad key 401* |
| [x] | BE-20 | `query_database` reads use `blastproof_reader` with a 5-second statement timeout and a 500-row cap; a read with more than 500 rows (fetch 501 to tell) returns the first 500 plus the `truncated` notice, with no total count. | BE B6, §6, §12; D-039 | B6 | *integration tests for timeout, cap and notice* |
| [x] | BE-21 | Every `query_database`, `list_tables` and `describe_table` call is recorded in `actions` and `audit_events` with the owner's `user_id` (reads end as `executed`); `get_action_status` creates no action and is only logged; rate-limited writes (SEC-20) are the other exception. | BE B6; D-038 | B6 | DW-B06; *integration test* |
| [x] | BE-22 | `session_id` is the MCP session ID when the transport provides one, else null. | BE §6 | B6 | *integration test* |
| [x] | BE-23 | `/ready` checks that Blastproof's DB, the company DB and the Neon API are reachable; `/health` is liveness only. | BE §6, §13, B6 | B1, B6 | *integration test with a dependency down* |
| [x] | BE-24 | A demo workspace outside the repo holds no database URL (not in `.env`, not in shell variables) and has `.claude/settings.json` `permissions.deny` rules for the repo, any `.env` and credential files. | BE B7; D-002 | B7 | DW-B07 |

### Safety pipeline (B8-B13)

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [x] | BE-25 | The classifier does not parse SQL over 20 KB (it returns `unknown`) and parses the rest with sqlglot (Postgres dialect) in a worker thread capped at 2 seconds. | BE B8, §12; D-040 | B8 | DW-B08 |
| [x] | BE-26 | The classifier returns `kind` (read, write, ddl, unknown), statement types, tables, schemas, `has_where`, `multi_statement`, `transaction_control`, `non_deterministic`, `volatile_in_where`, `unordered_limit` and `fast_lane_eligible`. | BE B8 | B8 | DW-B08 |
| [x] | BE-27 | The classifier walks the whole tree, so a write inside a CTE counts as a write; side-effect functions like `pg_terminate_backend` count as writes. | BE B8 | B8 | DW-B08 (CTE case) |
| [x] | BE-28 | A parse error, parse timeout or SQL over 20 KB returns `unknown`, which the "Unparseable SQL" rule blocks in pass 1; the agent gets the `blocked` template with that rule's reason. | BE B8, B9; D-040 | B8, B9 | DW-B08; DW-B09 |
| [x] | BE-29 | `has_where` is false when the WHERE simplifies to a constant true (`WHERE true`, `WHERE 1=1`). | BE B9 note; D-009 | B8 | DW-B08 (`WHERE true` case) |
| [x] | BE-30 | `unordered_limit` is set for a LIMIT without ORDER BY anywhere in a write. | BE B8 | B8 | DW-B08 |
| [x] | BE-31 | Pinning replaces `now()`, `current_timestamp`, `current_date`, `localtimestamp`, `statement_timestamp()`, `clock_timestamp()`, `transaction_timestamp()`, `current_time` and `localtime` with one UTC literal from intercept time (cast to the original type, precision kept), and returns `pinned_values`. | BE B8; D-005, D-122 | B8 | DW-B08 (frozen clock) |
| [x] | BE-32 | The time used for pinning comes from an injected `Clock` (real in the app, frozen at `ANCHOR_DATE` in tests and evals). | BE B8; D-017 | B8 | DW-B08 |
| [x] | BE-33 | Volatile functions (`random()`, `nextval()`, `gen_random_uuid()` and others) in a WHERE or a subquery feeding one set `volatile_in_where`; inside INSERT values they are allowed. | BE B8 | B8 | DW-B08 |
| [x] | BE-34 | `fast_lane_eligible` is true only for a single `INSERT … VALUES` of at most 10 rows, with no SELECT, ON CONFLICT, CTE or RETURNING at all, and only literals, NULL, DEFAULT, casts of literals and pinned timestamps in VALUES. | BE B8; D-024, D-125 | B8 | DW-B08 (function in VALUES case) |
| [x] | BE-35 | `sql_display` is `pinned_sql` with PII-looking string literals (email, phone) masked; it is the only SQL that reaches the API, UI, logs and prompts. | BE §5, B8 | B8 | *unit test* |
| [x] | BE-36 | Policy rules live in `policies/rules.yaml`; each has a name, conditions (all must hold), an outcome (allow, review, block) and a reason; they load at startup and changes need a restart. | BE B9 | B9 | DW-B09 |
| [x] | BE-37 | Condition keys are a closed list (SQL-only and diff keys in BE B9); an unknown key fails at startup. | BE B9; D-009 | B9 | DW-B09 |
| [x] | BE-38 | Pass 1 runs before the shadow run with SQL-only keys; a rule using any diff key is skipped, not treated as false. | BE B9; D-009 | B9 | DW-B09 |
| [x] | BE-39 | A pass-1 `block` ends the flow with no shadow run; a pass-1 `allow` on a fast-lane write sends it down the fast lane; pass 2 runs after the shadow run with every key. | BE B9 | B9, B14 | DW-B09, DW-B14 |
| [x] | BE-40 | The strictest matching outcome wins (block > review > allow); auto-approve needs a positive allow match and no stricter match; no match means review. | BE B9; D-009 | B9 | DW-B09 |
| [x] | BE-41 | The fast lane's only table list is an `allow` rule with `fast_lane: true` and `tables_any`; there is no separate list. | BE B9; D-024 | B9 | DW-B09 |
| [x] | BE-42 | The rules file defines per-table `important_columns`, `derived_groups` (counted over that table's affected rows only; an unaffected table counts as 0) and `sensitive_columns`; startup validates the file, and every preview checks that the columns they name exist (`check_columns`), failing the write closed if one is missing. | BE B9; D-023, D-132 | B9 | DW-B09 |
| [x] | BE-43 | Sensitive columns are the configured ones plus a name heuristic (email, phone, ssn, address, token, password). | BE B9, §12 | B9 | *unit test* |
| [x] | BE-44 | `rules.yaml` ships with the rules in BE §9: No DDL, Unparseable SQL, No DROP or TRUNCATE, One statement only, No transaction control, Hands off Blastproof's schema, Volatile values in WHERE, Unordered LIMIT, Delete or update without WHERE, Every row of a table, Most of a table, Large change, Touches paying customers, Fast-lane note, Small insert. | BE B9 | B9 | DW-B09 |
| [x] | BE-45 | The demo "clean up inactive users" delete (about 96% of users) is held for review, not blocked. | BE B9 note; D-009 | B9, B10 | MVP-B03; EVAL scenarios |
| [x] | BE-46 | A `NeonClient` interface has `NeonApiClient` and `FakeNeonClient`, chosen per system by `systems.connection_profile` (`neon` or `local`). | BE §5, B10; D-027 | B10 | DW-B10 |
| [x] | BE-47 | `NeonApiClient` creates `shadow-<action_id>` from `NEON_PARENT_BRANCH_ID` with a read-write endpoint, waits until ready, returns the direct (non-pooled) connection string, and deletes the branch afterward; commits on main also use the direct endpoint. | BE B10 | B10 | DW-B10 (once by hand) |
| [x] | BE-48 | `FakeNeonClient` clones with `CREATE DATABASE shadow_<id> TEMPLATE company_main`, uses no connection pool for the `local` profile, and terminates stray sessions on `company_main` via `FAKE_NEON_ADMIN_URL` before cloning. | BE B10; D-019 | B10 | DW-B10 |
| [ ] | BE-49 | Every `local`-profile operation on `company_main` (clone, read, commit) takes one shared Postgres advisory lock. | BE B10; D-029 | B10 | *integration test with concurrent operations* |
| [ ] | BE-50 | One `ROW_HASH_SQL` (`md5(row_to_json(t)::text)`) is defined once and computed by Postgres in both shadow run and commit; every company connection sets `TimeZone=UTC` and `extra_float_digits=1`. | BE B10; D-018 | B10, B16 | DW-B10 (hash test across time zones) |
| [x] | BE-51 | The shadow run snapshots per-table PK hashes into temp tables on the branch, runs `pinned_sql` in a transaction via asyncpg's prepared-statement path (`fetch`, never argument-less `execute`), returns only differing rows via FULL OUTER JOIN, then rolls back. | BE B10; D-018 | B10 | DW-B10 |
| [x] | BE-52 | Missing-after is deleted, missing-before is inserted, a changed hash is updated; tables other than the target that changed are cascades (including SET NULL). | BE B10 | B10 | DW-B10 |
| [x] | BE-53 | Inside the transaction (before rollback), the shadow run reads up to 50 sample after-rows for inserts and updates; after rollback it reads breakdowns (`important_columns`), derived-group counts, total row counts per table and up to 50 masked sample before-rows. | BE B10 | B10 | DW-B10 |
| [x] | BE-54 | Cascade expectations: per FK path to the target (from `pg_constraint`, depth ≤ 3), the count of child rows referencing affected keys, stored in `actions.cascade_expectations`. | BE B10; D-021 | B10 | DW-B10 |
| [x] | BE-55 | The shadow run outputs a `Diff` and writes one `preview_rows` row per affected row (table, PK, before hash, change) with `COPY`. | BE B10 | B10 | DW-B10 |
| [x] | BE-56 | A table without a primary key fails the shadow run, so the write is blocked. | BE B10; D-004 | B10 | *integration test* |
| [x] | BE-57 | At most `MAX_CONCURRENT_SHADOWS` (2) shadow runs at once; a write waits up to 10 s for a slot, then fails closed with the `busy` message; each run times out after `SHADOW_TIMEOUT_SECONDS` (default 60 s). | BE B10; D-143 | B10 | *integration test* |
| [x] | BE-58 | The shadow branch is always deleted in a `finally`; on startup, under an advisory lock, leftover `shadow-*` branches older than 15 minutes are deleted. | BE B10 | B10 | *unit test with FakeNeonClient* |
| [x] | BE-59 | Knowledge chunks come from an automatic schema scan (one chunk per table: columns, types, FKs), `schema_docs.md` split by heading (`## table: <name>` tags the table), and uploads split the same way with an optional `tables` tag. | BE B11 | B11 | DW-B11 |
| [x] | BE-60 | Retrieval returns all chunks tagged with the affected tables plus the top 10 by vector similarity (D-148) to the intent, de-duplicated; the chunks used are saved to `actions.knowledge_used`. | BE B11 | B11 | DW-B11 |
| [x] | BE-61 | `Embedder.embed(texts)` defaults to fastembed `BAAI/bge-small-en-v1.5` (384 dims); stores never embed. | BE B11 | B11 | *unit test with fake embedder* |
| [x] | BE-62 | `VectorStore` has `upsert`, `search(vector, k, tables=None) -> [(chunk_id, score)]` and `delete`; results are chunk IDs and text is read from Postgres; `PgVectorStore` is the default (`VECTOR_STORE`). | BE B11 | B11 | TEST-12 |
| [x] | BE-63 | A `reindex` command rebuilds the active vector store from `knowledge_chunks`. | BE B11 | B11 | *integration test* |
| [x] | BE-64 | The impact engine computes deterministic facts in code: affected rows by important column, derived groups, cascades and any percentage needed. | BE B12 | B12 | DW-B12 |
| [ ] | BE-65 | The summary model (`SUMMARY_MODEL`, Gemini Flash by default) writes an `ImpactReport` (headline, key points, concerns) via structured output from the facts and retrieved knowledge, told to use only the given numbers, as digits. | BE B12 | B12 | DW-B12 |
| [ ] | BE-66 | Summary and judge prompts see only `sql_display` and masked samples. | BE B12, §12 | B12, B13 | *unit test on prompt contents* |
| [x] | BE-67 | Number guardrail: every number in the output (regex `\d[\d,]*(\.\d+)?(\s*(%|percent\b))?`, commas stripped, after NFKC; number words too) must be in the allowed set (facts, numbers in the intent and in `sql_display`, rule thresholds; D-150), else a template summary is used. | BE B12 | B12 | DW-B12 |
| [x] | BE-68 | Input and output tokens are recorded from `usage_metadata`; `llm_cost_usd` = tokens × the `LLM_PRICES` table. | BE B12 | B12 | *unit test* |
| [x] | BE-69 | Summary calls have a 20-second timeout and a max-token cap (`LLM_TIMEOUT_SECONDS`, `LLM_MAX_TOKENS`); on error or timeout the template summary is used. | BE B12, §11 | B12 | *unit test with failing fake* |
| [x] | BE-70 | After the summary call, if `llm_cost_usd` is over `MAX_LLM_COST_PER_ACTION_USD` (0.10), the judge is skipped and the write takes the judge-failure path (at least review, `ai_unavailable` message); the summary is kept. | BE §11, B14; D-033 | B14 | DW-B14 (cost cap branch) |
| [x] | BE-71 | `Judge.assess(context) -> JudgeResult` (risk_level low-critical, recommendation allow/review/block, confidence 0-1, reason); `JUDGE_PROVIDER` picks the implementation and the workflow never knows which ran. | BE B13 | B13 | DW-B13 |
| [x] | BE-72 | The judge runs only on writes the rules did not block, and never on the fast lane. | BE B13 | B13, B14 | DW-B14 |
| [x] | BE-73 | A `confidence` below `JUDGE_MIN_CONFIDENCE` (0.6) on an allow recommendation counts as review; confidence is stored for calibration. | BE B13 | B13 | *unit test* |
| [x] | BE-74 | On judge error, timeout (20 s) or unparseable result, the decision is at least review, with the `ai_unavailable` message. | BE B13; D-008 | B13 | DW-B13 |
| [ ] | BE-75 | `LLMJudge` (MVP default) uses `JUDGE_MODEL` (Gemini 3.8 Flash by default, D-158) with structured output and writes its own reason. | BE B13 | B13 | DW-B13 |

### Approval, commit, hook and API (B14-B20)

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [x] | BE-76 | A LangGraph graph handles writes only; its state holds action ID, pinned SQL, intent, classification, diff, knowledge, impact, judge result, decision and reviewer note; nodes and edges follow BE §7 plus the fast-lane edge. | BE §7, B14 | B14 | DW-B14 |
| [x] | BE-77 | Review is two nodes: `request_review` sets `awaiting_review` and writes the audit event; `human_review` contains only `interrupt()`. | BE B14; D-025 | B14 | DW-B14 (status after resume) |
| [x] | BE-78 | The checkpointer is `AsyncPostgresSaver` with `thread_id = action_id`; its tables are created by a migration, run as `blastproof_owner`, that holds the library's DDL (no `setup()` call, D-173), grants `blastproof_app` CRUD on the three data tables and `SELECT` on `checkpoint_migrations`, and sets no `ALTER DEFAULT PRIVILEGES` (D-174); the app never calls `setup()`. | BE §5, B14; D-013 | B14 | *migration test* |
| [x] | BE-79 | Each node updates `actions.status` and writes an audit event; any exception sets the action to failed and blocks it. | BE B14 | B14 | DW-B14 |
| [ ] | BE-80 | The fast lane skips the shadow run, judge and LLM summary, uses a template summary, goes to commit (which checks the row count), and still runs through the graph with a full audit timeline. | BE §7; D-007 | B14, B16 | DW-B14; MVP-B02 |
| [x] | BE-81 | `start(action)` launches the graph with `asyncio.create_task` and keeps the task in a module-level set until done. | BE B15 | B15 | *unit test* |
| [x] | BE-82 | `wait_for_decision(action_id, timeout)` checks the `actions` row every half second. | BE B15 | B15 | *unit test* |
| [x] | BE-83 | `resume()` first claims the decision with `UPDATE … WHERE id=$1 AND status='awaiting_review' RETURNING id`; zero rows returns 409 and never calls `Command(resume=…)`. | BE B15; D-006 | B15 | DW-B15 |
| [x] | BE-84 | A background loop every 10 minutes, under an advisory lock, expires reviews older than `REVIEW_TIMEOUT_HOURS` (24), fails actions stuck in `previewing` over 5 minutes, and deletes `preview_rows`, `before_images`, `hook_events` and `login_attempts` older than `RETENTION_DAYS` (30); `audit_events` are never deleted. | BE §5, B15; D-048 | B15 | DW-B15 (expired case); *tests for the others* |
| [ ] | BE-85 | Startup recovery, under the advisory lock and before serving: `received`/`classified`/`previewing` fail with reason `restart`; `approved`/`rejected` are resumed if paused at `interrupt()`, continued from checkpoint if past it, or failed if the thread is missing; `committing` goes to the commit reconciler. | BE B15; D-025, D-029 | B15, B16 | DW-B15, DW-B16 |
| [ ] | BE-86 | On SIGTERM, new writes get `busy` and in-flight previews and commits get up to 45 s; `fly.toml` sets `kill_timeout = 60`. | BE B15, §15; D-025 | B15, Deploy | *integration test of drain* |
| [ ] | BE-87 | The commit uses `blastproof_writer` on the company main branch (direct endpoint, `TimeZone=UTC`) in one company transaction with `SET LOCAL lock_timeout = '3s'` and `statement_timeout = '30s'`, after setting the action to `committing`. | BE B16, §12 | B16 | DW-B16 |
| [ ] | BE-88 | The commit inserts into `blastproof.applied_actions … ON CONFLICT DO NOTHING RETURNING`; no row returned means already applied: roll back and mark committed. | BE B16; D-006 | B16 | DW-B16 (commit twice) |
| [ ] | BE-89 | Row check: approved deleted/updated keys are locked `FOR UPDATE` and their `ROW_HASH_SQL` hashes compared with `preview_rows.before_hash`; any difference or missing row is drift; the returned JSON rows are the before-images. | BE B16; D-005 | B16 | DW-B16 |
| [ ] | BE-90 | Cascade check: the same per-FK-path count queries run on main and must equal `actions.cascade_expectations`. | BE B16; D-021 | B16 | DW-B16 (SET NULL case) |
| [ ] | BE-91 | `pinned_sql` runs with `RETURNING <pk>`; for DELETE and UPDATE the key set must equal the approved set; for INSERT the count must match. | BE B16; D-005 | B16 | DW-B16 (new matching row case) |
| [ ] | BE-92 | On mismatch: roll back, mark `drifted`, re-preview once automatically and send back to review with what changed; a second drift fails with the `drifted` message; never auto-approve after drift. | BE B16 | B16 | DW-B16 |
| [ ] | BE-93 | Before-images are inserted and committed in Blastproof's DB before the company COMMIT, replacing leftovers from an earlier attempt. | BE B16; D-022 | B16 | DW-B16 (crash cases) |
| [ ] | BE-94 | A Postgres error on COMMIT deletes the before-images and marks the action failed; a dropped connection or timeout leaves it `committing` for the reconciler; success sets `committed` and writes the audit event. | BE B16; D-029 | B16 | DW-B16 |
| [ ] | BE-95 | Commit reconciler: if the `applied_actions` row exists, mark committed; if not, re-run the whole commit procedure. | BE B16; D-022 | B16 | DW-B16 |
| [ ] | BE-96 | MVP before-images cover the target table only; cascaded rows are recorded as counts. | BE B16 | B16 | *integration test* |
| [ ] | BE-97 | The commit refuses any action whose system's connection profile doesn't match the connection it is about to use. | BE B20; D-027 | B16/B20 | *integration test* |
| [ ] | BE-98 | `query_database` runs reads directly; for writes it creates an action (with `retry_of` if given), starts the workflow, waits up to `REVIEW_WAIT_SECONDS` (20), and replies with the matching template. | BE §6, B17; D-012 | B17 | DW-B17 |
| [ ] | BE-99 | `get_action_status` long-polls up to `wait_seconds` (max `STATUS_MAX_WAIT_SECONDS`, 50); after `AGENT_POLL_BUDGET_MINUTES` (10) of waiting it returns `still_waiting`. | BE §6; D-012, D-020 | B17 | DW-B17; *test for budget* |
| [ ] | BE-100 | `agent_messages.py` has one template per outcome: committed, blocked, pending, still_waiting, rejected, drifted, expired, busy, ai_unavailable, failed, not_found, plus the `truncated` notice for reads, with the wording in BE §6. | BE §6; D-039, D-055 | B6 (truncated), B17 | TEST-08 |
| [ ] | BE-101 | `claude_hook/pre_tool_use.py` reads the tool call JSON from stdin and posts it to `/v1/hook/pre-tool-use` with `BLASTPROOF_AGENT_KEY`, using an HTTP timeout shorter than the hook timeout. | BE B18 | B18 | DW-B18 |
| [ ] | BE-102 | On deny the script prints the reason to stderr and exits 2; on allow it prints nothing and exits 0, never emitting a JSON allow. | BE B18 | B18 | DW-B18 |
| [ ] | BE-103 | If Blastproof is unreachable, times out, returns 5xx or unparseable output, the script retries once after 1 s; then Bash/PowerShell/Monitor/Edit/Write/NotebookEdit exit 2, and Read/Grep/Glob use a local deny-list (`.env*`, credential and key files, the backend directory, `.claude/settings*.json`) and are otherwise allowed. | BE B18; D-026 | B18 | DW-B18 (backend stopped) |
| [ ] | BE-104 | The hook is registered under `PreToolUse` in `.claude/settings.json` with a matcher covering at least Bash, PowerShell, Monitor, Edit, Write, NotebookEdit, Read, Grep, Glob, and `onFailure: block` with a short timeout; `claude_hook/` ships an example settings file. | BE §4, B18 | B18 | DW-B18 |
| [ ] | BE-105 | `hook_rules.py` denies `psql` and `pg_dump`, reading `.env` or credential files (by Read, Grep, Glob or Bash), `terraform apply`/`destroy`, `git push --force`, `rm -rf` outside the workspace, and edits to `.env` and `.claude/settings*.json`; everything else is allowed. | BE B18, §12 | B18 | DW-B18 |
| [ ] | BE-106 | Hook rules split commands with `shlex` and normalise paths (`..`, `~`, drive letters, case on Windows) before matching. | BE B18 | B18 | *unit tests* |
| [ ] | BE-107 | Every hook call writes a `hook_events` row with a masked summary; hook calls never create `actions`. | BE §5, B18; D-014 | B18 | *integration test* |
| [ ] | BE-127 | The hook endpoint checks `BLASTPROOF_AGENT_KEY` with `get_current_agent` (wrong or deactivated key → 401, which the script treats as a deny for every tool) and stores the calling agent and its owner on each `hook_events` row. | BE B18, §14; D-031 | B18 | TEST-03 (hook key cases); *integration test* |
| [ ] | BE-108 | Auth routes: `POST /v1/auth/login` (anyone) and `GET /v1/auth/me` (ID, name, email, role). | BE §6, B5, B19 | B5, B19 (pulled forward) | TEST-03 |
| [x] | BE-109 | `GET /v1/actions` returns the caller's actions (admin: all) as `ActionSummary` items with filters status, agent_id, table, from, to and limit/offset paging. | BE §6, B19 | B19 (pulled forward) | TEST-03 |
| [x] | BE-110 | `GET /v1/actions/{id}` returns the `ActionDetail` shape in BE §6, including `can_decide`; raw `sql` is never returned. | BE §6, B19 | B19 (pulled forward) | TEST-03; OPS-05 |
| [x] | BE-111 | `POST /v1/actions/{id}/approve` and `/reject` are allowed for the owner or an admin, go through the conditional claim, record `decided_by`, and reject requires a reason of at least 10 characters that is sent to the agent. Pulled forward with their authorization tests, because DW-B17 and GATE-03 use them. | BE §6, B19; D-053 | B19 (pulled forward after B15) | DW-B17; DW-B19; TEST-03 |
| [ ] | BE-112 | `GET /v1/stats` returns counts by status and decision, average shadow-run time and LLM cost today from the `actions` table, scoped to the caller (admin: all); "today" is the UTC day. | BE §6, §13; D-051 | B19 | DW-F09 |
| [ ] | BE-113 | `GET /v1/policies` returns the active rules read-only to any signed-in user. | BE §6 | B19 | TEST-03 |
| [ ] | BE-114 | Knowledge routes: `GET /v1/knowledge` (signed in; sources with chunk counts), `POST /v1/knowledge` (admin; multipart .md/.txt up to 1 MB, optional `tables`), `DELETE /v1/knowledge/{id}` (admin; removes source, chunks and vectors). | BE §6, B11, B19 | B19 | TEST-03; DW-F10 |
| [ ] | BE-115 | Systems routes (admin): `GET /v1/systems` and `POST /v1/systems/{id}/scan` (rescans the company schema into knowledge). | BE §6 | B19 | TEST-03 |
| [ ] | BE-116 | Agent routes: `GET /v1/agents` (own; admin: all), `POST /v1/agents` (any signed-in user; returns the key once plus `mcp_url` and the `claude mcp add …` and `BLASTPROOF_AGENT_KEY` lines built from `PUBLIC_BASE_URL`), `POST /v1/agents/{id}/deactivate` (owner or admin). | BE §6, B19; D-015 | B19 | TEST-03; DW-F11 |
| [ ] | BE-117 | User routes (admin): `GET /v1/users` and `POST /v1/users` (creates a user with an initial password). | BE §6 | B19 | TEST-03 |
| [ ] | BE-118 | Other users only ever see a user's `display_name`, never their email. | BE §5 | B19 | *integration test* |
| [ ] | BE-119 | The backend serves its OpenAPI schema at `/openapi.json` and the API reference at `/docs`. | BE §6 | B19 | DW-B19 |
| [ ] | BE-120 | `bootstrap` creates an admin user, an agent and the `systems` row, runs the schema scan and ingests `schema_docs.md`. | BE B20 | B20 | DW-B20 |
| [ ] | BE-121 | `seed-demo-actions` creates two demo users, each with an agent, and actions for both in every status (awaiting_review, approved, rejected, expired, blocked, drifted, committed, failed), running the real graph with `FakeNeonClient` and a fake LLM (retrieval is real) so pending actions sit at a real `interrupt()`; `--add-pending` adds one new pending action on `users` for demo user 1. | BE B20; FE §16; D-049 | B20 (pulled forward after B15; re-run after B16) | DW-F04, DW-F06 |
| [ ] | BE-122 | Seeded actions belong to a "Demo seed (local)" system with `connection_profile = local`, so approving one never touches Neon. | BE B20; D-027 | B20 | *integration test* |
| [ ] | BE-123 | `export-openapi` writes the OpenAPI schema to `backend/openapi.json` with no server or database. | BE B20; FE §13 | B20 | OPS-05 |
| [ ] | BE-124 | `README.md` and a setup guide cover prerequisites, the three databases, `.env` setup, bootstrap, connecting Claude Code and installing the hook. | BE B20 | B20 | DW-B20 |
| [x] | BE-125 | All settings come from environment variables read once by `config.py`; `backend/.env` is git-ignored with a committed `.env.example`; production uses Fly.io secrets. | BE §11 | B1 | *check `.gitignore` and `.env.example`* |
| [x] | BE-126 | Configuration defaults match BE §11 (e.g. `REVIEW_WAIT_SECONDS` 20, `STATUS_MAX_WAIT_SECONDS` 50, `REVIEW_TIMEOUT_HOURS` 24, `MAX_CONCURRENT_SHADOWS` 2, `JUDGE_MIN_CONFIDENCE` 0.6, `RETENTION_DAYS` 30). | BE §11 | B1 onward | *unit test of Settings defaults* |

## SEC: security

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [x] | SEC-01 | Agent keys are `bp_` + `secrets.token_urlsafe(32)`; only the SHA-256 hash and first 8 characters are stored; the full key is shown once. | BE B5, §12 | B5 | *unit test* |
| [x] | SEC-02 | `create-agent` CLI and a `get_current_agent` dependency check the bearer key; a wrong key returns 401. | BE B5 | B5 | DW-B05 |
| [x] | SEC-03 | `create-user` CLI (email, display name, role admin/member); passwords are hashed with argon2. | BE B5, §12 | B5 | *unit test* |
| [x] | SEC-04 | Login returns a JWT with 8-hour expiry carrying `token_version`, signed with `JWT_SECRET`. | BE B5, §12 | B5 | DW-B05 |
| [x] | SEC-05 | Every request rejects inactive users and stale `token_version`s with 401. | BE B5 | B5 | DW-B05 |
| [x] | SEC-06 | Login is rate limited via `login_attempts`: 5 failures per 15 minutes per email+IP, and 20 failures per hour per email from any IP. | BE §5, B5 | B5 | DW-B05 |
| [x] | SEC-07 | `require_admin` rejects members with 403 on admin-only routes. | BE B5 | B5 | DW-B05 |
| [x] | SEC-08 | `blastproof_app` has only SELECT and INSERT on `audit_events`, and a `BEFORE UPDATE OR DELETE` trigger rejects changes even by the owner. | BE §5, B4; D-013 | B4 | DW-B04 |
| [x] | SEC-09 | `BLASTPROOF_MIGRATOR_URL` (owner role) is used only for migrations and checkpointer `setup()`, never by the running app. | BE §11; D-013 | B4, B14 | *code review* |
| [x] | SEC-10 | Agent SQL that touches the `blastproof` schema is blocked. | BE §5, B9 | B8, B9 | DW-B08 (`blastproof` schema case) |
| [x] | SEC-11 | All DDL from agents is blocked in the MVP. | SPEC §8; BE B9 | B9 | DW-B09; MVP-B05 |
| [ ] | SEC-12 | SQL, intents, uploaded documents and sample rows are wrapped as untrusted data in every prompt and never followed as instructions. | SPEC §13; BE B13, §12 | B12, B13 | *unit test on prompt construction* |
| [x] | SEC-13 | A prompt-injection pattern check (e.g. "ignore previous instructions" in SQL comments or intent) forces review. | SPEC §7 (module 6, MVP); BE B13 | B13 | *unit test* |
| [ ] | SEC-14 | Connection strings and keys are never logged; sensitive columns are masked in sample rows and PII-looking literals in SQL before they reach logs, LLM prompts, LangSmith or the API. | BE §12; SPEC §13, §15 | B8-B13, Security | *Security pass review; unit tests* |
| [ ] | SEC-15 | LangSmith client-side masking (input/output hiding or anonymizer hooks) is a second layer behind prompt masking. | BE §12 | Security | *trace inspection* |
| [ ] | SEC-16 | `before_images` are unmasked but never leave the backend and are deleted after `RETENTION_DAYS`. | BE §12 | B15, B16 | DW-B15; *authz test that no route returns them* |
| [x] | SEC-17 | `FAKE_NEON_ADMIN_URL` is refused at startup when `APP_ENV=production`. | BE §11 | B10 | *unit test* |
| [ ] | SEC-18 | The demo workspace gets only `BLASTPROOF_AGENT_KEY`. | BE §11, B7 | B7, B18 | DW-B07 |
| [ ] | SEC-19 | On the deployed demo, company credentials exist only in Fly.io secrets and the machine running Claude Code holds no production company credentials. | BE §12; D-002 | Deploy | MVP-B01 |
| [ ] | SEC-20 | Each agent is capped at `MAX_WRITES_PER_MINUTE_PER_AGENT` (30) writes per minute, counted from its write actions in the last 60 s before a new one is created; over the cap it gets the `busy` message, no action is created, and `event=rate_limited` is logged. | BE §11, §12, B17; D-032 | B17 | DW-B17 (rate-limit tests) |
| [ ] | SEC-21 | Dependency versions are pinned in `uv.lock` and GitHub Dependabot alerts are enabled. | BE §12 | B1/B2 | *repo settings check* |
| [ ] | SEC-22 | Production backend has no CORS (`CORS_ORIGINS` empty); the dashboard reaches it server to server. | BE §11, B19; FE §11 | B19, Deploy | *config check* |
| [ ] | SEC-23 | The JWT lives only in an httpOnly cookie (`Secure` in production, `SameSite=Lax`, `Path=/`); never in localStorage or page JavaScript. | CL; FE §4, §11 | F3 | DW-F03; MVP-F01 |
| [ ] | SEC-24 | The login route stores the JWT in the cookie and returns only the user's name and role to the browser. | FE §4, F3 | F3 | DW-F03 |
| [ ] | SEC-25 | The BFF route adds `Authorization` from the cookie and drops incoming cookies before calling the backend. | FE F3, §12 | F3 | TEST-10 |
| [ ] | SEC-26 | The proxy rejects any POST or DELETE whose `Origin` is not the dashboard's own address. | FE F3, §11, §12; D-050 | F3 | TEST-10 |
| [ ] | SEC-27 | Agent-provided and uploaded text (SQL, intents, documents, sample rows) is rendered as plain text; no `dangerouslySetInnerHTML`, no Markdown from uploads rendered as HTML. | CL; FE §11 | F5-F11 | *lint rule or grep; code review* |
| [ ] | SEC-28 | `X-Frame-Options: DENY` and `Referrer-Policy` are set on every page in `next.config`. | FE §11 | F1/F12 | *header check* |
| [ ] | SEC-29 | A nonce-based CSP applies to `/dashboard/*`, `/login` and `/api/*`; the static landing page uses `script-src 'self' 'unsafe-inline'` (hash-based if practical). | FE §11; D-028 | F1 (verify), F3, F13 | *header check* |
| [ ] | SEC-30 | Logout clears the session cookie. | FE §11, F4 | F3, F4 | MVP-F01 |
| [ ] | SEC-31 | The dashboard needs only `BLASTPROOF_API_URL`, `SESSION_COOKIE_NAME` and `SESSION_MAX_AGE_SECONDS` (28800), all server-side; none start with `NEXT_PUBLIC_`; `frontend/.env.local` is git-ignored with a committed `.env.example`. | FE §10 | F1, F3 | *config check* |
| [ ] | SEC-32 | Hiding controls by role in the UI is convenience only; every role check is enforced by the backend. | FE §11; CL | F4, B19 | TEST-03; MVP-F06 |
| [ ] | SEC-33 | A deactivated agent's key is rejected with 401 on `/mcp` and on the hook endpoint. | BE B5; D-030 | B5 | DW-B05 |
| [ ] | SEC-34 | `get_action_status` and `retry_of` look up action IDs through `visible_actions` for the agent's owner with admin widening off (filter is the owner's `user_id` even when the owner is an admin); an unknown or foreign ID gets the `not_found` message. | CL; BE B17; D-055 | B17 | *integration test with another owner's action ID* |

## FE: dashboard functional requirements

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [ ] | FE-01 | Next.js App Router app in `frontend/` with TypeScript, Tailwind, ESLint, `src/`; shadcn/ui initialised with button, card, table, badge, dialog, input, textarea, form, sonner, dropdown-menu, sidebar, skeleton; Prettier and a `typecheck` script. | FE F1 | F1 | DW-F01 |
| [ ] | FE-02 | `npm run gen:api` reads `http://localhost:8000/openapi.json` into `src/lib/api/schema.d.ts`; `src/lib/api/client.ts` is an openapi-fetch client with base URL `/api/blastproof`. | FE §5, F2 | F2 | DW-F02 |
| [ ] | FE-03 | A TanStack Query provider in the root layout with `refetchOnWindowFocus` on and polling paused while the tab is hidden; query devtools in development only. | FE §2, F2 | F2 | *unit test or manual check* |
| [ ] | FE-04 | Route handlers `api/auth/login`, `api/auth/logout` and the BFF catch-all `api/blastproof/[...path]`. | FE §4, §6, F3 | F3 | DW-F03 |
| [ ] | FE-05 | The auth proxy (`src/proxy.ts`) redirects any `/dashboard` request without a session cookie to `/login`; `/` and `/login` are public. | FE §4, F3 | F3 | DW-F03; MVP-F01 |
| [ ] | FE-06 | The BFF route forwards method, path, query string, body and content type, and returns the backend's status and body unchanged. | FE §4, F3 | F3 | TEST-10 |
| [ ] | FE-07 | A global TanStack Query error handler sends any 401 to `/login`. | FE §4, F3 | F3 | DW-F03 |
| [ ] | FE-08 | A `useMe()` hook reads the user's name and role from `/v1/auth/me`. | FE F3 | F3 | *unit test* |
| [ ] | FE-09 | `app/dashboard/layout.tsx` (a real URL segment) has a sidebar (Overview, Review with count badge, History, Knowledge, and Settings with Agents, Rules and, for admins, Users) and a user menu with logout. | FE §6, F4; D-046 | F4 | DW-F04 |
| [ ] | FE-10 | The review badge reads `total` from `/v1/actions?status=awaiting_review&limit=1` every 5 seconds. | FE F4 | F4 | DW-F04; MVP-F02 |
| [ ] | FE-11 | Admin-only links (Knowledge, Users) are hidden from members. | FE F4, F10 | F4 | MVP-F06 |
| [ ] | FE-12 | Shared components `StatusBadge`, `RiskBadge`, `TimeAgo`, `SQLBlock`, `NumberBreakdown`, `AuditTimeline`, `DataTable` with pagination, plus skeleton loading, empty and error states. | FE F5 | F5 | DW-F05 |
| [ ] | FE-13 | The dashboard has the ten pages and routes in FE §7: `/`, `/login`, `/dashboard`, `/dashboard/review`, `/dashboard/actions/[id]`, `/dashboard/history`, `/dashboard/settings/agents`, `/dashboard/settings/rules`, `/dashboard/knowledge` (admin), `/dashboard/settings/users` (admin). | FE §7 | F3-F13 | MVP-F checks |
| [ ] | FE-14 | Review queue lists the user's `awaiting_review` actions oldest first (agent, intent, headline, risk badge, waiting time), polling every 5 seconds, each row linking to its change request page. | FE §7, F6 | F6 | DW-F06; MVP-F02 |
| [ ] | FE-15 | Change request page shows, top to bottom: header (status and risk badges, agent, age, decider), plain-language impact (headline, key points, concerns), decision bar, intent and `sql_display`, impact numbers (rows per table with cascades marked, breakdowns, derived groups such as "paying: 12"), masked sample rows, judge result (recommendation, confidence, reason, which judge), matched rules with reasons, knowledge used (source, table, first line), audit timeline. | FE §7, F7 | F7 | MVP-F03 |
| [ ] | FE-16 | When values were pinned, the SQL block shows the original next to it and lists the pinned values; a retry links to the action it replaces (`retry_of`). | FE §7 | F7 | *component test* |
| [ ] | FE-17 | The change request page refreshes every 5 s while pending and shows "not found" for an action that isn't the user's. | FE §7 | F7 | TEST-09 step 6 |
| [ ] | FE-18 | The decision bar shows only while the action is pending and `can_decide` is true; approve and reject are mutations that refresh the action, the queue and the badge on success. | FE §7, F7 | F7 | DW-F07 |
| [ ] | FE-19 | Reject opens a dialog requiring a reason of at least 10 characters (zod). | FE F7 | F7 | TEST-10; MVP-F04 |
| [ ] | FE-20 | A 409 on approve or reject shows a toast naming who already decided. | FE §7, F7 | F7 | DW-F07; MVP-F04 |
| [ ] | FE-21 | History lists the user's actions with filters for status, agent, table and date range stored in the URL, with server-side pagination of 25 rows. | FE §7, F8 | F8 | DW-F08; MVP-F05 |
| [ ] | FE-22 | Overview shows count cards (pending reviews, decisions today, blocked today, average preview time, LLM cost today; "today" is the UTC day, labelled on the cards) and the 10 latest actions, refreshing every 15 seconds. | FE §7, F9; SPEC §12; D-051 | F9 | DW-F09 |
| [ ] | FE-23 | Knowledge page (admin only) lists sources with chunk counts, uploads a .md or .txt file up to 1 MB with optional tables (multipart through the BFF), deletes with a confirm dialog, and has a "Rescan schema" button. | FE §7, F10 | F10 | DW-F10 |
| [ ] | FE-24 | Agents page lists the user's agents (key prefix, active state); create shows the key once with a copy button and the setup lines exactly as returned by `POST /v1/agents`, plus a link to the README's hook steps; deactivate asks for confirmation. | FE §7, F11 | F11 | DW-F11; MVP-F07 |
| [ ] | FE-25 | Users page (admin only) lists users and roles and creates a user with an initial password. | FE §7, F11 | F11 | TEST-03 (backend) |
| [ ] | FE-26 | Rules page renders `/v1/policies` as a read-only table (name, conditions, outcome, reason). | FE §7, F11 | F11 | *component test* |
| [ ] | FE-27 | Every control has keyboard focus and labels; color contrast is readable; dark mode, page titles and a friendly 404 page exist; every page works at narrow (split-screen) width. | FE F12 | F12 | DW-F12 |
| [ ] | FE-28 | The landing page at `/` is static and server-rendered, needs no login and makes no API calls, with sections: hero (name, pitch, "Watch the demo", "View on GitHub", "Log in"), problem (incidents linked to sources), how it works in four steps, change-request screenshot, what makes it different, eval results, architecture diagram, security, FAQ, footer. | FE F13 | F13 | DW-F13 |
| [ ] | FE-29 | The landing page has page metadata, an Open Graph image and a sitemap, and publishes only measured numbers. | FE F13 | F13 | DW-F13 |

## EVAL: evals

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [x] | EVAL-01 | `backend/evals/scenarios/incidents.yaml` holds the MVP incident replays: Replit-style mass delete, DROP TABLE, delete without WHERE (and `WHERE true`), `psql` bypass attempt, hook-settings edit, and PocketOS-lite (credential hunt denied, no credential in the workspace). | SPEC §14; BE B3, §14; D-011 | B3 | DW-B03 |
| [ ] | EVAL-02 | A first `labeled.yaml` with about 20 labeled actions is written in step 3; the Evals stage tops it up to 50 SQL statements labeled allow, review or block, including subtle cases. | BE B3, §14; D-035 | B3, Evals | DW-B03; pytest: at least 50 entries and every label present |
| [x] | EVAL-03 | Each scenario has `sql`, `intent`, the expected decision and, where known, expected row counts against the seed as of `ANCHOR_DATE`; the scenario files carry `anchor_date: 2026-10-01` at the top, which backend tests and evals read. | BE B3; D-052 | B3 | DW-B03; CI test: `anchor_date` equals `seed_info.anchor_date` on local `company_main` |
| [x] | EVAL-04 | Scenario files load and validate against a Pydantic schema. | BE B3 | B3 | DW-B03 |
| [ ] | EVAL-05 | Steps 8, 9, 10, 12 and 13 each run the scenarios that apply to them. | BE B3 | B8-B13 | DW-B08, DW-B09, DW-B10 |
| [ ] | EVAL-06 | Incident replays stay at 100% caught. | CL; SPEC §14; BE §14 | Evals | MVP-B10; CI |
| [ ] | EVAL-07 | Metrics: catch rate, false block rate, explanation accuracy (numbers checked by code; the LLM-as-judge score is deferred, D-036) and added latency (p50/p95 of `total_ms` per path: read, fast lane, previewed; read overhead = Blastproof time minus the same query run directly). | SPEC §14; BE §14; D-034, D-036 | Evals | MVP-B10; eval runner output |
| [ ] | EVAL-08 | `uv run python -m evals.run` prints a results table and saves it as JSON. | CL; BE §14 | Evals | *run it* |
| [ ] | EVAL-09 | The labeled set is uploaded as a LangSmith dataset to compare variants as experiments (MVP: rules only vs rules plus judge, with vs without RAG context). | SPEC §14; BE §14 | Evals | *LangSmith experiment exists* |
| [ ] | EVAL-10 | Incident replays and rule-only evals run on every pull request (no LLM cost); the full LLM eval runs nightly, on manual trigger and before any change to `SUMMARY_MODEL` or `JUDGE_MODEL`. Both the PR run and the nightly run fail if any incident replay is not caught; false block rate is recorded with no threshold yet. | SPEC §14; BE §14, §15; D-045 | B2, Evals | CI |
| [ ] | EVAL-11 | Catch rate and false block rate on the labeled set are recorded. | BE §17 | Evals | MVP-B10 |

## OBS: observability

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [ ] | OBS-01 | LangSmith traces every LangChain call and LangGraph run (so every write has a trace), tagged with `action_id`, `agent_id` and `env`. | SPEC §7, §15; BE §13 | B12-B14 | MVP-B09 |
| [ ] | OBS-02 | Structured JSON logs bind `request_id` and `action_id` on every line and log each stage with its duration (classify, shadow, retrieve, impact, judge, decide, commit). | BE §13; SPEC §15 | B1, B8-B16 | *log inspection test* |
| [x] | OBS-03 | Every fail-closed block logs `event=fail_closed` with a reason code. | BE §13; SPEC §15 | B14 | *integration test* |
| [x] | OBS-04 | Middleware adds an `X-Request-ID` header to every response. | BE §13 | B1 | *integration test* |
| [x] | OBS-05 | Actions record `preview_ms`, `total_ms`, LLM tokens and `llm_cost_usd`. | BE §5, B12 | B10, B12, B14 | *integration test* |
| [ ] | OBS-06 | Every action has a complete audit timeline in `audit_events`: one event per status change, named after the new status, plus `shadow_done`, `decided` and `fail_closed`. | BE §5; SPEC §7, §10; D-037 | B6, B14-B16 | MVP-B09; *integration test: event types match the status history* |
| [ ] | OBS-07 | Every decision carries a human-readable reason. | SPEC §7, §10 | B9, B13, B14 | *eval check that reason is non-empty* |
| [x] | OBS-08 | The approve or reject claim writes its audit event in the same transaction, with the approver's `user_id` and `display_name` in `data`; auto-approvals carry no approver. | SPEC §13; BE §5, B15; D-037 | B15 | *integration test* |

## OPS: CI, infra and deployment

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [ ] | OPS-01 | Backend CI on every PR: install with uv, `ruff check`, `ruff format --check`, two Postgres service containers with the role init scripts, `seed.py --target local`, Alembic migrations, tests and rule-only evals with `NEON_CLIENT=fake`; CI never talks to Neon. | BE B2, §15 | B2 (extended later) | DW-B02 |
| [ ] | OPS-02 | A second workflow runs the LLM evals nightly with the `GOOGLE_API_KEY` repository secret. | BE §14, §15 | Evals | *workflow run* |
| [ ] | OPS-03 | Frontend CI job on every PR: `npm ci` (cached), lint, typecheck, test (Vitest), build. | FE §13 | F1 onward | MVP-F08 |
| [ ] | OPS-04 | `main` is protected so merging requires both backend and frontend jobs to pass. | FE §13 | Deploy | *repo settings check* |
| [ ] | OPS-05 | Contract check: the backend's `export-openapi` output regenerates `schema.d.ts`, and the job fails if the file changed. | FE §13, §16 | F2 + B20 | MVP-F08 |
| [ ] | OPS-06 | A nightly or manual workflow starts Postgres and the backend, runs `seed-demo-actions`, starts the dashboard and runs Playwright. | FE §13 | F7+ | MVP-F08 |
| [ ] | OPS-07 | Three company databases are never mixed: test/CI uses `company-db`, local dev uses Neon `blastproof-company-dev`, the deployed demo uses Neon `blastproof-company` (touched only by the deployed backend). | BE §15; D-003 | B3, B10, Deploy | *config review* |
| [ ] | OPS-08 | A multi-stage `Dockerfile` installs with uv, runs as a non-root user on port 8080, and downloads the embedding model at build time. | BE §15 | Deploy | *image build* |
| [ ] | OPS-09 | The Neon `blastproof-app` project holds Blastproof's production database with `CREATE EXTENSION vector`. | BE §2, §15 | Deploy | *manual check* |
| [ ] | OPS-10 | `fly.toml` sets `release_command = "alembic upgrade head"` (migrator URL), an HTTP health check on `/health`, `auto_stop_machines = "off"`, `min_machines_running = 1`, `kill_timeout = 60`, and the app runs on exactly one machine. | BE §15 | Deploy | *config review* |
| [ ] | OPS-11 | Every secret in BE §11 is set with `fly secrets set`; the owner loads the seed into `blastproof-company` (D-054); bootstrap is run once via `fly ssh console`; `rebase-dates` is run (by the owner) against `blastproof-company` before recording the demo. | BE §15 | Deploy | *manual checklist* |
| [ ] | OPS-12 | A GitHub Actions deploy job runs `flyctl deploy` on every merge to `main` using `FLY_API_TOKEN`. | BE §15; SPEC §16 | Deploy | MVP-B11 |
| [ ] | OPS-13 | Claude Code is pointed at the deployed MCP URL and the hook's `BLASTPROOF_AGENT_KEY` is updated. | BE §15 | Deploy | MVP-B01 |
| [ ] | OPS-14 | Vercel imports the repo with root directory `frontend/`, the three env vars (with `BLASTPROOF_API_URL` pointing at Fly.io), a functions region matching Fly.io and Neon, auto-deploy on `main` and preview URLs per PR. | FE §13 | Deploy (Stage 5) | *Vercel settings check* |
| [ ] | OPS-15 | The first admin is created with the backend's bootstrap command and the demo is run end to end on the deployed stack. | FE §13 | Deploy (Stage 5) | MVP-F checks |

## SPIKE: questions spikes must answer

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [x] | SPIKE-01 | MCP spike: mount path (`streamable_http_app()` serves at `/mcp`), lifespan and `session_manager.run()`, stateless vs session mode, how a tool reads request data, and Claude Code's MCP tool timeout vs the 50 s long poll. | BE §6, B6; WF Part 3 | before B6 | Spike output pasted; decisions.md |
| [x] | SPIKE-02 | Hook spike: matcher tool names (Read, Grep, Glob, MultiEdit, NotebookEdit), default hook timeout and whether a timeout fails open, `permissions.deny` path syntax, hook snapshot at startup, `disableAllHooks` and settings precedence, behaviour in bypass-permissions mode. | WF Part 3; BE B7, B18; SPEC §9 | before B7 | Spike output pasted |
| [x] | SPIKE-03 | Neon spike: create-branch payload, waiting on operations, non-owner role connection URIs, branch `expires_at`, free-plan caps, default Postgres version, pooled vs direct endpoint with asyncpg, project-scoped API keys, and measured create-to-first-query p50/p95. | BE §2, B10 | before B10 | Spike output; sets NFR-03 |
| [x] | SPIKE-04 | LangGraph spike: psycopg 3 setup (`autocommit=True`, `dict_row`, plain URL), `setup()` from an Alembic migration as `blastproof_owner`, and `interrupt()` re-running its node on resume. | BE B14 | before B14 | Spike output pasted |

## NFR: non-functional targets (spec §10)

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [ ] | NFR-01 | Read pass-through overhead stays under 50 ms at p95. | SPEC §10; BE §14; D-034 | Evals | Eval runner latency report (EVAL-07) |
| [ ] | NFR-02 | Fast-lane writes complete in under 300 ms of Blastproof time. | SPEC §10; BE §17; D-007 | B14-B17 | MVP-B02 |
| [x] | NFR-03 | Previewed writes meet a target set from the Neon spike's measured p50/p95 (spike measured p50 5.0 s to a usable branch and about 14 s for a whole preview; about 14 s for a small preview; target: under 20 s for a typical write (a few thousand rows); under 60 s for a write that touches most of a table, such as the 246k-row Replit delete, measured at 44 s on Neon, D-143). | SPEC §10; BE B10 | B10 | SPIKE-03, then *eval latency metric* |
| [ ] | NFR-04 | Pending approvals survive restarts; no action is lost or applied twice. | SPEC §10; BE §17 | B14-B16 | DW-B15, DW-B16; MVP-B07, MVP-B08 |
| [ ] | NFR-05 | Several agents and sessions run at once: an integration test runs about 5 writes plus reads concurrently with `FakeNeonClient`; every action ends in the expected state and no more than `MAX_CONCURRENT_SHADOWS` shadow runs overlap. | SPEC §10; BE B17; D-034 | B17 | DW-B17 (concurrency test) |
| [ ] | NFR-06 | Runs within free tiers plus LLM usage, under $100 per month at demo scale. | SPEC §10, §12 | Deploy | *monthly bill check* |

## DW: "Done when" checks

### Backend steps

| | ID | Done when | Source | Step | Verified by |
|---|---|---|---|---|---|
| [x] | DW-B01 | `docker compose up -d`, then `uv run uvicorn app.main:app --reload`, and `/health` returns `ok`. | BE B1 | B1 | Manual + test |
| [x] | DW-B02 | A pull request shows a green check. | BE B2 | B2 | CI |
| [x] | DW-B03 | The seed runs; a count by plan returns sensible numbers on the local and dev databases; the scenario files load and validate against a Pydantic schema. | BE B3 | B3 | Manual + test |
| [x] | DW-B04 | `alembic upgrade head` creates all tables locally; a test as `blastproof_app` fails to update or delete an audit event, and so does one as `blastproof_owner` (trigger). | BE B4 | B4 | Integration test |
| [x] | DW-B05 | Tests prove: wrong key or token, or a deactivated agent's key → 401; inactive user or bumped `token_version` → 401; 6th failed login in 15 min from one IP refused; 21st failed login in an hour for one email refused across IPs; admin-only route rejects members with 403. | BE B5 | B5 | Unit/integration tests |
| [x] | DW-B06 | After `claude mcp add --transport http blastproof http://localhost:8000/mcp --header "Authorization: Bearer <key>" --scope local`, Claude answers "how many pro users do we have?" through Blastproof and the call appears in `actions`. | BE B6 | B6 | Manual in Claude Code |
| [x] | DW-B07 | Searching the demo workspace and its environment for "postgres" finds nothing, and Claude's `Read` of the backend's `.env` path is denied. | BE B7 | B7 | Manual in Claude Code |
| [x] | DW-B08 | About 40 table-driven classifier tests pass, including CTE writes, `UPDATE … FROM`, `WHERE true`, pinning with a frozen clock, multi-statement, transaction control, `blastproof` schema access, unordered LIMIT and a function call in fast-lane VALUES; classifier-relevant incident scenarios pass. | BE B8 | B8 | Unit tests |
| [x] | DW-B09 | Unit tests cover each rule, pass-1 skip semantics and strictest-wins; every incident scenario decidable from SQL alone is blocked in pass 1. | BE B9 | B9 | Unit tests |
| [x] | DW-B10 | A DELETE of users inactive 6 months (clock frozen at `ANCHOR_DATE`) returns exact counts incl. cascaded `orders`/`order_items` and the SET NULL on `invoices`, with correct breakdowns and derived groups matching `incidents.yaml`; runs with `FakeNeonClient` in CI (incl. two previews in a row) and with `NeonApiClient` once by hand; a test proves identical row hashes in shadow run and commit across session time zones. | BE B10 | B10 | Integration tests + manual Neon run |
| [x] | DW-B11 | An action deleting users retrieves the "paying customer" and "enterprise contracts" rules. | BE B11 | B11 | Integration test |
| [ ] | DW-B12 | The report reads like "Deletes 48,213 users, including 12 paying customers (3 enterprise), and their orders through a cascade," and guardrail tests cover commas, percentages, intent numbers and an invented number. | BE B12 | B12 | Unit tests + manual read |
| [x] | DW-B13 | Tests show the judge escalates a risky allowed action, cannot downgrade a blocked one, a raising or timing-out judge produces review (never allow), and switching `JUDGE_PROVIDER` needs no code change. | BE B13 | B13 | Unit tests |
| [x] | DW-B14 | A test runs one write through every branch (fast lane, allow, review→approve, review→reject, block, judge failure→review, LLM cost cap exceeded→review); another proves the status after resume is `approved`, not `awaiting_review`. | BE B14 | B14 | Integration tests |
| [x] | DW-B15 | Tests prove two simultaneous approvals give one commit and one 409, an expired review cannot be approved, an approved-but-never-resumed action is resumed at startup, and an action left in `previewing` is failed at startup. | BE B15 | B15 | Integration tests |
| [ ] | DW-B16 | Approved write commits with matching counts; drift path triggered by a changed row, by a new matching row and by a nulled child FK; committing twice applies once; a kill after company commit is marked committed with before-images on restart; a kill before it is re-run and committed once; a dropped COMMIT connection is resolved by the reconciler; a restart while awaiting review then approval still commits. | BE B16 | B16 | Integration tests |
| [ ] | DW-B17 | In Claude Code, "clean up inactive users" returns pending; after rejecting it in `/docs`, the agent's next `get_action_status` returns the reason within seconds, and its rewritten query carries `retry_of`; tests cover the rate limit and the concurrency test passes. | BE B17 | B17 | Manual in Claude Code + integration tests |
| [ ] | DW-B18 | Claude trying `psql`, reading the backend's `.env` or editing its settings file is blocked with a clear reason; with the backend stopped, Bash and edits are denied, reading a normal source file works, and reading `.env` is still denied. | BE B18 | B18 | Manual in Claude Code + tests |
| [ ] | DW-B19 | An action can be reviewed and approved using only `/docs`, and the authorization matrix test passes. | BE B19 | B19 | Manual + TEST-03 |
| [ ] | DW-B20 | A fresh clone plus the README steps gives a working demo. | BE B20 | B20 | Manual |

### Frontend steps

| | ID | Done when | Source | Step | Verified by |
|---|---|---|---|---|---|
| [ ] | DW-F01 | `npm run dev` shows the starter page at `http://localhost:3000`. | FE F1 | F1 | Manual |
| [ ] | DW-F02 | Hovering `api.GET("/v1/actions/{id}")` in the editor shows the backend's real response type. | FE F2 | F2 | Manual |
| [ ] | DW-F03 | You can log in, the token never appears in browser JavaScript or local storage, and deleting the cookie sends you back to `/login`. | FE F3 | F3 | Manual |
| [ ] | DW-F04 | Creating a pending action with `seed-demo-actions --add-pending` makes the badge change within 5 seconds. | FE F4 | F4 | Manual |
| [ ] | DW-F05 | Vitest tests pass for the badges and formatting helpers. | FE F5 | F5 | `npm test` |
| [ ] | DW-F06 | A pending action added with `seed-demo-actions --add-pending` appears without a page refresh. | FE F6 | F6 | Manual |
| [ ] | DW-F07 | Approve and reject work end to end against the local backend, and two browser windows approving the same action show the 409 message in the second. | FE F7 | F7 | Manual |
| [ ] | DW-F08 | Changing filters updates the URL and the back button restores the previous view. | FE F8 | F8 | Manual |
| [ ] | DW-F09 | Overview numbers match a manual SQL count on Blastproof's database. | FE F9 | F9 | Manual |
| [ ] | DW-F10 | A document uploaded with table `users` appears in "Knowledge used" of the next action touching `users` (checked with `seed-demo-actions --add-pending`, no Neon or real LLM). | FE F10 | F10 | Manual |
| [ ] | DW-F11 | A newly created key works in Claude Code by copy-pasting the shown commands. | FE F11 | F11 | Manual in Claude Code |
| [ ] | DW-F12 | Lighthouse accessibility on the change request page scores 90 or above. | FE F12 | F12 | Lighthouse |
| [ ] | DW-F13 | A logged-out visitor sees the landing page, "Log in" leads to `/login`, Lighthouse scores 90+ for performance, accessibility and SEO, and the LinkedIn link preview looks right. | FE F13 | F13 | Lighthouse + manual |

### Stage gates

| | ID | Gate | Source | Step | Verified by |
|---|---|---|---|---|---|
| [x] | GATE-01 | Stage 1: Claude Code reads the demo DB only through Blastproof. | PROGRESS | after B7 | Gate audit (WF Prompt G) |
| [x] | GATE-02 | Stage 2: exact diffs and correct retrieval. | PROGRESS | after B13 | Gate audit (WF Prompt G) |
| [ ] | GATE-03 | Gate 1: the Replit scenario is caught end to end (held for review and approvable via `/docs`). | SPEC §18; PROGRESS | after B17 (approve/reject pulled forward, D-053) | Gate audit (WF Prompt G) |
| [ ] | GATE-04 | Gate 2: evals green and backend deployed. | SPEC §18; PROGRESS | Stage 4 | Gate audit (WF Prompt G) |

## MVP: done checklists

### Backend MVP (backend plan §17), checked on the deployed version with Claude Code

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [ ] | MVP-B01 | Claude Code reads the company database only through Blastproof; no credentials exist in its workspace; the machine running it holds no production company credentials. | BE §17 | B6, B7, Deploy | Final audit |
| [ ] | MVP-B02 | "Add a note to customer 42" commits through the fast lane in under 300 ms of Blastproof time. | BE §17; SPEC §17 | B8, B9, B14-B17 | Final audit (timings) |
| [ ] | MVP-B03 | "Clean up inactive users" is held for review with an impact report naming the paying and enterprise customers affected. | BE §17; SPEC §17 | B9-B17 | Final audit |
| [ ] | MVP-B04 | Rejecting it sends the reason to Claude, which writes a safer query that is approved and committed with matching counts. | BE §17; SPEC §17 | B15-B17 | Final audit |
| [ ] | MVP-B05 | DROP TABLE and delete-without-WHERE are blocked instantly with clear reasons. | BE §17 | B8, B9 | Final audit; EVAL-01 |
| [ ] | MVP-B06 | `psql` in the terminal and edits to `.claude/settings.json` are blocked by the hook. | BE §17; SPEC §17 | B18 | Final audit |
| [ ] | MVP-B07 | A change made between preview and commit triggers the drift path; no action is ever applied twice. | BE §17 | B16 | Final audit; DW-B16 |
| [ ] | MVP-B08 | A pending review survives a server restart. | BE §17 | B15, B16 | Final audit; DW-B16 |
| [ ] | MVP-B09 | Every action has a complete audit timeline and every write has a LangSmith trace; two users each see only their own agents' actions; an admin sees all. | BE §17 | B14-B19 | Final audit |
| [ ] | MVP-B10 | Incident replays score 100% caught; catch rate and false block rate on the labeled set are recorded. | BE §17 | Evals | Final audit |
| [ ] | MVP-B11 | CI is green, and merges to `main` deploy to Fly.io automatically. | BE §17 | B2, Deploy | Final audit |
| [ ] | MVP-B12 | A fresh clone reaches a working demo by following the README. | BE §17 | B20 | Final audit |

### Dashboard MVP (frontend plan §15), checked on the deployed Vercel site

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [ ] | MVP-F01 | Logged-out visitors see the landing page at `/`; every `/dashboard` page redirects them to login; login works; the token never appears in browser JavaScript or local storage; logout and expiry send you to login. | FE §15 | F3, F4, F13 | Final audit |
| [ ] | MVP-F02 | A pending action created by Claude Code appears in the review queue and nav badge within 5 seconds. | FE §15 | F4, F6 | Final audit |
| [ ] | MVP-F03 | The change request page shows the headline, impact numbers, masked sample rows, judge result, matched rules and audit timeline. | FE §15 | F7 | Final audit |
| [ ] | MVP-F04 | Approve and reject work; reject requires a reason that reaches the agent; a second approval shows the 409 message. | FE §15 | F7 | Final audit |
| [ ] | MVP-F05 | History filters live in the URL and survive back and forward navigation. | FE §15 | F8 | Final audit |
| [ ] | MVP-F06 | Each user sees only their own agents' actions and manages their own agent keys; admins see everything and manage users and knowledge; members cannot see admin controls, and the backend refuses them anyway. | FE §15 | F4, F10, F11, B19 | Final audit; TEST-03 |
| [ ] | MVP-F07 | A new agent can be connected to Claude Code by copy-pasting the setup lines shown once. | FE §15 | F11 | Final audit |
| [ ] | MVP-F08 | Lint, typecheck, unit tests, build and the contract check pass in CI; the Playwright flow passes. | FE §15 | F1-F13 | Final audit |
| [ ] | MVP-F09 | Lighthouse accessibility score of 90 or above on the change request page. | FE §15 | F12 | Final audit |

## DEL: portfolio deliverables (spec §17, §20)

| | ID | Requirement | Source | Step | Verified by |
|---|---|---|---|---|---|
| [ ] | DEL-01 | Public GitHub repo whose README opens with the pitch, a 30-second GIF of the "whoa" moment, the architecture diagram and the eval results table. | SPEC §20 | B20 (README); rest not assigned | Manual |
| [ ] | DEL-02 | Two-to-three-minute demo video (also used on the landing page). | SPEC §17, §20; FE F13 | Stage 5 | Manual |
| [ ] | DEL-03 | `docker compose up` plus the seed command gives a working dashboard with seeded demo actions and real local previews; the full Neon demo needs only the Neon dev project (README). | SPEC §20 | B20 | DW-B20 |
| [ ] | DEL-04 | MVP demo script: fast-lane note, Replit mass delete with reject and retry, bypass attempt blocked, close on measured eval numbers. | SPEC §17; D-011 | All | Manual demo run |
| [ ] | DEL-05 | Eval report: datasets, metrics, before/after experiment results. | SPEC §20 | Evals | Manual |
| [ ] | DEL-06 | Design notes on the key trade-offs from spec §19 and how they were solved. | SPEC §20 | not assigned | Manual |
| [ ] | DEL-07 | Blog post or LinkedIn write-up, 4-5 resume bullets with real measured numbers, and prepared interview answers. | SPEC §20 | not assigned | Manual |
| [ ] | DEL-08 | (Optional) live deployed demo with a read-only public dashboard. | SPEC §20 | not assigned | Manual |

---

## Implied, needs confirmation

These follow from the plans but are not stated outright, or a stated requirement has no step,
number or check. Each needs a yes/no (and a plan edit or decision entry) before its step starts.

| ID | What is implied or missing | Where it comes from | Affects |
|---|---|---|---|
| IMP-01 | **Accepted (D-030), moved to SEC-33.** Deactivated agent keys get 401. | `agents.is_active` and the deactivate route exist; B5's checks only name users and tokens. | B5, B19 |
| IMP-02 | **Accepted (D-031), moved to BE-127 and TEST-03.** Hook endpoint authenticates the agent key. | B18 posts with the key; `hook_events` has `user_id`/`agent_id`. | B18, TEST-03 |
| IMP-03 | **Accepted (D-032), now in SEC-20.** 30 writes per minute per agent, `busy` message, built in B17. | BE §12 only. | B17 |
| IMP-04 | **Accepted (D-033), now in BE-70.** Checked after the summary, built in B14. | BE §11 only. | B14 |
| IMP-05 | **Accepted (D-034), now in NFR-01, NFR-05 and EVAL-07.** A real load test is Deferred (D-034). | SPEC §10; BE §14. | Evals, B17 |
| IMP-06 | **Accepted (D-035), now in EVAL-02.** The Evals stage tops the labeled set up to 50. | BE B3 vs §14. | Evals |
| IMP-07 | **Deferred (D-036).** LLM-as-judge explanation score in LangSmith; EVAL-07 trimmed. | SPEC §14; BE §14. | — |
| IMP-08 | **Accepted (D-037), now in OBS-06 and OBS-08.** One event per status change; decisions record the approver. | BE §5, B14; SPEC §13. | B4, B14, B15 |
| IMP-09 | **Accepted (D-038), now in BE-21.** Three tools create actions; `get_action_status` does not. | BE B6. | B6, B19, F8, F9 |
| IMP-10 | **Accepted (D-039), now in BE-20 and BE-100.** Truncate to 500 with a notice. | BE B6, §12. | B6 |
| IMP-11 | **Accepted (D-040), now in BE-25, BE-28 and BE-44.** New "Unparseable SQL" block rule. | BE B8. | B8, B9 |
| IMP-12 | **Deferred (D-041).** Server-side logout; logout clears the cookie only (SEC-30). | FE §11; BE has no logout route. | — |
| IMP-13 | **Accepted (D-042), now in FE-15.** Derived groups shown with the impact numbers. | BE §6 vs FE §7. | F7 |
| IMP-14 | **Deferred (D-043).** Turning judge overrides into eval cases; the data is already stored. | SPEC §15. | — |
| IMP-15 | **Deferred (D-044).** Alerts. | SPEC §15; BE §13, §17. | — |
| IMP-16 | **Accepted (D-045), now in EVAL-10.** PRs fail if any incident replay is missed. A false-block-rate threshold is Deferred (D-045) until first measured. | SPEC §14, §16; BE §14. | B2, Evals |
| IMP-17 | **Accepted (D-046), now in FE-09.** Users sits under Settings. | FE §6, §7, F4. | F4, F11 |
| IMP-18 | **Accepted (D-047), now in ARCH-21.** Ruff TID251 bans `demo_company` imports. | CL. | B1 |
| IMP-19 | **Accepted (D-048), now in BE-84.** One retention setting; audit events kept. | BE §5. | B15 |
| IMP-20 | **Accepted (D-049), now in BE-121, DW-F04, DW-F06 and DW-F10.** Real retrieval in the seed; `--add-pending`. | FE F10; BE B20. | B20, F4, F6, F10 |
| IMP-21 | **Accepted (D-050), now in SEC-26.** Origin check built in F3. | FE F3, §11, §12. | F3 |
| IMP-22 | **Accepted (D-051), now in BE-112 and FE-22.** "Today" is the UTC day, labelled. The viewer's local day is Deferred (D-051). | BE §13; FE F9. | B19, F9 |
| IMP-23 | **Accepted (D-052), now in BE-07 and EVAL-03.** `ANCHOR_DATE` = 2026-10-01. | BE B3. | B3 |
| IMP-24 | **Accepted (D-053), now in BE-111 and GATE-03.** Approve and reject pulled forward to after B15 (PROGRESS updated). | BE B17, B19; FE §14; PROGRESS. | B15-B17, B19 |
| IMP-25 | **Accepted (D-054), now in BE-11, DW-B03 and OPS-11.** Owner loads `blastproof-company` at deploy time. | BE B3; CL "Safety while building". | B3, Deploy |
| IMP-26 | **Accepted (D-055), now in SEC-34 and BE-100.** MCP action IDs scoped to the agent's owner; `not_found` template. | CL; BE §6, B17. | B17 |

## Notes on sources

- **Superseded text:** D-007 mentions `fast_lane_tables`; D-024 removed it. This file follows D-024.
- **Described but deferred:** B11 describes `QdrantStore` and B13 describes `JevJudge` and
  `JUDGE_SHADOW_PROVIDER`, but BE §17 defers Qdrant, Jev and shadow judging. Only their interfaces
  are MVP (ARCH-18), so they have no rows here. For the same reason DW-B11 leaves out B11's second
  "Done when" clause (switching `VECTOR_STORE` to Qdrant gives the same results).
- **Spec vs plans:** the spec's tech stack and §15-§16 list OpenTelemetry, Prometheus, Grafana and
  Terraform; the backend plan defers all of them to phase 2. The spec's "a few hundred" labeled
  actions become 50 for the MVP (BE §14). Spec §14's LLM-as-judge explanation score (D-036), §15's
  alerts (D-044) and feedback loop (D-043), and §16's eval pass/fail thresholds beyond the 100%
  incident gate (D-045) are deferred by decision; the plans win.
- **Left out on purpose:** Phase-2 items in BE §17 and FE §15 deferred lists; the
  "How we work" and "Safety while building" rules in CLAUDE.md, which govern the build process
  rather than the product; the outside-setup checklists (BE §2, FE §2), which are tracked in
  PROGRESS.md Stage 0.
