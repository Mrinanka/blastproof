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

---

*D-030 onward settle the "Implied, needs confirmation" items in `docs/REQUIREMENTS.md` (Prompt 0C).*

## D-030 · 2026-10-24 · Deactivated agent keys are rejected (IMP-01)
**Decision:** `get_current_agent` rejects the key of an agent whose `is_active` is false with 401,
on `/mcp` and on the hook endpoint.
**Why:** Otherwise the deactivate route changes nothing; it is one check in one dependency.
**Rejected:** deleting agent rows on deactivate (loses the link from past actions to their agent).

## D-031 · 2026-10-24 · The hook endpoint authenticates the agent key (IMP-02)
**Decision:** `/v1/hook/pre-tool-use` checks `BLASTPROOF_AGENT_KEY` with `get_current_agent` and
stores that agent and its owner on each `hook_events` row. A wrong or deactivated key gets 401, and
the hook script treats a 401 as a deny for every tool. The authorization matrix covers the endpoint
with a valid, a wrong and a deactivated agent key (it takes agent keys, not user logins).
**Why:** An unauthenticated endpoint would accept anyone's calls, and `hook_events` already has
`user_id` and `agent_id` columns to fill.
**Rejected:** an open hook endpoint; testing it as owner, other user and admin (it has no user login).

## D-032 · 2026-10-24 · Per-agent write rate limit: 30 per minute, counted from `actions` (IMP-03)
**Decision:** In `query_database`, once the classifier says a call is a write and before its action
is created, count that agent's write actions from the last 60 seconds. At
`MAX_WRITES_PER_MINUTE_PER_AGENT` (default 30) or more, reply with the `busy` message, create no
action, and log `event=rate_limited`. This is the one exception to step 6's "record every call".
**Why:** It stops a looping agent from flooding the review queue. Counting existing rows needs no new
table, and reusing `busy` needs no new message template.
**Rejected:** a separate counter table; a new `rate_limited` template; recording refused calls as
actions (a flood of refused calls would then flood `actions`).

## D-033 · 2026-10-24 · The LLM cost cap is checked once, after the summary (IMP-04)
**Decision:** After the summary call, if the action's `llm_cost_usd` is over
`MAX_LLM_COST_PER_ACTION_USD`, the judge is skipped and the write takes the judge-failure path: at
least review, with the `ai_unavailable` message. The summary already written is kept. Built in step 14.
**Why:** Cost is only known after a call, so between the two calls is the only point the cap can act.
Reusing the judge-failure branch adds no new path, and the summary has already passed the number check.
**Rejected:** also replacing the summary with the template (throws away a checked summary); no build
step (the setting would exist but do nothing).

## D-034 · 2026-10-24 · Latency and concurrency measured without a load-test tool (IMP-05)
**Decision:** The eval runner reports p50/p95 of `total_ms` per path (read, fast lane, previewed).
For reads it also runs each query directly, without Blastproof, and reports the difference as the
gateway's overhead (NFR-01). Concurrency (NFR-05) is one integration test in step 17: about 5 writes
plus reads at once with `FakeNeonClient`. A real load test is deferred.
**Why:** Real p95 numbers for the README and landing page with no new dependency.
**Rejected:** adding a load-testing tool (Locust, k6) to the MVP.

## D-035 · 2026-10-24 · The Evals stage tops the labeled set up to 50 (IMP-06)
**Decision:** Step 3 writes about 20 labeled actions; the Evals stage (Stage 4) adds the rest to
reach 50. A pytest checks there are at least 50 entries and that allow, review and block all appear.
**Why:** By then steps 8 to 13 have shown which edge cases are worth labeling.
**Rejected:** adding a few per step from 8 to 13 (no single owner, easy to forget).

## D-036 · 2026-10-24 · LLM-as-judge explanation score deferred (IMP-07)
**Decision:** The MVP checks explanation accuracy by code only (the number guardrail plus exact
checks in evals). The LLM-as-judge score in LangSmith moves to after the MVP.
**Why:** The numbers, which matter most, are already checked exactly; the LangSmith experiments
(rules only vs rules plus judge, with vs without RAG) still show LangSmith evals; neither MVP
checklist needs the score.
**Rejected:** building a LangSmith LLM-as-judge evaluator in the MVP.

## D-037 · 2026-10-24 · One audit event per status change; decisions record the approver (IMP-08)
**Decision:** Every status change writes an audit event named after the new status, plus the extra
events `shadow_done`, `decided` and `fail_closed`. The approve or reject claim in `resume()` writes
its event in the same transaction, with the approver's `user_id` and `display_name` in `data`.
Auto-approvals carry no approver.
**Why:** A rule cannot drift from the state machine the way a hand-kept list did (it had no
`rejected`, `blocked` or `awaiting_review`), and SPEC §13 requires the approver in the audit log.
**Rejected:** extending the hand-kept list.

