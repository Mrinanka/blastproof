# Progress

> **Test speed (D-190, 2026-10-27):** `uv run pytest --integration --skip-slow` runs 2,078 tests in 3 min 40 s (leaves out 94 seed-heavy ones); `--integration` alone runs all 2,173 (about 9 min) and only CI runs it (D-191: never the full suite locally).
>
> **Test speed (D-179, done 2026-10-26):** local `uv run pytest` runs only the fast unit tests (about 40 s); `uv run pytest --integration` runs all of them, and CI always does. See `docs/test-speed.md` (includes how to reverse it).

## Current focus
F1 (frontend scaffold) is built on `feat/f1` (D-215 to D-220). Step 17 (wire up `query_database`) is built on `feat/step-16` (D-206 to D-214). F2 (typed API client) is built on `feat/f1` (D-221 to D-223). Next unticked: F3 (the checklist puts F1 to F5 before the Stage 3 gate; step 18, the Claude Code hook, comes later).

The pull-forward of GET /v1/actions and GET /v1/actions/{id} is done on `feat/actions-read-api` (D-168 to D-172). The LangGraph interrupt and resume spike is done on `spike/langgraph-interrupt` (D-173 to D-178). Step 15 (workflow runner) is done on `feat/step-15` (D-187 to D-189). The approve/reject routes (a pull-forward, D-053) are done on `feat/approve-reject` (D-192 to D-194); `seed-demo-actions` is done on the same branch (D-195 to D-198); Step 16 (commit) is done on `feat/step-16` (D-199 to D-205); next: step 17 (wire up `query_database`). Step 14 (LangGraph workflow) is done on `feat/step-14` (D-180 to D-186). The Stage 2 gate audit is done on `fix/gate-audit-stage-2` (D-165 to D-167). Step 13 (Risk judge, pluggable) is done on `feat/step-13` (decisions D-158 to D-164; its manual read of real Gemini wording is deferred, see Known issues). Step 12 (Impact engine) is done on `feat/step-12`; its manual read of real Gemini wording is deferred (Gemini was overloaded, see Known issues). Decisions D-150 to D-157.
Step 11 (Knowledge and RAG) is done on `feat/step-11`. Retrieval takes the top 10 similar chunks (D-148).
Step 10 (Neon client and shadow executor) is done on `feat/step-9`, including the manual Neon run (D-142, D-143). Next: step 11 (Knowledge and RAG). The Neon branching spike is done (D-133, D-134). Step 9 (policy rules) is done on `feat/step-9`
(decisions D-128 to D-132). Step 8 (classifier) is committed on `feat/step-8`. Step 7 (remove direct access) is merged
(PR #9). The Claude Code hook spike is done on `spike/claude-hook`; step 6 (MCP server) is done on `feat/step-6`.

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
Spike: Claude Code hook done 2026-10-25 on `spike/claude-hook`, decisions D-113 to D-116.

### Gate audit (Stage 1), fixes applied 2026-10-25, D-121
- Baseline: 488 tests passed, 1 skipped; ruff, format and `alembic check` clean; CI green for PRs #1 to #9.
- Fixed: Docker denied in the workspace (F1), `disableClaudeAiConnectors` (F2), `NEON_API_KEY` scan (F3), `check_read` refuses
  SQL-running and file-reading functions (F4). Now 495 pass. Left: F5 (shell writes to `settings.local.json`, step 18 input).
- Live, after the fixes: `verify` PASS (environment, canary, real `.env` denied); `claude -p` from the workspace answered 459 with
  `list_tables`, `describe_table`, `query_database` recorded as `executed`; `docker ps` was denied; the only server the agent sees is `blastproof`.
  GATE-01 is ticked below.
- Run `workspace.py create --force` again, or edit `settings.json` by hand, on workspaces made before this change.

### Spike: Neon branching, done 2026-10-25
- Throwaway code and findings in `spikes/`: `neon_branching.py` (`probe`, `latency`, `cleanup`) and `neon_branching.md`. Dev project only;
  no `backend/app` change; 848 unit tests and ruff still pass; no `spike-*` branches left.
- Findings: no `connection_uris` (build the DSN from the endpoint host); branch usable p50 5.0 s / p95 5.2 s, whole preview about 14 s;
  `expires_at` accepted on create; branch cap 10 (422 `BRANCHES_LIMIT_EXCEEDED`); second DELETE returns 204; two parallel creates fine.
- Plan edits: backend plan step 10, SPEC §10 and NFR-03 (target under 20 s). Requirements ticked: SPIKE-03.
- Not tested: `expires_at` auto-delete timing, pooled endpoint under concurrent clients (pooling was off), 429 limits.

### Spike: LangGraph interrupt and resume, done 2026-10-26, D-173 to D-178
- Throwaway code and findings in `spikes/`: `langgraph_interrupt.py` (`probe`, `migrate`, `cleanup`) and `langgraph_interrupt.md` (answers, plan edits, real output). Local Docker Postgres only; no `backend/app` change, no new dependency; scratch databases dropped.
- Findings: `setup()` from a migration does not hang but is not atomic, so the migration holds the library DDL itself (D-173); no `ALTER DEFAULT PRIVILEGES` (D-174); without `autocommit` the graph stores nothing, and a pool needs `check_connection` to survive killed connections (D-175); `durability="sync"` (D-176); LangGraph does not stop a double resume, the SQL claim does (D-177); plain state types or registered models (D-178). The interrupt re-runs only its own node; restart and crash recovery work as the plan assumed.
- Plan edits: backend plan section 5 and step 14, BE-78 wording. Requirements ticked: SPIKE-04. Step 14 must ask before pinning `psycopg-pool` (new direct dependency).
- Not tested: a migration holding a snapshot before `setup()`, pgbouncer or Neon's pooled endpoint, `LANGGRAPH_AES_KEY`, `interrupt()` inside try/except.
- Also fixed here: `PgVectorStore.search` checks the vector length itself (the flaky pgvector dimension test from step 11; known issue removed), and `test_the_log_names_the_field_and_keys_and_holds_no_value` configures logging itself (it failed when run alone or early).
- Branched from `feat/actions-read-api`, not `develop`: `develop` lacks the docs from steps 6 to 13 and the pull-forward that these edits build on.

### seed-demo-actions (Stage 3 pull-forward), done 2026-10-27, D-195 to D-198
- New: `app/services/demo_seed.py` (scenario table, accounts, the run), `app/services/scripted_models.py` (`ScriptedModelFactory`), `get_agent_by_owner_and_name` in `app/db/agents.py`, the `seed-demo-actions [--add-pending] [--password-stdin]` command in `app/cli.py` (Selector loop on Windows). No migration, setting or dependency.
- What it makes: two demo users (members) with one agent each and, for each, actions in awaiting_review, rejected, expired, blocked, committed (human and auto) and failed, all through the real graph, runner and `ShadowRunner` on `FakeNeonClient`; real retrieval; scripted models; stub commit. `approved` and `drifted` are not seeded (D-195). A run takes about 70 s.
- Tests: `test_scripted_models.py` (9) and `test_cli_seed_demo.py` (7) need no Docker; `test_seed_demo.py` (26, slow tier) seeds once per module and checks every status for both users, the state-machine history, the pause at `human_review` with a checkpoint, `source: llm`, impact equal to the preview rows, company_main unchanged, no `shadow_` database left, no password or key in logs, approve/reject through the API with a 404 for the other user, re-run, `--add-pending`, and each refusal.
- Mutation checks: removing each of 5 guards turned a test red (production refusal, profile check, `mark_recovered`, the expected-status check, the password-only-on-create rule; the last first survived and its test now passes a password on the re-run).
- By hand 2026-10-27: ran against local `blastproof` with the local values from `.env.example` in the process environment: 14 actions, every one in its expected status, 24 knowledge chunks retrieved. This left users `demo-user-1@example.com` and `demo-user-2@example.com` (password given on stdin) and 14 actions in the local dev database. Not done by hand: the `/docs` walk-through (DW-B19, first half: log in as demo user 1, approve one pending action, reject another, demo user 2 gets 404); the automated API test stands in until you run it.
- Requirements ticked: BE-121. Left open: DW-B19 (first half by hand), DW-F04, DW-F06, DW-F10, TEST-09 (they use the data in the dashboard steps).
- Step 16 hand-over: re-run the seed and add a `failed: drifted` action; the commit's profile guard ("refuses any action whose system's profile does not match") is step 16's, not the seed's.

### F2, built 2026-10-27, D-221 to D-223
- New: `src/lib/api/schema.d.ts` (generated from the local backend), `src/lib/api/client.ts`, `src/lib/query-client.ts`, `src/app/providers.tsx` (Query provider and devtools in the root layout), `gen:api` script, schema ignored by ESLint; tests `api-client.test.ts` (type tests incl. `@ts-expect-error` cases, base path, no env/token, array params), `query-client.test.ts`, `query-polling.test.ts`. 4 pins (D-221).
- Tests: 40 Vitest; lint, format, typecheck and `npm run build` clean. Mutation checks: each turned a test red (background polling on, focus refetch off, server branch deleted, browser client not shared, base URL changed, `action_id` renamed in the schema: typecheck fails). Backend untouched, so backend tests were not re-run.
- Not done: the editor hover (DW-F02, by hand: hover `api.GET("/v1/actions/{action_id}")`); a second `gen:api` giving no diff and the `.next/static` devtools check were not run. CI contract check waits for step 20 (OPS-05); regenerate `schema.d.ts` by hand whenever a route changes.
- Requirements ticked: FE-02, FE-03, CONV-13. CONV-12, CONV-14, CONV-16, OPS-05 and DW-F02 stay open.

### F1, built 2026-10-27, D-215 to D-218
- New: `frontend/` (Next.js 16.4.3, React 19.3.1, Tailwind 4.3.3, shadcn 4.22.1 on Radix with 12 components plus `label`, `separator`, `sheet`, `tooltip`), `next.config.ts` headers, Vitest tests in `frontend/tests/`, `.github/workflows/frontend.yml`, `backend/tests/test_frontend_workflow.py`. Edited shadcn's `use-mobile.ts` (lint rule).
- Tests: 32 Vitest; 10 backend workflow guards; `uv run pytest` 1,767 passed; frontend lint, format, typecheck, build clean; ruff clean.
- By hand: `npm run dev` gave 200 with the starter page and the three headers; `npm start` also; the HTML holds one inline `self.__next_f.push` script (D-028's premise holds).
- Mutation checks: removing each of 11 guards turned a test red (strict, exact pin, X-Frame-Options, Referrer-Policy, env ignore exception, `.nvmrc` major, `engines`, a script, a component, `localStorage` in src, a public env name; plus 3 in the workflow test: a step, a SHA pin, a secret). SRI build (D-219): 5 of 7 external scripts get `integrity`, 3 inline scripts do not, so `script-src 'self'` alone would block them.
- Found by the first PR run (D-220): typecheck needed `.next/types` from an earlier build; fixed with `next typegen`. Not done: the `frontend` job going green on the PR.
- Requirements ticked: FE-01, DW-F01, CONV-11, SEC-31. SEC-28 stays open until CI/header check on the PR; OPS-03 needs the green PR run.
- Known issue: Dependabot has no npm entry yet (owner's choice); add it in the security pass.

### Step 17, built 2026-10-27, D-206 to D-214
- New: `app/services/agent_outcomes.py` (`AgentView`, `outcome_for`, `over_budget`). Changed: `agent_messages.py` (all write templates, `in_progress`, `KEEP_POLLING`; `not_supported` gone), `agent_calls.py` (`query_database` classifies then reads or writes; `action_status` is the long poll), `db/actions.py` (`create_action` takes `retry_of` and the received-event data; `agent_view`, `recent_writes`, `lock_agent_writes`), `mcp/server.py` (`retry_of`, runner and clock getters), `schemas/mcp.py`, `main.py`, `reads.py` (`check_read` removed). No migration, setting or dependency.
- Tests: about 100 new in `test_agent_outcomes.py` (no Docker), `test_mcp_writes.py`, `test_mcp_rate_limit.py`, `test_mcp_status_poll.py`, `test_query_database_done_when.py` and the slow `test_mcp_concurrency.py` (5 writes plus 5 reads, never more than 2 previews at once, `company_main` unchanged). Local: `uv run pytest` 1,757 passed; `--integration --skip-slow` 2,280 passed; the concurrency file passes by name; ruff, format and `alembic check` clean. Existing tests changed: D-214.
- Mutation checks: removing each of 11 guards (lock, kind filter, 60 s window, cap boundary, `executed` terminal, `retry_of` visibility, accepting check, start-race fail, admin widening off, no-runner refusal) turned a test red. Running `pinned_sql` instead of the raw SQL for reads cannot be seen in results (the pin changes `now()` by milliseconds), so no test fails for it.
- By hand 2026-10-27 (DW-B17, `claude -p` from the demo workspace, uvicorn `--reload`, Neon dev profile): "clean up inactive users" first returned `previewing` with the `in_progress` text, then `awaiting_review` (with the `ai_unavailable` text plus the polling sentence: Gemini was unavailable, so it was held). Rejected through `POST /v1/actions/{id}/reject` as a new local admin `dw-b17-admin@example.com` (left in the local dev database); the agent's waiting `get_action_status` returned the reviewer's name and note in the same second, then sent a narrower DELETE with `retry_of`, which was held; `GET /v1/actions/{id}` showed `retried_by` and `retry_of`. Found: a note ending in a full stop gave `..` (fixed, `rejected` strips it). Not left clean: the dev uvicorn on :8000 may still be running.
- Requirements ticked: BE-18, BE-98, BE-99, BE-100, SEC-20, SEC-34, NFR-05, ARCH-01, ARCH-10, OBS-06, TEST-08. DW-B17 ticked.

### Step 16, done 2026-10-27, D-199 to D-205
- New: `app/services/committer.py` (`CompanyCommitter`), `app/services/commit_sql.py` (pure statement, diff and drift logic), `app/db/before_images.py`. Changed: `commit.py` (outcome and three exceptions), `company_schema.py` (`row_hash_sql`), `shadow.py` (`cascade_count_sql` public), `company_db.py` (`writer_url_for`, `connect_writer_for`), `workflow/graph.py` (commit node, `route_after_commit`, `after_drift`, `MAX_DRIFTS`), `workflow/state.py` (`drifts`, `repreview`), `workflow/runner.py` (reconcile `committing`, `_running`), `main.py`. No migration, setting or dependency.
- Tests: about 190 new in 8 files (`test_commit_{sql,diff,outcomes,routing}.py` need no Docker; `test_commit_{done_when,guards,runner}.py` and the slow `test_commit_replit.py` need Docker). Local run `--integration --skip-slow`: 2,261 passed, 1 skipped; the slow Replit file passes by name; ruff, format and `alembic check` clean.
- Mutation checks: removing each of 17 guards turned a test red (marker conflict, hash compare, cascade compare, both key-set directions, no-op filter, profile no-fallback, unknown vs failed, `already_applied` keeps images, drift forces review, second drift fails, re-raise of unknown, post-commit write never failed, shared clone lock, images replaced, approver cleared, sweep skips running).
- By hand 2026-10-27: in `/docs` as a local admin, approved a pending seeded `UPDATE users SET plan = 'free' WHERE id = 105`; the action reached `committed`. Not looked at by hand: the `applied_actions` row and the event's `stub: false`. Company data was then re-seeded. The seed still commits through `StubCommitter` and has no `failed: drifted` action (follow-up, owner's choice).
- Requirements ticked: ARCH-03, ARCH-04, BE-49, BE-50, BE-80, BE-85, BE-87 to BE-97, SEC-16, DW-B16, MVP-B07, MVP-B08, NFR-04. Left open: ARCH-01 (agent messages, step 17), OBS-06 (messages and full timeline check, step 17), BE-86 (deploy).

### Approve and reject routes (Stage 3 pull-forward), done 2026-10-27, D-192 to D-194
- New: `POST /v1/actions/{id}/approve` and `/reject` in `app/api/actions.py` (with `get_runner`), `app/services/decisions.py`, `RejectRequest` and `DecisionResponse` in `app/schemas/actions.py`. Changed: `StatusView` gained `decided_at` (`app/db/actions.py`). No migration, setting or dependency.
- Tests: `test_decisions_units.py` (25, no Docker), `test_decisions_api.py` (20, on the real graph: done-when for approve and reject, two approves at once, approve against reject, other user, admin, 401s, expired, not in review, 503s, bad bodies, no reason in logs, OpenAPI), 8 new matrix rows in `test_authorization_matrix.py` (the matrix now uses its own client with a recovered runner; the existing rows are unchanged), two models added to the no-sql/email check in `test_actions_api.py`.
- Mutation checks: removing each of 8 guards turned a test red (runner `None` check, the 503 mapping, the strip, the minimum length, the 404 body, the 409 naming the decider, `is_admin` passed to `Decider`, the reason kept out of logs).
- Not done by hand: no paused write can be created locally until `seed-demo-actions`, so the `/docs` walk-through (DW-B19, first half) runs right after it. The automated done-when stands in.
- Requirements ticked: BE-111, CONV-08. Left open: CONV-07 (503 and 409 done; 403 and 429 elsewhere), TEST-03 (matrix grows with step 19), BE-16, DW-B17, GATE-03, DW-B19.

### Step 15, done 2026-10-27, D-187 to D-189
- New: `app/workflow/runner.py` (`WorkflowRunner`: `start`, `wait_for_decision`, `resume`, `recover`, `sweep`, `maintain`, `drain`; `WorkflowUnavailableError`, `ActionNotFoundError`, `AlreadyDecidedError`, `Decider`, `StatusView`), `app/db/retention.py`. Changed: `app/db/actions.py` (`status_view`, `claim_decision`, `expire_if_overdue`, `overdue_reviews`, `stuck_actions`), `main.py` (`app.state.runner`, maintenance task outside `APP_ENV=test`, drain before the pool closes), `api/ready.py` (`checkpointer` and `workflow` probes when a checkpointer pool exists), `tests/workflow_helpers.py`, `tests/conftest.py` (`fresh_wf_env`). No migration, no new dependency, no new `.env` name.
- Tests: 44 new in `test_runner_{done_when,resume,recovery,sweep,units}.py` and `test_ready_checkpointer.py`; full run `--integration` 2,169 passed, 1 skipped (8 min 43 s); ruff, format and `alembic check` clean. One existing test file changed: `test_workflow_fail_closed.py` (an autouse fixture forgets the cached graph logger).
- Mutation checks: removing each of 14 guards turned a test red (claim status, claim expiry, claim visibility, recovery lock, recovery `next` check, task set, resume refusal, retention cutoff, stuck-time source, event in the claim, drain refusal, `committing` untouched, audit events kept, sweep expiry). Claim visibility first survived (`resume` checks it twice) and got its own test.
- Found: the first full run had 17 failures in `test_workflow_fail_closed.py` (clean `develop` passes). Cause: `configure_logging` uses `cache_logger_on_first_use`, so a module logger first used under an earlier configuration keeps that processor list and `structlog.testing.capture_logs` misses it; which tests run before decides it. Fixed in the test (the cached binding is forgotten), not the app.
- By hand 2026-10-27: uvicorn `--reload` with `CHECKPOINTER_DATABASE_URL` set in the process environment (local value from `.env.example`; `backend/.env` still has it empty): logs show `recovery_done` and `sweep_done`; `/ready` lists `checkpointer: ok` and `workflow: ok`. Not done by hand: the drain log on Ctrl+C (covered by `test_runner_units.py`; check it once in a real terminal).
- Requirements ticked: BE-81, BE-82, BE-83, BE-84, OBS-08, DW-B15. Left open: BE-85 and BE-86 (the `committing` reconcile is step 16; `kill_timeout` and the uvicorn flag are Deploy), BE-16 (graph half done; the approve and reject routes use it), SEC-16 (retention half done), CONV-08 (the route returns the 409), NFR-04 and OBS-06 (step 16).

### Step 14, done 2026-10-27, D-180 to D-186
- New: `app/workflow/{state,graph,checkpointer}.py`, `app/services/commit.py` (`Committer`, `StubCommitter`), migration `0003_langgraph_checkpoints.py`. Changed: `config.py` (`CHECKPOINTER_DATABASE_URL`), `main.py` (the closed pool and deps in `create_app`; saver, graph and pool opening in the lifespan; production requires the URL), `db/enums.py` (`ALLOWED_TRANSITIONS`), `db/actions.py` (transition guard, `add_event`, optional `decision`, `policy_reason`, `decided_at`, `expires_at` on `set_status`; events stamped with `clock_timestamp()`), `db/judge.py` (a skipped judge adds no cost), `classifier.py` (`insert_rows`), `judge.py` (`knowledge_unavailable`), `migrations/env.py` (`include_object`), `pyproject.toml` and `uv.lock` (`psycopg-pool==3.3.3`), `.env.example`. Empty `CHECKPOINTER_DATABASE_URL` appended to `backend/.env`.
- The graph: `classify -> pre_rules -> block | fast_lane | shadow -> post_rules -> block | retrieve -> impact -> judge -> decide -> approve | request_review -> human_review -> commit | block`, 14 nodes (backend plan step 14, "As built"). `run_write` and `resume_write` hold the call options (`durability="sync"`, `version="v2"`, resume only at `human_review`); step 15 wraps them.
- Tests: about 150 new in 12 files (`test_workflow_{state,routing,calls,done_when,resume,fail_closed,end_to_end}.py`, `test_action_transitions.py`, `test_checkpoint_{migration,privileges}.py`, `test_checkpointer_config.py`, `workflow_helpers.py`), plus rows in `test_classifier.py`; full run 2,125 passed, 1 skipped (8 min 31 s). Four existing tests changed as necessary consequences: `test_migrations.py` (expects the four checkpoint tables) and the "complete production config" in `test_production_neon_settings.py`, `test_db_session.py` (also a new missing-URL case), `test_model_settings.py`. `conftest.py` gained `wf_env` and, on Windows only, `pytest_asyncio_loop_factories` (psycopg async needs the Selector loop).
- Mutation checks: removing each of 16 guards turned a test red (the fail-closed wrapper, the `GraphBubbleUp` re-raise, the cost-cap NULL rule, the knowledge-failed skip, the commit status re-check, `durability="sync"`, `autocommit` twice, strict serde, the role check, the transition guard, `include_object`, pass-2 block skipping the judge, `is True` on the approval, a skipped judge adding no cost, `pinned_sql` kept out of the state). Strict serde first survived (a function-local dataclass can never be revived) and got a module-level one. Two real bugs found by tests while building: `{"approved": 1} == {"approved": True}` (now `is True`), and a skipped judge turning an unknown cost into 0 (D-185).
- Done-when (DW-B14), automated: one write per branch through the real graph on the real `AsyncPostgresSaver`: fast lane (no shadow, judge or LLM call, template report), allow, review then approve, review then reject, block in pass 1 (no preview) and in pass 2 (template summary, no retrieval, LLM or judge), a judge that raises, hangs or answers garbage, the cost cap (a known cost over a cap of 0, and an unknown cost), a retrieval failure; for each the status history and audit events, a checkpoint row, and what was and was not called. The status after a resume is `approved` with exactly one `awaiting_review` event. A second pool and graph resume a paused thread; a resume whose row is expired, rejected or unclaimed commits nothing and writes `fail_closed` / `resume_mismatch`. An exception in each wrapped node, `ShadowBusyError`, `ShadowFailedError`, `PolicyFactsError` and a read all end as `failed` with decision `block` and a log line, and no log line, checkpoint or prompt holds a planted email. End to end on the real `ShadowRunner` and `FakeNeonClient`: the Replit delete reaches review with the exact `incidents.yaml` counts and 245,677 `preview_rows`, then commits through the stub with `company_main` unchanged.
- By hand 2026-10-27: `alembic upgrade head` on local `blastproof` created the four tables owned by `blastproof_owner`; `\dp checkpoint*` shows the app with `arwd` on three and `r` on `checkpoint_migrations`; `alembic check` is clean; uvicorn starts with and without `CHECKPOINTER_DATABASE_URL` and `/ready` is ok (see Known issues for Windows without `--reload`).
- Requirements ticked: BE-39, BE-70, BE-72, BE-76 (state narrower than its list, D-180), BE-77, BE-78, BE-79, ARCH-02, ARCH-08, SEC-09, OBS-03, OBS-05, CONV-10, DW-B14. Left open (graph half done): BE-16 (step 15 sets `decided_by`/`expired`), BE-80 and ARCH-01 (the real commit, step 16), ARCH-10 (a read creating no thread is wired in step 17), OBS-01 (a LangSmith trace per run: check by hand once tracing is on), OBS-02 (stage durations in logs), OBS-06 (steps 15 and 16 add events), OBS-07 (eval check).

### Actions read routes (Stage 3 pull-forward), done 2026-10-26, D-168 to D-172
- New: `app/api/actions.py`, `app/schemas/actions.py`, `app/services/actions.py`; `app/db/actions.py` gained `list_visible_actions` and `get_action_detail_rows` (both start from `visible_actions`); `classifier.py` gained public `display_sql` and `display_in_worker`; `main.py` includes the router. No migration, no new dependency.
- Tests: 113 new in `test_actions_api.py`, `test_actions_views.py`, `test_authorization_matrix.py` (+ `action_helpers.py`); full run 1,975 passed, 1 skipped (7 min 24 s); ruff, format and `alembic check` clean. Mutation checks: removing each of 11 guards turned a test red (owner filter, admin widening, both intent masks, first_line mask, 50 cap, can_decide expiry, timeline whitelist, malformed-id 404, retried_by visibility, total ignoring filters; the last first survived and got its own test).
- By hand 2026-10-26: uvicorn on :8000 (`/ready` all ok), logged in as a new local admin `check-admin@example.com`, `GET /v1/actions` and `GET /v1/actions/{id}` returned the step 6 read actions in the full shape, a random and a malformed ID gave the same 404. Not checked by hand: a second member getting 404 on another user's action (the automated tests cover it). That run left users `check-admin@example.com` (and possibly `check-member@example.com`) in the local dev database.
- Requirements ticked: BE-109, BE-110, ARCH-14, ARCH-15. Left open: TEST-03 (matrix started, step 19 adds rows), CONV-03 to CONV-07, SEC-14, ARCH-17, OPS-05.

### Gate audit (Stage 2), done 2026-10-26, D-165 to D-167
- Audit (Prompt G, branch `fix/gate-audit-stage-2`): 1,814 passed before the fixes; ruff, format and `alembic check` clean. Gate check: the shadow done-when, all 16 measured scenarios and the row-hash test pass, and plain `SELECT count(*)` queries on local `company_main` as `blastproof_reader` (no shadow code) match `incidents.yaml` exactly (users 48,213 = free 48,201 + pro 9 + enterprise 3; orders 48,500; order_items 121,364; subscriptions 1,476; payments 4,382; support_tickets 11,647; customer_notes 5,713; invoices set to NULL 4,382). Every users delete retrieves the business rules. `evals.run` does not exist yet (Evals stage).
- Fixed: bare model names refused, `SUMMARY_MODEL`, `JUDGE_MODEL` and `LLM_PRICES` keys must be `provider:model` (finding 1, ARCH-19); production refuses to start on the code's default model and price, dev keeps them (finding 2, D-165); a Neon create whose answer was lost deletes its branch by exact name (finding 3, D-166); BE-31, BE-34, BE-42, BE-57 reworded to match the code (finding 5); `ShadowRunner.timeout_seconds` has no default of its own (finding 7); new test pins the business-rules rank at 7 or better for users deletes that reach retrieval (measured 1 to 6).
- Tests: 47 new (`test_model_settings.py`, cases in `test_neon_api_client.py`, `test_shadow_units.py`, `test_knowledge_done_when.py`); full run 1,861 passed, 1 skipped (6 min 41 s); ruff, format and `alembic check` clean. Mutation checks: removing each of 14 guards turned a test red (production check, empty-means-unset, model validator, price-key validator, the pattern, timeout default, the three name lookups, exact name, default branch, quiet lookup, rank margin, pass-1 filter). Two existing tests gained the model settings in their "complete production config" (`test_production_neon_settings.py`, `test_db_session.py`), as step 10 did for the Neon settings; nothing else changed in them.
- Finding 6, decided by Claude at the owner's request (D-167): the crash log line holds the exception class and up to 15 `file:line function` frames, never the message, traceback text or locals. `test_request_id.py:149` changed from asserting the message is logged to asserting it is not (the only edit to that test); 2 new tests; 3 more mutation checks red. Not fixed: finding 4 (`expires_at` on the deployed project) is a deploy check (Known issues).
- Requirements ticked: GATE-02, ARCH-19.

### Step 13, done 2026-10-26 (manual read of real wording deferred)
- New: `app/services/judge.py` (`Judge` protocol, `JudgeContext`, `JudgeVerdict`, `JudgeResult`, `LLMJudge`, `JUDGE_PROVIDERS`, `build_judge`, `assess_safely`, `injection_signals`, `decide`), `app/db/judge.py` (`save_judge`). Changed: `config.py` (`JUDGE_PROVIDER`, `JUDGE_MODEL`, `JUDGE_MODEL_OPTIONS`), `llm.py` (`ModelFactory`, `usage_tokens` moved here), `impact.py` (`data_block` public), `db/impact.py` (`add_usage` public), `agent_messages.py` (`ai_unavailable`), `main.py` (`app.state.judge`), `cli.py` (`judge-report`), `.env.example`. No new dependency. Empty `JUDGE_PROVIDER`, `JUDGE_MODEL`, `JUDGE_MODEL_OPTIONS` appended to `backend/.env`.
- Tests: about 330 new in 9 files (`test_judge_{decide,llm,safely,prompt,injection,config,scenarios,done_when}.py`, `judge_helpers.py`); full run 1,814 passed, 1 skipped (6 min 42 s); ruff check and format clean. Two existing tests changed: `test_impact_config.py` (the judge model needs a price too) and `test_agent_messages.py` (new template). Mutation checks: removing each of 29 guards turned a test red (block short-circuit, strictest wins, confidence rule, failure to review, `ai_unavailable`, injection to review, each of the 4 scanned fields, NFKC, format-character strip, whitespace collapse, reason masking, number check, withheld reason, rule numbers, shape check, both timeouts, price check, catching in `assess_safely`, consistency check, `sql_display` only, intent masking, impact report as untrusted, `<` escaping, tokens kept on parse error, `not_configured` cost). Whitespace collapse first survived and got its own test.
- Done-when (DW-B13), automated: a fake judge saying block escalates an allowed small insert and a review to block; the three SQL-blocked incidents stay blocked whatever the judge says; the Replit review stays review with a judge saying allow 0.99; a raising, hanging or unparseable judge gives review with `ai_unavailable`; a second registered provider works with no other change; on the real Replit preview with real retrieval the prompt holds the business rules, and the poisoned upload is flagged; `save_judge` on a real `actions` row writes the judge, risk level and adds cost. Sweep over all 16 measured scenarios: an always-allow judge never changes a rule decision, an always-block judge blocks all.
- Requirements ticked: BE-71, BE-73, BE-74, ARCH-09, ARCH-18, SEC-13, DW-B13. Left open: BE-75 (the real model never answered), BE-72 and BE-70 (graph and cost cap, step 14), ARCH-02, ARCH-08, ARCH-19, BE-66, SEC-12, SEC-14, OBS-07, EVAL-05 (graph, real-model or later-step halves).

### Step 12, done 2026-10-26 (manual read of real wording deferred)
- New: `app/services/impact.py` (facts, number guardrail, prompt, template, `ImpactEngine`), `app/services/llm.py` (`build_chat_model`, `LazyModel`, `LlmPrice`, `price_for`, `cost_usd`; step 13 reuses it), `app/db/impact.py` (`save_impact`, adds tokens and cost). Changed: `config.py` (`SUMMARY_MODEL`, `SUMMARY_MODEL_OPTIONS`, `LLM_PRICES`), `policy.py` (a sensitive important column is refused), `main.py` (`app.state.impact`, builds nothing), `cli.py` (`impact-report`, with `--now`), `conftest.py` (autouse: no model keys, tracing off), `.env.example`, CLAUDE.md run command (`--env-file .env`). No new dependency. Empty `SUMMARY_MODEL`, `SUMMARY_MODEL_OPTIONS`, `LLM_PRICES` appended to `backend/.env`.
- Tests: about 200 new in 9 files (`test_impact_{facts,guardrail,prompt,engine,config,template,done_when}.py`, `test_llm_factory.py`, cases in `test_policy_loading.py`); full run 1,488 passed, 1 skipped (6 min 16 s). Mutation checks: removing each of 20 guards turned a test red (comma strip, NFKC, percent with a space, number words, SQL source, knowledge excluded, threshold source, `<` escape, knowledge masking, knowledge cap, timeout, tokens kept on parse and number-check failures, cost accumulation, price required, reserved options, sensitive important column, report masking, shape check, no call without changes). NFKC first needed two extra cases (full-width `％`, superscript digits).
- Done-when, automated: on the real Replit preview, facts equal `incidents.yaml`; the fake model's "Deletes 48,213 users, including 12 paying customers (3 enterprise), and their orders through a cascade." passes and "13 paying" falls back to the template; real retrieval (scan, `schema_docs.md`, a poisoned upload) is masked and wrapped in the prompt; `save_impact` on a real `actions` row writes the report and adds tokens and cost on a second call. The template passes the guardrail for all 16 measured scenarios.
- Manual run tried 2026-10-26 (`impact-report --now 2026-10-01T00:00:00+00:00`, local profile): the preview was right (48,213 users, 12 paying, 245,677 rows, 11 s), but Gemini 3.8 Flash never answered: `503 UNAVAILABLE` ("model is currently experiencing high demand") or `504 DEADLINE_EXCEEDED` at 20, 30 and 60 s, with and without `thinking_level`, even for "Reply ok". The engine did the right thing: template report, `fallback_reason: llm_error`, only `GoogleAPIError` logged, cost unknown. DW-B12's "manual read" of real wording is still open: re-run when Gemini recovers. Local `blastproof` has no knowledge chunks yet (0 retrieved): run `knowledge-scan` and `knowledge-ingest` first.
- Requirements ticked: BE-64, BE-67, BE-68, BE-69, ARCH-06, ARCH-07. Left open: BE-65 and DW-B12 (the real model never answered; only the fake wrote a report), BE-66, ARCH-19, SEC-12 (judge half, step 13), SEC-14, OBS-05 (step 14 calls `save_impact`), BE-70 (step 14).

### Step 11, done 2026-10-26
- New: `app/services/{embeddings,knowledge}.py`, `app/services/vectorstores/` (`base`, `memory`, `pgvector`), `app/db/knowledge.py`, migration `0002`. Changed: `models.py`, `config.py`, `main.py`, `cli.py` (`knowledge-scan`, `knowledge-ingest`, `reindex`), `describe_table` (`notes`). No new dependency. Empty `EMBEDDING_MODEL`, `EMBEDDING_CACHE_DIR`, `VECTOR_STORE` appended to `backend/.env`.
- Tests: about 100 new in 9 files; full run 1,288 passed, 1 xfailed, 1 flaky (`test_cli_auth::test_create_user_then_log_in` failed once under full-suite load, passes alone and without my changes). Real model downloaded to `backend/.cache/fastembed` (CI downloads each run). Mutation checks: removing each of dedupe, stale-ID drop, table filter, replace-delete, lazy load, dimension check, iterative-scan setting turned a test red.
- Done-when: scan plus `schema_docs.md` (11 + 13 chunks) retrieve the business rules for "Clean up inactive users" through similarity, with all Replit tables or only `users`; `knowledge_used` saved on a real action row. Every users-delete scenario also retrieves the rules after raising k from 5 to 10 (D-148). Retrieval takes about 25 to 30 ms once the model is loaded.
- Not done by hand: the manual CLI run against local `blastproof` and `describe_table users` through MCP (covered by tests). Known: the Qdrant clause of Done-when is deferred.

### Step 10, done 2026-10-25
- New: `app/services/company_schema.py` (`ROW_HASH_SQL`, `pk_json_sql`, `read_schema`), `app/services/neon.py` (`NeonClient`, `NeonApiClient`,
  `FakeNeonClient`, `neon_client_for`, `build_neon_clients`, `sweep_stale_shadows`), `app/services/shadow.py` (`ShadowRunner`, `Diff`, `fk_paths`, `mask_row`),
  `app/db/preview.py` (`save_preview`). Changed: `config.py`, `main.py`, `company_db.py` (hash settings, `connect_writer`, `connect_target`, clone lock),
  `api/ready.py` (`neon_api`), `cli.py` (`preview-sql`), `rules.yaml` (`full_name` is sensitive), `.env.example`. No new dependency. Empty names appended to `backend/.env`:
  `LOCAL_COMPANY_WRITER_URL`, `FAKE_NEON_ADMIN_URL`, `NEON_CLIENT` (fill the first two with the local Compose values for the local profile).
- Tests: about 200 new in 15 files (1,190 pass in total, 1 skipped, 5 minutes). Done-when, automated: the Replit delete on `FakeNeonClient` gives exactly the `incidents.yaml` counts, breakdowns, `users.paying = 12`,
  245,677 `preview_rows`, cascade expectations (orders 48,500, order_items 121,364, invoices SET NULL 4,382 ...), `company_main` unchanged, no copy left, two previews in a row
  identical, pass 2 on the measured facts is `review`. All 16 scenarios with `expected_impact` measure equal to their files. A row hashes the same on a copy with other
  time zone and date style as on `company_main`; a raw connection in another zone does not (the test can fail). Removing each of 17 guards in turn turned a test red
  (schema check, table check, hash settings, primary key check, policy columns, delete in `finally`, slot, clone lock, old preview rows, production refusals, `channel_binding`,
  branch cap, `expires_at`, delete after a failed create, FK action filter, masking). One guard (the schema check) first survived and got its own test case.
- Decisions D-135 to D-141. Two places differ from the plan, both because the scenario files or the foreign keys disagreed with it: derived groups and breakdowns use the state
  before the write for updated rows (D-139), and cascade expectations count deleted keys only (D-140).
- Changed existing tests (necessary consequences): `test_ready.py` (message now names `neon_api`, `neon_client="api"` pinned), `test_db_session.py` (production needs the Neon settings),
  `test_policy_loading.py` (`full_name`). Plan 6.6 and 6.11 were taken as approved by "implement the plan".
- Timing, local Docker Desktop: a preview of the 273k-row seed takes 10 to 15 s (the clone is 0.3 s; hashing and diffing are the rest). The full suite now takes about 5 minutes
  (it was about 3). CI will be slower too; watch it.
- Requirements ticked: BE-05, BE-23, BE-42, BE-45, BE-46, BE-48, BE-51 to BE-58, SEC-17, TEST-04. Also ticked after the manual Neon run: BE-47, DW-B10, NFR-03 (relaxed, D-143). Left open: BE-49 and BE-50
  (commit side, step 16), TEST-05 (no test mutates `company_main`; recreate it from `company_template` when one does), ARCH-01/03/06/17, EVAL-05, OBS-05 (steps 12 to 16), OPS-07 (Deploy).
- **Manual Neon run done 2026-10-25** (`blastproof-company-dev`, branches only): counts match the fake's shape (48,299 users, 121,653 order items, 4,648 invoices set to NULL ...), no branch left behind, but the Replit delete took 44 s (49 s before D-142), over the old 20 s target, so NFR-03 was relaxed (D-143: 60 s for writes touching most of a table; shadow timeout is now `SHADOW_TIMEOUT_SECONDS`, default 60). Next lever: do not download every changed row.
- (Original instruction, now done): `uv run python -m app.cli preview-sql --sql "<the Replit delete>" --confirm-project <exact project name>` with `COMPANY_CONNECTION_PROFILE=neon`,
  `NEON_CLIENT=api` and the dev project's key; compare counts with the fake, check the branch is gone, record the time against NFR-03 (done).

### Step 9, done 2026-10-25
- `app/services/policy.py` (`load_policy`, `check_columns`, `evaluate_pass_one`, `evaluate_pass_two`, `Policy.is_sensitive`,
  `PolicyFileError`, `PolicyFactsError`), `backend/policies/rules.yaml` (the 15 rules of backend plan section 9 plus
  "No side-effect functions", D-128), `STATEMENT_TYPES` in the classifier, `policy_file` in `Settings` (`POLICY_FILE`, optional).
  `create_app` loads the file into `app.state.policy`; a bad file stops startup. Pure code with no I/O at evaluation time.
- Tests: `test_policy_loading.py` (75), `test_policy.py` (147), `test_policy_scenarios.py` and `test_policy_pass_two.py` (50,
  the pass-2 file needs Docker). `test_classifier_scenarios.py` and `test_seed_counts.py` now read `rules.yaml` instead of
  their own mirrors. Removing each of 14 guards in turn (duplicate-key loader, closed keys, pass-1 skip, strictest wins,
  positive allow, fast-lane checks, missing facts, exact fraction, cascades, unique names, condition allowlist) turned a test red;
  one test passed for the wrong reason and was fixed.
- Done-when: each rule, the pass-1 skip and strictest-wins have tests; every SQL-decidable incident is blocked in pass 1;
  the Replit delete is `review` (BE-45's rule side) in pass 2. By hand: uvicorn starts with the shipped file and refuses a copy
  with a misspelled key, naming the rule and key.
- Requirements ticked: BE-28, BE-36, BE-37, BE-38, BE-40, BE-41, BE-43, BE-44, SEC-10, SEC-11, ARCH-11, ARCH-12, DW-B09. Left open
  (the rules half is done): BE-39 (graph routing, step 14), BE-42 (the shadow run calls `check_columns`, step 10), BE-45
  (measured diff, step 10), EVAL-05 (steps 10, 12, 13), OBS-07 (steps 13, 14), ARCH-17 and SEC-14 (masking reaches logs, prompts
  and the API in steps 10 to 17).

### Step 8, done 2026-10-25
- `app/core/clock.py` (`Clock`, `SystemClock`, `FrozenClock`), `app/services/classifier.py` (`classify`, pure and
  synchronous; `classify_in_worker`, a 4-thread `classifier-*` pool with a 2 s cap, returns the duration for step 17 to log),
  `app/services/masking.py` (email and phone heuristics, `mask_text`), `app/services/sql_functions.py` (time, volatile,
  side-effect and allowlisted function names). The classifier is standalone: `check_read` and `query_database` are unchanged.
- Returns kind, statement types, `tables`, `target_tables`, `schemas`, `has_where`, `multi_statement`, `transaction_control`,
  `non_deterministic`, `volatile_in_where`, `unordered_limit`, `fast_lane_eligible`, `side_effect_functions`,
  `unknown_functions`, `pinned_sql`, `pinned_values`, `sql_display`. Pinning splices tokenizer spans and re-parses (D-122).
- Tests: about 300 new, 796 pass in total with 1 skipped (Docker up). `test_classifier.py` has 126 table-driven rows plus the async
  wrapper (timeout, crash, clock read once, worker thread); `test_classifier_pinning.py`, `test_masking.py`,
  `test_classifier_scenarios.py` (every SQL scenario in both files against a local mirror of the SQL-only rules),
  `test_clock.py`, and `test_classifier_postgres.py` (pinned literals have the same `pg_typeof` and value on `company-db`).
- Found while testing: the first email regex was cubic on a 20 KB run of letters and hung the suite; rewritten and
  covered by a timing test (D-123). One scenario label was incomplete and was fixed (D-124).
- Done-when: at least 40 table-driven tests pass (126); the classifier-relevant incident scenarios pass; the named
  DW-B08 cases are present. Removing each of 13 guards in turn turned a test red.
- Requirements ticked: BE-25, BE-26, BE-27, BE-29, BE-30, BE-31, BE-32, BE-33, BE-34, BE-35, TEST-06, DW-B08. Left open
  (the classifier half is done): BE-28, ARCH-01, ARCH-03, ARCH-11, ARCH-12, SEC-10 (step 9 blocks), SEC-14 and ARCH-17
  (masking reaches logs, prompts and the API in steps 9 to 17), EVAL-05, OBS-02 (step 17 logs the duration with `action_id`).

### Step 7, done 2026-10-25
- `claude_hook/workspace.py` (`create`, `check`, `verify`; stdlib only) and `claude_hook/README.md`. Workspace at
  `D:/My Workplace/blastproof-demo` (outside the repo): deny rules for the whole repo and credential files,
  `blockReadsOutsideWorkingDirectories`, key only in `.claude/settings.local.json`, `.mcp.json` expands it (D-117 to D-119).
- Tests: 26 new in `test_demo_workspace.py` plus `test_lint_covers_claude_hook`; 487 pass in total. Removing each of 9
  guards turned a test red. CI and CLAUDE.md lint now cover `claude_hook/`.
- Done-when, by hand: `check` exits 0 (folder and shell environment); `verify` PASS on environment, canary and the real
  `backend/.env` (denied). Interactive `/mcp` shows `blastproof` with 4 tools; "how many pro users" gave 459, 7 actions
  recorded for agent `step7-check`. `verify` uses Sonnet for the deny checks (Haiku never attempts the Read).
- Requirements ticked: BE-24, DW-B07. Left open: SEC-18 (step 18), ARCH-05, ARCH-16 (Deploy).
- Accepted: the agent key was pasted into the chat; it unlocks only the local dev data.

### Spike: Claude Code hook, done 2026-10-25
- Throwaway code and findings in `spikes/`: `claude_hook.py` (`probe`, about 6 minutes, 36 checks, decoy tree
  outside the repo, deleted afterwards) and `claude_hook.md` (answers, plan differences, real output).
  No `backend/app` change; 273 unit tests and ruff still pass.
- Findings: timeouts and crashes fail open unless `onFailure: block`; `python` on a missing script exits 2
  (a block); the hook fires for PowerShell and Monitor, `MultiEdit` is gone; a Read deny covers Grep, Glob,
  `cat`, `Get-Content` but not `python -c`; the agent can disable its hook through `settings.local.json`
  unless `Edit(.claude/**)` is denied; hooks stay on in bypass mode; `http` hook headers need
  `allowedEnvVars`.
- Plan edits: backend plan steps 7 and 18, BE-103 and BE-104 wording. Requirements ticked: SPIKE-02.
- Not tested: a nested `claude --settings` from Bash, workspace trust (docs only, a step 7 manual step), an
  `Edit`-only deny on NotebookEdit (blocked earlier by read-before-edit), and the 600 s default timeout.

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
- F2: DW-F02 (hover `api.GET("/v1/actions/{action_id}")` in the editor) is a manual check the owner still has to do. `schema.d.ts` has no CI contract check until step 20's `export-openapi` (OPS-05): regenerate it by hand (`npm run gen:api`, backend running) whenever a route changes. TanStack's default of 3 retries also applies to 4xx until F3 sets no-retry on them. `noUncheckedIndexedAccess` is off (revisit in F12). Devtools are a devDependency (D-221).
- F1: Dependabot has no npm entry yet (owner's choice); add one in the security pass. The `frontend` CI job and the `setup-node` commit hash (from the plan, not yet resolved from GitHub) are verified only by the PR run. ESLint 9.39.5 is marked unsupported by npm (D-217). The CSP for the landing page needs hashes or `'unsafe-inline'` for inline scripts (D-219).
- Step 17: not yet tried on real Neon under load (the concurrency test uses `FakeNeonClient`, which terminates connections on `company_main` while it clones, so a read could be killed mid-clone locally or in CI). Untested paths: the wait raising after a write starts, `retry_of` on a read, and a SQL string over 20 KB through MCP. Two `assert isinstance` lines in `agent_calls.py` and the `UUID | None | bool` return of `_resolve_retry_of` could be tidied. Real Gemini wording for reviews is still unread (steps 12 and 13). The local dev database holds `dw-b17-admin@example.com` and two rejected test actions.
- Step 16: children of the deleted rows are counted but not locked (locking 245k rows would double the cost), so a child re-pointed between the cascade check and the delete can slip through; new children are blocked by the parent row locks. A write the commit cannot check (two targets, a write inside a CTE) is only refused at commit as `commit_unsupported` (D-201); a pass-1 rule could catch it earlier. Before-images hold the target table only (BE-96); ON UPDATE cascade paths are not checked.
- Step 16: `--add-pending` seed actions approved on a dev server now really change local `company_main`; re-seed with `demo_company/seed.py --target local` to reset. The fast lane's commit on Neon pays about 1.5 s to connect, so MVP-B02's 300 ms is not met there. The deployed `blastproof-company` must be PostgreSQL 18 (`RETURNING OLD/NEW`): check at deploy. The commit takes the whole row-key list as one parameter (about 700 KB for the Replit delete): fine locally, unmeasured on Neon.
- Seed: demo actions seeded on a running dev server expire after `REVIEW_TIMEOUT_HOURS` (24 h): re-run, or use `--add-pending`. Approving a seeded action runs `StubCommitter` (company data unchanged) until step 16. The seed needs `CHECKPOINTER_DATABASE_URL`, `LOCAL_COMPANY_WRITER_URL` and `FAKE_NEON_ADMIN_URL` filled in `backend/.env` (or the process environment).
- Approve/reject: approving an action with no graph thread returns 200 `approved`, then it becomes `failed: resume_mismatch` (D-194). Approving on a dev server runs `StubCommitter` (`stub: true`, company data unchanged). Checked in step 17: the installed `Pregel.ainvoke` takes `version="v1"|"v2"`, so `version="v2"` is current (the interrupts page shows `v3` only for `stream_events`).
- Step 15: the runner is wired (approve/reject call `resume`, `query_database` calls `start` and checks `accepting` first, D-206). An `approved` action stuck while the process stays up is not swept (recovery and step 16 own it). Deploy: run uvicorn with `--timeout-graceful-shutdown 10`, because uvicorn waits for open connections before the lifespan shutdown, so a 50 s long poll plus our 45 s drain would pass `kill_timeout = 60`. A reviewer's decision is claimed on the action's `expires_at`, which `request_review` sets from the clock at that moment.
- Step 15: log-capture tests: a module logger first used after `configure_logging` is cached with that configuration, so `capture_logs` can miss it depending on test order. New tests that check log lines should use `caplog` or forget the cached binding as `test_workflow_fail_closed.py` does.
- Step 14: the commit is a stub (`StubCommitter` applies nothing; its `committed` event has `stub: true`). Step 17 must not ship before step 16 replaces it.
- Step 14: on Windows, psycopg's async mode needs the Selector event loop. `uvicorn ... --reload` (the documented command) uses it; plain `uvicorn` uses the Proactor loop, the pool logs "Psycopg cannot use the 'ProactorEventLoop'" and every write run fails closed. The app still starts. Fly runs Linux. `/ready` now checks the checkpointer (step 15).
- Step 14: if Blastproof's own database is down, the fail-closed wrapper cannot record `failed` either: the exception reaches the caller and the action stays mid-flow until step 15's stuck-`previewing` sweep or startup recovery (a `committing` or `approved` action needs the same).
- Step 14: the fast lane writes `rows_before: 0` for its table (nothing is read), so its impact shows no percentage. A shadow failure ends as `failed` (with its code), not `blocked`. `total_ms` is the sum of node durations and leaves out the wait for a reviewer.
- Step 14: `decide` writes `risk_level` only when the judge gave one (a failed or skipped judge leaves it NULL). The `decided` event is written for rule blocks and the fast lane too, by the `block` and `approve` nodes.
- Full suite on 2026-10-26 (after the spike, three runs): 5, a different set, and 15 failures, all in `test_shadow_*`, `test_fake_neon_client`, `test_judge_done_when` and `test_knowledge_done_when`; every one passes when its file is run alone, and the last had `ConnectionDoesNotExistError` from `company-db`. The earlier full runs (1,975 passed) did not show it. Nothing in the changed files touches these tests; suspect load on Docker Desktop (3.7 GB) or the interrupted runs overlapping. Re-run the full suite on a quiet machine before relying on it.
- Actions routes: step 14 must write `matched_rules` as `[{name, outcome, reason, pass}]` with `pass` `pre` or `post` (D-171). A stored shape the views cannot parse is a 500 (D-170). In `from`/`to` query values, write the offset as `Z` or `%2B00:00`. The 404 for unknown routes is still FastAPI's default (step 19).
- Gate audit 2, finding 6 (fixed, D-167): one path still logs exception text: when a response has already started, the middleware re-raises and uvicorn's `uvicorn.error` logger prints the traceback with the message. No route streams a response today (MCP tool errors are caught by the SDK and filtered by `StripMcpDetails`); revisit if a streaming route is added. Logs from other code that call `logger.exception` would also print text; none do in `app/` today.
- Gate audit 2, finding 4 (Deploy): Neon documents `branch.expires_at` as Early Access only. It works on `blastproof-company-dev`; at deploy, run one `preview-sql --confirm-project blastproof-company` (owner-approved) and, if Neon refuses it, make `expires_at` a setting and rely on the sweep.
- Gate audit 2: with `APP_ENV=production`, `SUMMARY_MODEL`, `JUDGE_MODEL` and `LLM_PRICES` must be set in Fly.io secrets or the backend does not start (D-165). The users-delete scenario `delete-users-without-where` ranks the business rules 8th of 10, but pass 1 blocks it before retrieval. A cancelled Neon create now spends up to 10 s looking up its branch by name (D-166), so a preview that hits `SHADOW_TIMEOUT_SECONDS` can take up to 70 s before it fails closed.
- Step 13 (deferred): the manual `judge-report` run on 2026-10-26 got `503` from Gemini 3.8 Flash for both the summary and the judge; the failure path worked for real (review, `ai_unavailable`, cost unknown). Re-run when Gemini answers: `uv run --env-file .env python -m app.cli judge-report --sql "DELETE FROM users WHERE last_login_at < now() - interval '6 months'" --intent "Clean up inactive users" --now 2026-10-01T00:00:00+00:00`, twice; check the verdict, reason, tokens, cost and that no `parse_error` came from the thinking-token cap, then tick BE-75. If thinking truncates answers, raise `LLM_MAX_TOKENS` or add a separate `JUDGE_MAX_TOKENS`.
- Step 13: `close-one-ticket` stays a known false block: the rules review it and the judge can only add caution. Setting `LLM_PRICES` replaces the whole table, so list the summary and the judge model. The injection list misses rewordings (D-160). The judge's cost is added after the cost-cap check (D-033), so worst case is one Flash call over the cap. Step 14 builds `JudgeContext`, calls `assess_safely`, `injection_signals` and `decide`, routes a `RetrievalError`, and uses `JudgeResult.unavailable(..., 'skipped_cost_cap')`; step 17 sends the `ai_unavailable` message; step 19 adds `JudgeView`.
- Step 12 (deferred): DW-B12's manual read of real Gemini wording. Re-run when Gemini 3.8 Flash answers again: `uv run --env-file .env python -m app.cli impact-report --sql "DELETE FROM users WHERE last_login_at < now() - interval '6 months'" --intent "Clean up inactive users" --now 2026-10-01T00:00:00+00:00` (after `knowledge-scan` and `knowledge-ingest --file ../demo_company/schema_docs.md`), run it twice, check the wording, `source: llm`, tokens and cost, then tick BE-65 and DW-B12.
- Step 12: JSON values in `backend/.env` must be single-quoted (`SUMMARY_MODEL_OPTIONS='{"thinking_level": "low"}'`): `uv run --env-file` cannot parse bare JSON and warns "Failed to parse environment file". Model options stay in config, never in code (an empty value means `{}`, so 3.8 Flash would think at `medium`, which counts against `LLM_MAX_TOKENS`).
- Step 12: on 2026-10-26 Gemini 3.8 Flash returned 503 (high demand) or 504 for every call. If that persists, a different `SUMMARY_MODEL` (with its `LLM_PRICES` entry) is a config change only.
- Step 12: Gemini 3.8 Flash's price doubles on 2027-01-01 ($1.50 / $7.50 per million): update `LLM_PRICES` then; until then the cost cap reads the current price (D-152).
- Step 12: an unknown call cost makes the action's total NULL (D-153), but a later call with a known cost restarts the total from 0 (`coalesce`). Step 14 checks the cap in the `judge` node (an unknown cost counts as over); a skipped judge no longer resets an unknown total to 0 (D-185). Revisit if re-previews (step 16) make the `coalesce` restart matter.
- Step 12: the template is not held to the model's shape limits (the Replit template has 9 key points, one per cascaded table). Step 19's view must not assume at most 8.
- Step 12: the test diffs use `SEED_ROWS` (rows per table counted on the local seed, 2026-10-26) in `tests/impact_helpers.py`; a seed change must update them.
- Step 11: the write lock is per process; with several backend processes a lost race can leave chunks without vectors (`reindex` repairs). `KnowledgeService.notes` was not built (`describe_table` queries the db directly). Knowledge text is untrusted: steps 12 and 13 must mask and wrap it (D-149). Step 19 must check upload size before reading the body and define `DELETE /v1/knowledge/{id}`. The CLI and `describe_table` were not run by hand against local `blastproof` (tests cover them). The Qdrant half of Done-when is deferred with `QdrantStore`. CI downloads the 67 MB model each run. `test_cli_auth::test_create_user_then_log_in` failed once under full-suite load (passes alone).
- Step 10: a preview of a write that touches most of a table is slow: the Replit delete (246k rows) took 44 s on Neon (D-142, D-143). Most of it is downloading every changed
  row and computing both sides on a small compute. If typical writes turn out slow, stop downloading all rows (a change to what step 16 reads from `preview_rows`).
- Step 10: each preview of the seed takes 10 to 15 s locally, so the suite is about 5.5 minutes locally (about 3 minutes in CI before this change). To cut it, the Replit preview is
  shared between the done-when tests, and with `CI=true` the scenario test runs one scenario per statement type and decision (5 instead of 16). Locally all 16 still run.
- CI sets `NEON_CLIENT=fake`, so any test that builds a production-like `Settings` must pin `neon_client="api"` (three test files do).
- Step 10: the stale-copy sweep task started in the lifespan and the clients' close on shutdown are not tested directly (only `sweep_stale_shadows` is). `unknown_table` is also the
  reason code for a refused schema or a schema-qualified function such as `pg_catalog.length()`.
- Step 10: `ShadowRunner` does not save its result or change an action's status; step 14 calls `save_preview`. BE-49 and BE-50 (commit side) are finished in step 16; TEST-05
  needs a `company_main` rebuild fixture the first time a test writes to it. Cascade expectations count deleted keys only (D-140); step 16 may need ON UPDATE paths.
- Step 10: a failed delete after a good measurement is only logged (D-141); a leaked Neon branch counts against the cap of 10 until `expires_at` or the sweep removes it.
- Fill `LOCAL_COMPANY_WRITER_URL` and `FAKE_NEON_ADMIN_URL` in `backend/.env` with the Compose values from `.env.example` to use the local profile by hand.
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
- Pooled Neon endpoint under concurrent clients is untested (D-134); step 10 uses the direct endpoint. The MCP long-poll
  timeout was settled by the MCP spike.
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
- `log_config.json` sets the reloader process's level to INFO, so that process ignores
  `LOG_LEVEL` (the app process respects it). Dev only.
- Test helpers (`NetworkGuard`, database URLs) live in `tests/conftest.py` and are imported
  from it (hence `tests/__init__.py`). Move them to `tests/support.py` when the suite grows.
- `FastAPI(version="0.1.0")` duplicates the version in `pyproject.toml`.
- Step 8: `sql_display` and `pinned_sql` stay null on reads until step 17 wires the classifier in; reads also stay on
  `check_read` until then. When they switch, a read using a function that is not on the allowlist is refused (D-124).
- Step 9 known limits (D-131, pinned by tests): removing a
  block rule from `rules.yaml` removes that block (no code backstop); "Small insert" matches a CTE DELETE feeding an INSERT of
  10 rows or fewer. Step 10 must call `check_columns` with the company database's columns and supply `rows_before` per table
  and derived-group counts for inserted rows (D-132).
- Step 8: `ORDER BY` on a non-unique key with `LIMIT` still picks tied rows differently; step 16's key-set check catches it as
  drift. A bare `'now'` with no cast in a comparison is not detected (D-122). `COMMIT PREPARED` and `RELEASE SAVEPOINT` do not
  parse in sqlglot, so they are blocked as unknown.
- Step 8: the frozen `Clock` must pin to exactly 2026-10-01T00:00:00Z. Only `last_login_at` has
  a margin around its cutoff; other expected counts (e.g. tickets closed a year ago) are exact
  only at that instant (D-079).
- Step 10: `CREATE DATABASE ... TEMPLATE` does not copy database-level rights, so
  `FakeNeonClient` must grant CONNECT (reader) and CONNECT, TEMPORARY (writer) on each
  `shadow_*` database, as `seed.py` does for `company_main`.
- Step 6 onward: asyncpg sends unknown URL query parameters to Postgres as settings, so Neon's
  `channel_binding=require` must stay out of the company URLs (keep `sslmode=require`).
- Step 7: local Docker databases stay reachable from agent code by `python -c` (D-119); `docker` itself is denied (D-121).
- Step 7: `workspace.py verify` needs a Claude login and Sonnet, so it runs by hand, never in CI (D-120). A model that
  declines to attempt the Read makes it fail closed.
- Step 7: the workspace has no `PreToolUse` hook yet (step 18). A Bash redirect or `python -c` can still read files that
  the deny rules cover (D-002); the agent key pasted into a chat during the manual check belongs to dev agent `step7-check`.
- Gate audit F5: the agent can still write `.claude/settings.local.json` through Bash or PowerShell (`Edit(.claude/**)` does not cover shell writes), which could set `disableAllHooks`. Harmless until step 18 adds the hook; step 18 must handle it.
- Gate audit F4: `check_read` is a blocklist of functions (D-121). Step 8's classifier should use an allowlist; `pg_sleep` is allowed on purpose (the 5 s timeout stops it).
- Gate audit F2: `disableClaudeAiConnectors` does not cover user-scope MCP servers in `~/.claude.json` (none today). Tell demo users not to add a Neon or Postgres connector to their own account.
- Gate audit: BE-125 ("production uses Fly.io secrets") is ticked but can only be shown at Deploy.
- Step 7: trust the demo workspace folder by hand once (interactive sessions hold hooks back until then).
- Step 18: the hook's block message shows the agent the hook command line; the spike's results are single runs of Haiku.
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
- [x] Spike: Claude Code hook
- [x] 7. Remove direct access
- [x] GATE AUDIT: Claude Code reads the demo DB only through Blastproof

## Stage 2: judging parts
- [x] 8. Classifier
- [x] 9. Policy rules
- [x] Spike: Neon branching
- [x] 10. Neon client and shadow executor
- [x] 11. Knowledge and RAG, swappable vector store
- [x] 12. Impact engine (manual read of real Gemini wording deferred, Known issues)
- [x] 13. Risk judge, pluggable (manual read of real Gemini wording deferred, Known issues)
- [x] GATE AUDIT: exact diffs and correct retrieval

## Stage 3: the flow + dashboard start
- [x] Pull forward: GET /v1/actions, GET /v1/actions/{id} (`/v1/auth/me` done in step 5, D-092)
- [x] Spike: LangGraph interrupt and resume
- [x] 14. LangGraph workflow
- [x] 15. Workflow runner
- [x] Pull forward: POST /v1/actions/{id}/approve and /reject (D-053)
- [x] Pull forward: seed-demo-actions
- [x] 16. Commit
- [x] 17. Wire up query_database
- [x] F1. Scaffold
- [x] F2. Typed API client
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
