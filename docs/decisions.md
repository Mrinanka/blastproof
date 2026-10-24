# Decisions

Append-only. Each entry: the decision, why, and the alternative rejected. Newest at the bottom.

---

## D-001 · 2026-10-24 · The security boundary is credential isolation, not the hook
**Decision:** Hooks and deny-lists are guardrails that give clear feedback and catch honest
mistakes. The boundary is that no credential that matters exists where the agent process can read.
**Why:** Bash deny-lists are always bypassable (`cat .e""nv`, `python -c`, new tool names). The
PocketOS incident was a credential found in a file, which isolation prevents and a deny-list does not.
**Rejected:** treating the PreToolUse hook as the main control.

## D-002 · 2026-10-24 · Full isolation only on the deployed demo; dev runs with guardrails
**Decision:** On the dev machine Claude Code runs directly, guarded by the hook and
`permissions.deny`; the dev `.env` stays on the same disk. The deployed demo is fully isolated:
production company credentials live only in Fly.io secrets.
**Why:** The owner chose no setup blockers in dev. The risk is contained because dev credentials
only unlock the separate dev company project (D-003).
**Rejected:** a Docker devcontainer for the agent in dev (still possible later).

## D-003 · 2026-10-24 · Three company databases: test (local), dev (own Neon project), prod
**Decision:** Tests and CI use a local `company-db` container with a `FakeNeonClient` that clones
a template database. Dev uses the Neon project `blastproof-company-dev`. The deployed demo uses
`blastproof-company`, touched only by the deployed backend.
**Why:** Neon branches share role passwords with their parent, so a dev *branch* of the prod
project would put prod-capable credentials in the dev `.env`. Template databases give tests real
copy semantics with no network.
**Rejected:** one shared company project (local commits changed the demo data); a dev branch
inside the prod project.

## D-004 · 2026-10-24 · Measure write effects with whole-table hash diffs on the branch
**Decision:** The shadow run hashes every row of every company table before and after the write,
and diffs by primary key. Every table must have a primary key. Affected rows' before-hashes are
stored in `preview_rows`.
**Why:** It catches CTE writes, `UPDATE … FROM`, cascades, `SET NULL` and triggers with no special
cases, and gives the drift check its data. It costs milliseconds at demo scale.
**Rejected:** predicting effects by rewriting SQL into SELECTs plus count deltas (misses `SET NULL`,
triggers and cancelling changes). **Limit:** past a few million rows, restrict to FK-reachable tables
or use logical decoding.

## D-005 · 2026-10-24 · Pin non-deterministic values; commit exactly what was previewed
**Decision:** `now()` and its relatives are replaced with one timestamp literal at intercept;
`pinned_sql` is previewed, shown, approved and committed. Volatile functions in a WHERE are
blocked. The commit locks the approved rows, compares their hashes, runs `RETURNING pk` and requires
the same key set, or rolls back as drift.
**Why:** Otherwise the demo's own `now() - interval '6 months'` drifts between preview and commit,
and the reviewer would approve something other than what runs.
**Rejected:** re-previewing on every commit; ignoring non-determinism.

## D-006 · 2026-10-24 · Idempotent commits via a marker table in the company database
**Decision:** Each commit inserts its `action_id` into `blastproof.applied_actions` in the same
transaction; a startup reconciler resolves actions left in `committing`. Approvals claim the
decision with a conditional `UPDATE … WHERE status='awaiting_review'` before resuming the graph.
**Why:** The commit spans two databases; this makes "no action applied twice" provable and closes
the double-approve race.
**Rejected:** two-phase commit (`PREPARE TRANSACTION`), which is too heavy for the MVP.

## D-007 · 2026-10-24 · A fast lane for trivially safe inserts
**Decision:** A single `INSERT … VALUES` of at most 10 rows into a `fast_lane_tables` table, matched
by an allow rule, skips the shadow run, the judge and the LLM summary. Target: under 300 ms.
**Why:** Demo step 1 ("add a note") must feel instant; a branch plus two LLM calls cannot.
**Rejected:** running the full pipeline for every write.

## D-008 · 2026-10-24 · Judge failure means review; missing facts mean block
**Decision:** If the shadow run or diff fails, the write is blocked. If only the AI judge fails or
times out, the write goes to human review. The judge may raise to review or block, never lower.
**Why:** Fail-closed means "never applied without rules or a human allowing it". With facts in
hand, a human can still decide, so an LLM outage shouldn't block every write.
**Rejected:** blocking on judge failure.

## D-009 · 2026-10-24 · Policy rule semantics
**Decision:** The condition keys are a closed list. In pass 1, rules that need diff keys are
skipped, not treated as false. A pass-1 block skips the shadow run. Auto-approve needs a positive
allow match and no stricter match. "Every row" is caught by a constant-true WHERE check plus the
measured `fraction_of_table_gte: 1.0` (block); a majority of a table goes to review.
**Why:** Two engineers would otherwise build different engines, and `has_where` alone is defeated
by `WHERE true`.
**Rejected:** blocking any change over 50% of a table, which would block the demo's mass delete
instead of holding it for review.