## D-038 · 2026-10-24 · Which MCP calls create actions (IMP-09)
**Decision:** `query_database`, `list_tables` and `describe_table` each create an action; reads end
as `executed` and appear in History and `/v1/stats` as they are, with no new filter.
`get_action_status` creates no action and is only logged. Together with D-032's rate-limited
writes, these are the two exceptions to step 6's "record every call".
**Why:** One recording path keeps the audit complete, and the status filter already separates reads
from writes. A status call asks about an existing action; recording each poll would add about a
dozen rows per review.
**Rejected:** recording only `query_database`; recording `get_action_status` polls as actions.

## D-039 · 2026-10-24 · Reads over 500 rows are truncated with a notice (IMP-10)
**Decision:** A read fetches 501 rows. If the 501st exists, the agent gets the first 500 plus the
fixed `truncated` notice from `agent_messages.py`: "Showing the first 500 rows; add a LIMIT or narrow
the WHERE to see the rest." No total count is computed.
**Why:** The notice tells the agent what to change; an error makes it retry blindly. Counting the
total would need a second, possibly slow query.
**Rejected:** returning an error; computing the total row count.

## D-040 · 2026-10-24 · A rule blocks unparseable SQL (IMP-11)
**Decision:** SQL over 20 KB is not parsed and returns `kind: unknown`, like a parse error or parse
timeout. A pass-1 rule, "Unparseable SQL" (`kind_in: [unknown]` → block), blocks every `unknown`;
the agent gets the `blocked` template with the rule's reason.
**Why:** The plan called `unknown` "a dangerous write", but no rule matched it, so it would have gone
to review and a shadow run of SQL nobody could parse: a fail-open path. One rule closes it, and no
new template is needed.
**Rejected:** a separate `too_large` message; a hard-coded block in the classifier outside the rules.

## D-041 · 2026-10-24 · Logout clears the cookie only; server-side revocation deferred (IMP-12)
**Decision:** Logout deletes the session cookie. The JWT itself stays valid until its 8-hour expiry.
**Why:** The token only ever lived in that httpOnly cookie, never in page JavaScript. Revoking it
would need a backend logout route and a `token_version` bump on every logout.
**Rejected:** a backend logout route that bumps `token_version` in the MVP.

## D-042 · 2026-10-24 · The change request page shows derived groups (IMP-13)
**Decision:** The impact numbers section shows `derived_groups` (e.g. "paying: 12") next to the
breakdowns.
**Why:** It is the demo's key number; the reviewer should see it as computed by code beside the
LLM's sentence. `ActionDetail` already returns it.
**Rejected:** showing it only inside the LLM summary.

## D-043 · 2026-10-24 · Judge-override feedback loop deferred (IMP-14)
**Decision:** The MVP stores what the loop needs (`actions.judge` holds the judge's recommendation;
status and `decided_by` hold the human's decision). Turning overrides into eval cases comes later.
**Why:** The data costs nothing to keep; the export is new work no MVP check needs.
**Rejected:** building the export into the eval set in the MVP.

## D-044 · 2026-10-24 · Alerts deferred (IMP-15)
**Decision:** Spec §15's alerts (spikes in blocks, long waits, shadow failures, fail-closed events)
come after the MVP.
**Why:** `event=fail_closed` and the per-stage logs already make them countable; alerting needs a
tool the MVP does not have.
**Rejected:** adding an alerting service to the MVP.

## D-045 · 2026-10-24 · PR eval gate: incident replays at 100%; no false-block threshold yet (IMP-16)
**Decision:** Every pull request fails if any incident replay is not caught in the rule-only run;
the nightly LLM eval keeps the same 100% gate. False block rate is recorded with no threshold until a
first real measurement sets one.
**Why:** Rule-only replays are deterministic, so a hard gate never flakes. A threshold chosen before
measuring would be a guess.
**Rejected:** a catch-rate threshold on the labeled set per PR; inventing a false-block threshold now.

## D-046 · 2026-10-24 · Users page sits under Settings (IMP-17)
**Decision:** The sidebar's "Settings" item opens a group of Agents, Rules and Users (Users for
admins only). `settings/users/page.tsx` is added to the frontend project tree.
**Why:** The route is already `/dashboard/settings/users`; the link belongs with the other settings.
**Rejected:** a separate top-level "Users" sidebar item.

## D-047 · 2026-10-24 · Ruff bans `demo_company` imports in the backend (IMP-18)
**Decision:** Step 1 configures Ruff's banned-imports rule (`flake8-tidy-imports`, TID251) to ban
`demo_company`; CI's existing `ruff check` enforces it.
**Why:** It turns the CLAUDE.md rule into an automated check with no new dependency.
**Rejected:** a custom AST test; adding import-linter.

## D-048 · 2026-10-24 · One retention setting for working data; audit events kept (IMP-19)
**Decision:** The 10-minute sweeper deletes `preview_rows`, `before_images`, `hook_events` and
`login_attempts` older than `RETENTION_DAYS` (30). `audit_events` are never deleted in the MVP.
**Why:** One setting and one loop. `login_attempts` hold emails and IPs, so they should not pile up.
The audit log is the append-only record, and its trigger blocks deletes anyway.
**Rejected:** separate retention settings per table; deleting old audit events.

## D-049 · 2026-10-24 · Seeded actions use real retrieval; `--add-pending` adds one (IMP-20)
**Decision:** `seed-demo-actions` fakes only the LLM and Neon; retrieval uses the real knowledge
service and the configured embedder. `seed-demo-actions --add-pending` adds one new pending action on
`users` for demo user 1.
**Why:** DW-F10 needs an uploaded document to appear in "Knowledge used" (it does through the table
tag), and DW-F04, DW-F06 and DW-F10 each need one fresh pending action without re-seeding everything.
**Rejected:** faking retrieval in the seed; re-running the whole seed for each check.

## D-050 · 2026-10-24 · The Origin check is built in F3 (IMP-21)
**Decision:** F3's BFF route rejects any POST or DELETE whose `Origin` is not the dashboard's own
address.
**Why:** FE §11 promises it and FE §12 tests it, but no step listed it.
**Rejected:** relying on `SameSite=Lax` alone.

## D-051 · 2026-10-24 · "Today" in stats is the UTC day (IMP-22)
**Decision:** `/v1/stats` counts "today" as the UTC day, and the Overview cards say "today (UTC)".
The viewer's local day is deferred.
**Why:** The backend computes it with no time-zone input; the label keeps it honest for viewers
outside UTC.
**Rejected:** passing the viewer's time zone to the backend in the MVP.

## D-052 · 2026-10-24 · `ANCHOR_DATE` is 2026-10-01, kept in two checked places (IMP-23)
**Decision:** `ANCHOR_DATE` is 2026-10-01. `seed.py` writes it to `blastproof.seed_info`; the
scenario files carry `anchor_date: 2026-10-01` at the top, and backend tests and evals read it from
there. A CI test checks the two match on the local `company_main`.
**Why:** Expected counts in `incidents.yaml` need a fixed date. The backend cannot import the seed's
constant (D-047), so the value lives in two places, and the test stops them drifting apart.
**Rejected:** a backend config setting for the anchor (a third copy); importing it from `demo_company/`.

## D-053 · 2026-10-24 · Approve and reject routes are pulled forward to Stage 3 (IMP-24)
**Decision:** `POST /v1/actions/{id}/approve` and `/reject` are built right after step 15, complete
with the decision claim, the 409 naming who decided, the 10-character reason, owner-or-admin access
and their owner, other-user and admin tests. They use the stub commit until step 16.
**Why:** Step 17's "Done when" rejects an action in `/docs` and Gate 1 needs it approvable; both come
before step 19, so step 17 could not pass its own check.
**Rejected:** testing step 17 by calling `resume()` directly (Gate 1 is meant to be checked through
the API, as a reviewer would).

## D-054 · 2026-10-24 · The owner loads the seed into `blastproof-company`, at deploy time (IMP-25)
**Decision:** Step 3 seeds only the local `company-db` and Neon `blastproof-company-dev` (Claude
asks before the dev load, because it overwrites a Neon main branch). The owner loads
`blastproof-company` during deployment; the command prints the target project and asks for
confirmation. Claude Code never runs it.
**Why:** CLAUDE.md keeps Claude Code off `blastproof-company`, and that database is only needed for
the deployed demo.
**Rejected:** loading all three databases in step 3.

## D-055 · 2026-10-24 · MCP action IDs are scoped to the agent's owner (IMP-26)
**Decision:** `get_action_status` and `query_database`'s `retry_of` look up action IDs through
`visible_actions` called for the agent's owner with admin widening turned off, so the filter is the
owner's `user_id` even when the owner is an admin. An unknown or foreign ID gets the `not_found`
template: "No action {id} was found."
**Why:** Otherwise any agent could read another user's reviewer notes, and an admin's agent would
inherit admin-wide visibility. Keeping one function keeps the CLAUDE.md rule that every action query
goes through `visible_actions`; same-owner agents see each other's actions, as that function does.
**Rejected:** calling `visible_actions(owner)` with admin widening on (admin agents would see
everything); a separate lookup outside `visible_actions`; a 403-style message (it would reveal that
the action exists).

## D-056 · 2026-10-24 · Gemini is the default LLM provider
**Decision:** The summary and judge models default to Gemini through `init_chat_model`
(`google_genai:<Flash model>` for `SUMMARY_MODEL`, `google_genai:<Pro model>` for `JUDGE_MODEL`;
exact model names are picked from current docs in steps 12 and 13). The key is `GOOGLE_API_KEY`,
also the repository secret for the nightly LLM evals. `langchain-google-genai` replaces
`langchain-anthropic` in the stack; the owner approves it when step 1 installs it.
**Why:** The owner chose Gemini. The design is already provider-agnostic, and no evals exist yet, so
the "run the eval suite before switching models" rule does not apply: evals are built on Gemini.
**Rejected:** Anthropic Claude (Haiku and Sonnet) as the default; it stays one config string away.