## D-010 · 2026-10-24 · Demo schema foreign keys: all CASCADE plus SET NULL
**Decision:** Every child of `users` and `orders` uses `ON DELETE CASCADE`, except
`invoices.user_id` and `users.organization_id`, which use `SET NULL`.
**Why:** The headline delete succeeds and the report shows both deleted and nulled side effects.
**Rejected:** including a `RESTRICT` (an FK error in the demo).

## D-011 · 2026-10-24 · MVP demo and incident scope trimmed
**Decision:** The MVP demo is: fast-lane note, Replit mass delete with reject and retry, a bypass
attempt, and the numbers. The MVP incident replays add PocketOS-lite (credential hunt denied).
The real cloud-API, Terraform, injection and undo scenes move to Phase 2.
**Why:** The backend MVP has no cloud-API tool or undo; demoing them would mean building them early.
**Rejected:** a fake `cloud_api` MCP tool in the MVP (about 2 extra days).

## D-012 · 2026-10-24 · How the agent learns a review outcome
**Decision:** `get_action_status` long-polls up to 50 s, and the pending message tells the agent to
keep calling it. `query_database` takes an optional `retry_of` to link a rewrite to the rejected
action. Each outcome has a fixed message template in `agent_messages.py`.
**Why:** Claude Code does not poll by itself; a short status call would end the turn at "pending".
**Rejected:** holding the original tool call open until a human decides.

## D-013 · 2026-10-24 · Two database roles and a trigger for the append-only audit log
**Decision:** `blastproof_owner` runs migrations and the checkpointer `setup()`; `blastproof_app`
runs the app with only SELECT and INSERT on `audit_events`. A `BEFORE UPDATE OR DELETE` trigger
also rejects changes.
**Why:** With one role, the app owns the table and REVOKE does nothing.
**Rejected:** a single role with REVOKE.

## D-014 · 2026-10-24 · Hook calls are stored in `hook_events`, not `actions`
**Decision:** Every hook call writes a `hook_events` row; hook calls never create actions or enter
the review queue. A dashboard view of them is deferred.
**Why:** Every Bash call would flood the actions table and the review queue.
**Rejected:** storing hook calls as actions.

## D-015 · 2026-10-24 · Every signed-in user creates their own agents
**Decision:** Any signed-in user creates and deactivates their own agents; admins manage users,
systems and knowledge.
**Why:** It matches the spec's ownership model (SPEC §4); the frontend plan contradicted it.
**Rejected:** admin-only agent creation.

## D-016 · 2026-10-24 · New dependencies: `psycopg[binary]` and `langchain`
**Decision:** Add both to the plan's stack (exact versions pinned in the step that installs them).
**Why:** LangGraph's Postgres checkpointer requires psycopg 3; `init_chat_model` lives in
`langchain`, not `langchain-anthropic`.
**Rejected:** writing a custom checkpointer or model factory.

---

*D-017 to D-028 come from a review of the applied plan (2026-10-24).*

## D-017 · 2026-10-24 · Stable demo numbers: anchor-dated seed, rebase command, injectable Clock
**Decision:** The seed generates every timestamp relative to a fixed `ANCHOR_DATE`. A `rebase-dates`
command shifts all timestamps on a long-lived database by (today minus the anchor). The classifier
takes its "now" from an injected `Clock`, frozen at the anchor in tests and evals.
**Why:** The demo query is relative to `now()`, so a fixed Faker seed alone lets counts drift daily.
Rebasing keeps relative ages, so 48,213 and 12 are reproduced exactly on any day.
**Rejected:** freezing the clock in the deployed app (a pinned "now" that isn't now is a
correctness bug outside demos).

## D-018 · 2026-10-24 · Shadow run inside a rolled-back transaction, diffed on the branch
**Decision:** Before-hashes go into session temp tables on the branch. The write runs inside a
transaction, a FULL OUTER JOIN returns only the differences, then ROLLBACK, and before-rows are read
back by key for breakdowns, derived groups and samples. One `ROW_HASH_SQL` expression is computed by
Postgres in both the shadow run and the commit, with `TimeZone=UTC` set on every connection.
**Why:** Hashes alone can't produce breakdowns after a DELETE; shipping whole-table hashes to the
backend is wasteful; hashes computed in different places or time zones would always "drift".
**Rejected:** copying full rows to the backend; hashing in Python.

## D-019 · 2026-10-24 · FakeNeonClient database layout
**Decision:** `company_template` is seeded once and then locked against connections. `company_main`
is cloned from it, and shadow databases are cloned from `company_main`. The `local` profile uses no
connection pool, and the fake ends stray sessions with a test-only superuser URL before cloning.
**Why:** `CREATE DATABASE … TEMPLATE` fails if any session is connected to the source.
**Rejected:** cloning from a database the app keeps open.

## D-020 · 2026-10-24 · A 10-minute polling budget for agents
**Decision:** After `AGENT_POLL_BUDGET_MINUTES` (10) waiting for review, `get_action_status` tells
the agent to stop polling and tell the user.
**Why:** Polling for a 24-hour review window means about 1,700 tool calls of context and budget.
**Rejected:** unlimited polling (D-012 as first written).