## D-057 · 2026-10-24 · Neon projects run Postgres 18; local images match
**Decision:** The Neon projects use Postgres 18 in `aws-us-east-2`. The local Compose images become
`pgvector/pgvector:pg18` and `postgres:18`; step 1 checks both tags exist. Neon names each project's
default branch `production`; "main branch" in the plans means that branch, found by its ID
(`NEON_PARENT_BRANCH_ID`), never by name.
**Why:** Postgres 18 was Neon's default when the projects were created, and a project's version
cannot change later. The local `company-db` must match Neon so tests behave like the real database.
**Rejected:** recreating the projects on Postgres 16 to match the original plan (older, no benefit).

## D-058 · 2026-10-24 · The production Neon API key is created at deploy time
**Decision:** During outside setup only `blastproof-company-dev` gets an API key: project-scoped,
named `blastproof-dev-local`, in `backend/.env`. The `blastproof-company` key is created at deploy
time and goes straight into Fly.io secrets.
**Why:** Keeps the production credential off the dev machine, where the agent can reach `.env`
(D-002). A project-scoped key cannot touch any other project.
**Rejected:** creating both keys during outside setup; an org-wide key.

## D-059 · 2026-10-24 · Embeddings: fastembed with `BAAI/bge-small-en-v1.5`
**Decision:** Keep the plan's default embedder (384 dimensions, local, no key).
**Why:** Free, no extra account, and good enough for the MVP's knowledge base. Changing the
embedding model later means re-embedding every chunk, so the choice is made once, now.
**Rejected:** Voyage AI (another account and key; revisit only if retrieval fails the gate audit).

## D-060 · 2026-10-24 · Python 3.13 instead of 3.12
**Decision:** The backend targets Python 3.13: the owner's installed `C:\Python313`, managed by
uv 0.13.5. Step 1 pins `requires-python = ">=3.13,<3.14"`; the deploy image is `python:3.13-slim`.
**Why:** 3.13 is already installed, the planned libraries ship 3.13 builds, and it is supported for
a year longer (security fixes until October 2029). If a library fails `uv sync` in step 1, fall
back to 3.12 with `uv python install 3.12` and log the change.
**Rejected:** installing a separate 3.12 through uv.