## D-021 · 2026-10-24 · Cascade check by FK path
**Decision:** The preview stores, per FK path (from `pg_constraint`, up to depth 3), the count of
child rows referencing the affected keys. The commit repeats the same queries on main and compares.
**Why:** RETURNING covers only the target table, and a `SET NULL` cascade changes no row counts.
**Rejected:** comparing table row counts.

## D-022 · 2026-10-24 · Before-images saved before the company COMMIT; re-run on a missing marker
**Decision:** Before-images are committed to Blastproof's database before the company COMMIT, and
deleted if it fails. An action left in `committing` with no marker row is re-run through the full
commit procedure, not failed.
**Why:** Nothing is lost on a crash, and the marker table plus drift check make a re-run safe,
which is the point of having the marker.
**Rejected:** saving before-images after COMMIT; failing actions on a missing marker.

## D-023 · 2026-10-24 · Derived groups are scoped per table
**Decision:** `derived_groups` is keyed by table (`users: {paying: ...}`) and counted only over that
table's affected rows; rules reference `users.paying`. Startup validates the columns.
**Why:** An unscoped `plan in (...)` fails on tables without `plan`, which would block every write
to them.
**Rejected:** global groups.

## D-024 · 2026-10-24 · Fast lane: literals only, and the rule is the single source of truth
**Decision:** Fast-lane VALUES may hold only literals, NULL, DEFAULT, casts of literals and pinned
timestamps. Which tables qualify is set only by an `allow` rule with `fast_lane: true`;
`fast_lane_tables` is removed.
**Why:** A function call in VALUES would otherwise skip the shadow run and the judge; two lists
would drift apart.
**Rejected:** "no SELECT" as the only restriction.

## D-025 · 2026-10-24 · Restart safety: two-node review, startup recovery, graceful drain
**Decision:** Review is split into `request_review` (status) and `human_review` (only
`interrupt()`). At startup: in-flight actions (`received`, `classified`, `previewing`) are failed,
decided-but-not-resumed actions are resumed, and `committing` goes to the reconciler. On shutdown,
new writes get `busy` and in-flight work has 45 s (`kill_timeout = 60`).
**Why:** `interrupt()` re-runs its node on resume; one machine means anything in flight at startup
is orphaned; every deploy restarts that machine.
**Rejected:** resuming half-done previews from their checkpoint (the shadow branch may be gone).

## D-026 · 2026-10-24 · Hook script: offline fallback for read tools only
**Decision:** If the backend is unreachable after one retry, Bash and edit tools fail closed, but
Read, Grep and Glob fall back to a local path deny-list and are otherwise allowed.
**Why:** The hook is a guardrail (D-001); blinding the agent on every `--reload` restart helps no
one, and sensitive paths stay denied offline.
**Rejected:** denying every hooked tool when the backend is down.

## D-027 · 2026-10-24 · Seeded actions use a local connection profile
**Decision:** `systems` rows carry a `connection_profile` (`neon` or `local`). Seeded actions
belong to a `local` system, so approving one previews and commits against the Compose
`company-db`, never Neon dev. The commit also refuses a profile mismatch.
**Why:** Seeded hashes come from the same seed, so they would match Neon dev and the commit would
really run there.
**Rejected:** making seeded actions non-committable (the dashboard needs a real approve path).

## D-028 · 2026-10-24 · Landing page CSP allows inline scripts
**Decision:** The dashboard and API use a nonce-based CSP; the static landing page uses
`script-src 'self' 'unsafe-inline'` (hash-based if the build makes it practical).
**Why:** The App Router emits inline scripts even on static pages; the landing page renders no user
or agent content and loads no third-party scripts.
**Rejected:** a nonce CSP everywhere (it would make the landing page dynamic).

## D-029 · 2026-10-24 · Four follow-up fixes from the round-2 check
**Decision:**
1. A connection lost during the company COMMIT counts as an unknown outcome: the action stays in
   `committing` with its before-images, and the commit reconciler decides from the marker row.
   Only an error from Postgres marks it failed.
2. Every `local`-profile operation on `company_main` (clone, read, commit) takes one shared
   advisory lock, so `FakeNeonClient`'s session termination never aborts other work.
3. Startup recovery reads each decided thread's state first: paused at `interrupt()` means
   `Command(resume)`, past it means continue from the checkpoint, and a missing thread means fail.
4. `rebase-dates` is owner-run only. It confirms the target project first and should run with an
   empty review queue, because it makes pending reviews drift.
**Why:** A dropped connection doesn't prove the COMMIT failed. Killing sessions before a clone
would abort concurrent local work. A crash between resume and `committing` left the action stuck.
`rebase-dates` rewrites a Neon main branch, which CLAUDE.md reserves for the owner.
**Rejected:** treating every COMMIT exception as failure; `MAX_CONCURRENT_SHADOWS=1` alone (it
doesn't serialise commits); resuming without checking the thread's state.