## D-061 · 2026-10-24 · npm instead of pnpm for the frontend
**Decision:** The frontend uses npm (bundled with Node): `npm run dev`, `npm run lint`,
`npm run typecheck`, `npm test`, `npm run gen:api`; one-off tools run with `npx`. The lockfile is
`package-lock.json`, and CI installs with `npm ci`.
**Why:** The owner already uses npm, and it needs no extra install. pnpm's main gain here, refusing
imports of undeclared packages, is small for one app; ESLint's import rules can cover most of it.
**Rejected:** pnpm 12 (the plan's original choice).

## D-062 · 2026-10-24 · Node.js 24 LTS
**Decision:** The dev machine runs Node 24.22.1 ("Krypton", the current LTS), upgraded from 22.15
with winget. F1 pins the version for CI and Vercel (`engines` in `package.json`).
**Why:** The frontend plan asks for the current LTS. Node 22 is in maintenance only, until April 2027.
**Rejected:** staying on Node 22. Note: Claude upgraded Node without asking first; the owner
kept the upgrade, and Claude now asks before installing anything.

## D-063 · 2026-10-24 · MCP Python SDK v2 (`mcp` 2.3.0)
**Decision:** Pin `mcp==2.3.0`, the current major version. The plan was written against v1, so
step 6 and the MCP spike read "FastMCP" as v2's `MCPServer` (`from mcp.server.mcpserver import
...`). Known v2 changes to check in the spike: `stateless_http` and the transport options moved to
`streamable_http_app()`; `get_context()` is gone (a `ctx: Context` parameter is injected); the host
lifespan must enter `session_manager.run()`; DNS-rebinding protection is on by default, so the
Fly.io hostname needs `TransportSecuritySettings`.
**Why:** v1 is no longer the current line; starting the MVP on it would mean a migration later.
**Rejected:** pinning the last 1.x release to match the plan's wording.

## D-064 · 2026-10-24 · SQLAlchemy 2.1
**Decision:** Pin `sqlalchemy[asyncio]==2.1.6` instead of the plan's "SQLAlchemy 2.0 (async)".
**Why:** 2.1 is the current stable release and the async API the plan relies on is unchanged.
**Rejected:** holding back on 2.0.x.

## D-065 · 2026-10-24 · Version policy: latest stable, pinned exactly; whole MVP stack in step 1
**Decision:** Every backend dependency is the latest stable release on 2026-10-24, pinned with
`==` in `pyproject.toml`, with transitive versions fixed in `uv.lock`. Step 1 installs the whole
MVP stack at once, except `qdrant-client` and `voyageai` (after the MVP) and `mypy` (optional,
skipped). `faker` is a dev dependency, used only by `demo_company/seed.py`.
**Why:** One `uv sync` proves the full stack resolves on Python 3.13 (D-060) before any code
depends on it, and later steps need no new dependency approvals for planned libraries.
**Rejected:** adding each library in the step that first uses it.

## D-066 · 2026-10-24 · Request IDs: pure-ASGI middleware that trusts only incoming UUIDs
**Decision:** `RequestIdMiddleware` is plain ASGI, not Starlette's `BaseHTTPMiddleware`. It reuses
an incoming `X-Request-ID` only if it parses as a UUID (returned in canonical form), otherwise it
generates a `uuid4`. It binds `request_id` into structlog's contextvars, sets the response header,
and logs one `request_finished` line per request, so uvicorn's access log is raised to WARNING.
**Why:** `BaseHTTPMiddleware` breaks streaming responses such as the MCP transport (step 6).
Accepting only UUIDs stops clients from putting arbitrary text (newlines, huge values) into logs.
**Rejected:** echoing any incoming value; logging both our line and uvicorn's access line.

## D-067 · 2026-10-24 · Postgres 18 images mount their volume at `/var/lib/postgresql`
**Decision:** The Compose volume for `blastproof-db` mounts at `/var/lib/postgresql`, not
`/var/lib/postgresql/data` as in backend plan §15's snippet. The same applies to `company-db` in
step 3. The healthcheck runs `pg_isready` over TCP (`-h 127.0.0.1`).
**Why:** From Postgres 18 the official images (and `pgvector/pgvector:pg18`, built on them) set
`PGDATA=/var/lib/postgresql/18/docker` and declare the volume at `/var/lib/postgresql`; the old
mount path is no longer where data lives. The temporary server that runs the init scripts listens
only on the Unix socket, so a TCP check passes only once the roles exist (matters for CI).
**Rejected:** setting `PGDATA` back to the old path.

## D-068 · 2026-10-24 · The request-ID middleware turns unhandled exceptions into the 500
**Decision:** If the app raises before sending a response, `RequestIdMiddleware` logs
`event=unhandled_exception` (with the traceback and `request_id`) and sends the 500 itself, with
`X-Request-ID` and the body `{"error": {"code": "internal_error", "message": "Internal server
error"}}`. If the response has already started, it logs and re-raises so the server closes the
connection. Amends D-066.
**Why:** Starlette's outer error handler runs outside user middleware, so its 500 had no
`X-Request-ID` and its traceback no `request_id` (found in the step 1 verify review). OBS-04 says
every response. The body follows CLAUDE.md's error format and never includes the exception text.
**Rejected:** waiting for the step 19 error handlers (OBS-04 would stay false until then);
registering a FastAPI exception handler for `Exception` (Starlette still routes it through the
outer error handler, outside this middleware).

## D-069 · 2026-10-24 · The app is built on first access; dev runs with `--log-config`
**Decision:** `app.main` no longer builds the app at import time. A module `__getattr__`
(PEP 562) builds it once, the first time `app.main.app` is read, which is what uvicorn's
`app.main:app` does. `backend/log_config.json` (a logging `dictConfig` using
`app.logging.make_handler`) makes uvicorn's reloader process log JSON too, so the dev command in
CLAUDE.md is now `uv run uvicorn app.main:app --reload --log-config log_config.json`. The plan's
Done-when command still works without the flag. `LOG_LEVEL` is validated (one of
DEBUG/INFO/WARNING/ERROR/CRITICAL, case-insensitive).
**Why:** Importing the module (as tests do) used to read `backend/.env`, so a bad value there
could break test collection. The reloader process never imports the app, so only a log config
passed to uvicorn can make its lines JSON. An invalid `LOG_LEVEL` used to fail with a raw
`ValueError` from `logging` instead of a settings error.
**Rejected:** uvicorn's `--factory` flag (changes the plan's Done-when command); a custom launcher
script (`python -m app`), which is another entry point to keep in sync.

## D-070 · 2026-10-24 · Local Postgres: loopback only, digest-pinned, no temporary tables for the app
**Decision:** Compose publishes `blastproof-db` on `127.0.0.1:5432` and `[::1]:5432` only. The
image is pinned by its multi-arch index digest (`pgvector/pgvector:pg18@sha256:2358fc...`).
`blastproof_app` cannot create temporary tables (a side effect of `REVOKE ALL ... FROM PUBLIC`,
now deliberate, commented in the init script and tested). The same rules apply to `company-db`
in step 3.
**Why:** The superuser password is a dev value, so the port must not be reachable from the
network. Both loopback addresses are needed: with only IPv4, every `localhost` connection on
Windows waited about 2 s for IPv6 to fail. A floating tag could change the database under us
(CONV-16). Nothing in the stack needs temporary tables, so the app does not get the right.
**Rejected:** `0.0.0.0` (the plan's snippet); a bare `pg18` tag; granting `TEMPORARY` just in case.

## D-071 · 2026-10-24 · Test guards: wider network blocking, integration tests fail fast
**Decision:** The `no_network` fixture blocks non-loopback connections at four layers: DNS
(`getaddrinfo`), `socket.connect`/`connect_ex`, the asyncio event loop's `sock_connect`, and
psycopg's `connect`. A session fixture checks once that `blastproof-db` is reachable and fails
every integration test at once with "run `docker compose up -d`" if not.
**Why:** The verify review showed that an asyncio connection to a numeric IP got past a guard that
only patched sockets (on Windows the proactor loop connects in C), and psycopg's libpq bypasses
Python sockets entirely. Without the fail-fast check, a stopped database cost about 4 s per
integration test in timeouts.
**Rejected:** skipping integration tests when Postgres is down (a missing database must never
look like a pass); an OS-level network sandbox (not portable to Windows).

## D-072 · 2026-10-24 · CI starts Postgres with `docker compose up --wait`, not a service container
**Decision:** The CI job runs `docker compose up -d --wait` on the repo's `docker-compose.yml` after
checkout, instead of the "Postgres service container" in backend plan §8 step 2 and §15. Step 3's
`company-db` joins the same file, so CI needs no change for it. On failure the job prints
`docker compose logs`.
**Why:** GitHub starts `services:` containers before any step, so they cannot mount
`docker/blastproof-init/` from the checkout. One Compose file keeps CI identical to dev: the same
image digest, init scripts and healthcheck (D-067, D-070). `--wait` blocks until the TCP
healthcheck passes, which happens only once the roles exist. The first run showed that publishing
on `[::1]` works on the `ubuntu-24.04` runner.
**Rejected:** a `services:` container plus a `psql` step that replays the init SQL (two copies of
the setup that can drift).

## D-073 · 2026-10-24 · uv is pinned once, via `required-version` in `backend/pyproject.toml`
**Decision:** `[tool.uv] required-version = "==0.13.5"`. CI's `astral-sh/setup-uv` reads it through
`version-file: backend/pyproject.toml`, and locally uv refuses to run at any other version.
**Why:** PROGRESS asked for uv to be pinned in CI. Writing the pin once keeps dev and CI on the same
uv, and a mismatch fails loudly instead of resolving differently. The green CI run proves setup-uv
honoured it: `uv sync --locked` would have failed under any other version.
**Rejected:** `version: "0.13.5"` in the workflow only (dev and CI could drift); no pin (setup-uv
installs the latest uv).

## D-074 · 2026-10-24 · Dependabot: alerts plus weekly grouped version PRs into `develop`
**Decision:** `.github/dependabot.yml` sets weekly updates for `uv` (`/backend`), `github-actions`
and `docker-compose` (the digest-pinned image). Each ecosystem gets one grouped PR, targets
`develop`, has `open-pull-requests-limit: 3` and a 7-day cooldown. The owner also turns on alerts and
security updates in the repo settings (SEC-21).
**Why:** Exact pins (D-065, D-070, D-075) go stale and miss fixes unless something bumps them.
Grouped weekly PRs that must pass CI keep updates reviewable. The cooldown skips releases that are
pulled again within days.
**Rejected:** alerts only, as the plan says (pins would be bumped by hand); daily or ungrouped
updates (too many PRs for one person).

## D-075 · 2026-10-24 · CI hardening: SHA-pinned actions, pinned runner, read-only token, no secrets
**Decision:** Every action is pinned to a full commit SHA (checkout v7.0.1, setup-uv v10.3.2), the
runner is `ubuntu-24.04`, `permissions` is `contents: read`, checkout uses
`persist-credentials: false`, and the PR workflow uses no secrets. `backend/tests/test_ci_workflow.py`
enforces all of this, plus the step order and that integration tests are not filtered out.
**Why:** A tag can be moved to other code; a commit SHA cannot. PR code runs inside CI, so with no
secrets and a read-only token it has nothing to leak and nothing to push (TEST-01). `ubuntu-latest`
moves to 26.04 in November 2026, which would change the build under us. The tests catch a one-line
edit that undoes any of this.
**Rejected:** floating tags and `ubuntu-latest`; LLM evals with `GOOGLE_API_KEY` in the PR workflow
(they belong to the nightly workflow, OPS-02). Known gap: the secret check matches `secrets.` as
text, so bracket syntax (`secrets['X']`) would get past it.

## D-076 · 2026-10-24 · The company writer role may create temporary tables
**Decision:** On the company databases, `blastproof_writer` gets `CONNECT, TEMPORARY`;
`blastproof_reader` gets `CONNECT` only. PUBLIC gets nothing. Amends D-070's "same rules apply to
`company-db`" for the writer.
**Why:** The shadow run keeps its before-hashes in session temp tables on the branch, connected as
the writer (D-018). The reader never needs them, so it keeps least privilege.
**Rejected:** no temp tables for either role (the shadow run could not work); granting TEMPORARY
only on shadow databases (Neon branches copy main's rights, so main must have it anyway).

## D-077 · 2026-10-24 · The seed creates the company roles with SQL, never in the Neon Console
**Decision:** `seed.py --target neon` creates or updates `blastproof_reader` and `blastproof_writer`
with `CREATE/ALTER ROLE ... NOSUPERUSER NOCREATEDB NOCREATEROLE`, taking the passwords from
`COMPANY_DB_READER_URL` and `COMPANY_DB_WRITER_URL`. It refuses a role that is a member of any other
role, and asks for it to be deleted in the Console first.
**Why:** Neon makes every Console- or API-created role a member of `neon_superuser` (CREATEROLE,
CREATEDB, BYPASSRLS), which would break BE-09's least privilege. SQL-created roles get only basic
rights. Neon requires 60 bits of password entropy for SQL-created roles.
**Rejected:** creating the roles in the Console; generating passwords in the script (it would have
to print them).

## D-078 · 2026-10-24 · Deterministic seed: exact demo numbers by construction
**Decision:** Every value that changes a count comes from `random.Random(seed).random()`; Faker
fills only cosmetic text. The user mix is fixed: 50,000 users, 48,213 inactive at the anchor
(free 48,201, pro 9, enterprise 3). Every `last_login_at` is at least 14 days from the 6-month
cutoff and is `NOT NULL`. Two seed runs give byte-identical tables (checked).
**Why:** The spec, plans and landing page quote 48,213 and 12. Python promises a stable `random()`
sequence across versions but not `randint` or `choice`, and Faker output may change when Dependabot
bumps it. The margin keeps counts exact whatever the time of day and for "180 days" as well as
"6 months". A NULL last login would silently fall out of `last_login_at < ...`.
**Rejected:** leaving the counts to Faker's randomness and recording whatever came out.

## D-079 · 2026-10-24 · Scenario files: two kinds of entry, labels are the correct outcome
**Decision:** `evals/schema.py` has `sql` entries (sql, intent, expected decision, `expected_rules`
as a list, `decidable_from_sql`, optional `expected_impact`) and `hook` entries (Claude Code's
PreToolUse `tool_name` and `tool_input`, expected allow or deny). A label is the right decision, not
what today's rules produce; `notes` says where rules alone differ. Expected counts were measured on
the seed and are checked by `tests/test_seed_counts.py`, which pins `now()` to midnight UTC on the
anchor date and diffs row hashes by primary key, as the shadow run will.
**Why:** Three of the six MVP incidents are hook calls with no SQL. Several rules can match one
write. Labels taken from the rules would make the false block rate always zero. Pinning `now()` at
midnight makes counts exact at any time of day (a time window without a seed margin, such as
tickets closed a year ago, moved by 2 rows when the real clock was used).
**Rejected:** forcing hook incidents into a SQL shape; one `expected_rule`; rebasing a copy to today
for the count test (exact only for columns with a margin).

## D-080 · 2026-10-24 · Seed and rebase commands: targets, confirmation, no backend imports
**Decision:** The scripts run from the repo root with `uv run --project backend python
demo_company/<script>.py` (the root has no `pyproject.toml`). `--target local` uses the Compose
superuser (`FAKE_NEON_ADMIN_URL`, dev default). `--target neon` uses `SEED_NEON_OWNER_URL`, refuses a
`-pooler` host, prints the target, and changes nothing unless `--confirm <host>` names the host
exactly. `rebase_dates.py` is a separate script that shifts only `public` columns by whole days.
Settings come from the environment, then `backend/.env`; `SEED_ENV_FILE` overrides the file so tests
never read it. Errors print no traceback and no password. Backend tests check the scripts by running
them as subprocesses and through the database, never by importing them (D-047); Ruff lints
`demo_company/` with the backend's settings.
**Why:** A typed host cannot be confirmed by habit the way "y" can, and works without a terminal.
Neon recommends the direct endpoint for admin work; the pooler drops session settings.
**Rejected:** a bare `--yes` flag; a `[y/N]` prompt (cannot run from a non-interactive tool);
subcommands on `seed.py` (changes the plan's command).

## D-081 · 2026-10-24 · Demo schema details the plan left open
**Decision:** Primary keys are `bigint GENERATED BY DEFAULT AS IDENTITY`, and the seed moves each
sequence past the loaded ids. `order_items.product_id` is not a foreign key. `orders`,
`support_tickets` and `customer_notes` default `created_at` to `now()`. Every table has a
`COMMENT ON TABLE` line. `blastproof.seed_info` has a one-row boolean primary key.
**Why:** Agent inserts must work without an id. The plan lists every FK, and D-010 keeps RESTRICT
out of the demo. Agents leave `created_at` out of inserts. `list_tables` (step 6) can read the
comments as its one-line descriptions.
**Rejected:** `serial` columns; a RESTRICT foreign key to products.

## D-082 · 2026-10-24 · The vector extension is created by the init script, not the owner
**Decision:** `docker/blastproof-init/02-extensions.sql` runs `CREATE EXTENSION IF NOT EXISTS
vector` as the Compose superuser. The first migration keeps `CREATE EXTENSION IF NOT EXISTS vector`
(BE-14), which is then a no-op; without the extension the migration fails with "permission denied"
and creates nothing (one transaction). On Neon `blastproof-app`, the Deploy step already creates it
(backend plan §15). The existing dev volume got it with a one-off `psql -f` of the same file.
**Why:** pgvector 0.8.7 is not a trusted extension (`pg_available_extension_versions`: superuser
true, trusted false), so `blastproof_owner`, which is not a superuser, cannot create it.
**Rejected:** making the owner a superuser; installing the extension into `template1` (every new
database would get it, which hides the missing-extension failure in tests).

## D-083 · 2026-10-24 · The append-only trigger is statement-level and also blocks TRUNCATE
**Decision:** `audit_events_append_only` is `BEFORE UPDATE OR DELETE OR TRUNCATE ... FOR EACH
STATEMENT` and raises `insufficient_privilege` with "audit_events is append-only: <op> is not
allowed". The app has only SELECT and INSERT on the table (D-013).
**Why:** `TRUNCATE` skips row-level DELETE triggers, so the planned `BEFORE UPDATE OR DELETE`
trigger alone would let the owner empty the log. Statement level also fires for statements that
match no rows. **Limit:** the owner can still drop or disable the trigger; that is deliberate DDL
in a migration, not a quiet rewrite, and the app role can do neither (tested).
**Rejected:** a row-level trigger only (the plan's wording).

## D-084 · 2026-10-24 · Data model gaps filled in step 4
**Decision:** Four additions to backend plan §5: `actions.pinned_values` (jsonb; `ActionDetail`
returns it and the intercept time cannot be recomputed), `actions.failure_reason` (text; the
`failed` message's `{reason_code}`, keeping `policy_reason` for rules), and `created_at` on
`preview_rows` and `before_images` (retention needs an age). `preview_rows` has the primary key
(action_id, table_name, primary_key), one row per affected row; a re-preview must delete the old rows
first, or the insert fails. Emails are unique ignoring case (`lower(email)`).
**Why:** Each was needed by a later step and missing from the column list.
**Rejected:** keeping reason codes only in audit-event data; a surrogate id on `preview_rows`
(would let a missed cleanup double-count silently).

## D-085 · 2026-10-24 · Schema conventions: text plus named CHECKs, cosine HNSW, sync migrations
**Decision:** Enum-like columns are `text` with a named CHECK built from a `StrEnum` in
`app/db/enums.py`; the migration freezes the value lists, and `tests/test_models.py` checks them
against the enums (Alembic does not compare CHECKs). All constraints are named by a naming
convention; indexes are named by hand. ids are `uuid DEFAULT gen_random_uuid()`, timestamps
`timestamptz`. The HNSW index uses `vector_cosine_ops`, so step 11 must search with `<=>`.
Migrations use a plain `postgresql://` URL, which SQLAlchemy 2.1 runs on psycopg 3 (its new
default), the same URL form the checkpointer needs in step 14. `alembic check` runs in the tests.
**Why:** Adding a value to a CHECK is a small migration; Postgres enum types are harder to change.
Cosine is right whether or not the embedder normalises its vectors.
**Rejected:** Postgres enum types; inner-product ops (only correct for normalised vectors); an
async (asyncpg) Alembic environment.

## D-086 · 2026-10-24 · Migration tests use scratch databases; CI migrates before pytest
**Decision:** Tests create `bp_scratch_*` databases with the Compose superuser
(`TEST_BLASTPROOF_ADMIN_URL`, test-only), set up like the init scripts, migrate them as the owner and
drop them afterwards; they never migrate the dev `blastproof` database. CI runs `uv run alembic
upgrade head` after `docker compose up` with the local dev owner URL (not a secret). Its placement is
checked by its own test instead of `REQUIRED_RUN_ORDER`.
**Why:** Upgrade and downgrade tests would otherwise wipe dev data. Adding the step to
`REQUIRED_RUN_ORDER` would have broken the exact-message check in
`test_order_check_catches_a_seed_before_postgres`, an existing test that must not change.
**Rejected:** granting CREATEDB to `blastproof_owner`; migrating the shared dev database in tests.

## D-087 · 2026-10-24 · Migrations refuse any role but the owner, and log JSON
**Decision:** `migrations/env.py` checks `current_user` inside the migration transaction and stops
with `WrongMigratorRoleError` ("Nothing was changed") unless it is `blastproof_owner`. From the
command line it sets up `app.logging` (JSON lines, SQLAlchemy's SQL logging kept at WARNING), so
`alembic.ini` has no logging sections. With an explicit URL (tests), `env.py` never reads
`backend/.env`. A test checks that scratch databases are set up exactly like the dev `blastproof`
database (owners, access lists, the vector extension).
**Why:** Found in the step 4 verify review. Run as a superuser, a migration would leave tables the
owner can never migrate again; run as the app, it fails halfway. Migrations run on every Fly.io
deploy (`release_command`), so their logs should match the app's. The check must sit inside
`begin_transaction()`: a query before it opens a transaction that Alembic joins and never commits.
The scratch helper repeats part of the init scripts, and without the test the two could drift.
**Rejected:** checking only "not a superuser" (the app role would still get halfway); keeping
Alembic's plain-text logging.
