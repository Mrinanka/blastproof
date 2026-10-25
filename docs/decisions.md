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

## D-088 · 2026-10-25 · Auth errors use the error envelope now, one generic message per kind
**Decision:** `app/core/errors.py` adds `ApiError` and a handler that returns
`{"error": {"code", "message"}}`. Auth uses the codes `invalid_credentials` (401; wrong password,
unknown email and inactive user look the same), `not_authenticated` (401; any token problem),
`invalid_agent_key` (401; wrong or deactivated key), `forbidden` (403), `too_many_attempts` (429)
and `unavailable` (503; database or JWT secret not configured). Every 401 carries
`WWW-Authenticate: Bearer`. The real reason goes to the logs only (`event=auth_failed reason=...`),
which never hold a password, token, key or email. FastAPI's own 404 and 422 keep `{"detail"}` until
step 19.
**Why:** The dashboard is built against the envelope (CONV-04). One message per kind stops callers
from learning which emails exist or why a token failed.
**Rejected:** `HTTPException` with `detail` (wrong shape); replacing FastAPI's global handlers now
(step 19's job).

## D-089 · 2026-10-25 · Login rate limit details
**Decision:** At most 5 failed logins per email+IP in 15 minutes, and 20 per email in an hour
(SEC-06), counted with the database clock. Each attempt runs in one transaction holding
`pg_advisory_xact_lock(LOGIN_LOCK_CLASS, <int4 from the email's SHA-256>)`, so parallel attempts are
counted one at a time. Over a limit the answer is 429 with `Retry-After`, the password is not
checked (even a correct one is refused), and the attempt is **not recorded**, so a lockout ends
exactly when the window passes. Unknown emails are recorded like real ones. A success does not
clear earlier failures. Emails are compared lower case. The IP is `request.client.host`; an
address that is not an IP counts as `0.0.0.0`. Note that uvicorn by default trusts
`X-Forwarded-For` from `127.0.0.1` and `::1` (`--proxy-headers`, `FORWARDED_ALLOW_IPS`), so locally
any process on the machine can choose the IP it is counted under; on Fly the peer is the proxy, not
loopback, so the header is ignored. Adds 429 to CONV-07.
**Why:** Without the lock, 10 parallel guesses all saw "4 failures so far" (the concurrency test
fails without it). Recording refusals would extend a lockout for as long as an attacker keeps
trying. Trusting `X-Forwarded-For` from anyone would let an attacker rotate IPs for free.
**Limits:** Behind the dashboard's login route and Fly's proxy, every user shares the proxy's IP,
so the per-IP limit acts per email (stricter, so safe). Anyone can lock a known email out for an
hour; the plan's per-email limit allows that by design, and there is no admin unlock in the MVP.
**Rejected:** counting refusals; clearing failures on success; trusting forwarded headers from
anyone.

## D-090 · 2026-10-25 · Agent keys: shape checked first; a deactivated owner disables their agents
**Decision:** `authenticate_agent_key` refuses anything that is not `bp_` plus 43 URL-safe
characters before touching the database, then looks the key up by SHA-256 hash. The agent and its
owner must both be active (D-030 covers the agent). It is a plain function, so step 6's `/mcp`
middleware can call it; `get_current_agent` wraps it for routes.
**Why:** Deactivating a user should stop their agents too; otherwise an offboarded user's agent
keeps writing actions under their id. FastAPI dependencies do not run inside a mounted ASGI app.
**Rejected:** checking the agent only.

## D-091 · 2026-10-25 · The app refuses any database role but `blastproof_app`
**Decision:** The app's engine (`app/db/session.py`, asyncpg, `pool_pre_ping`) checks
`current_user` on every new connection and raises `WrongAppRoleError` (logged as
`event=wrong_app_role`) unless it is `blastproof_app`. The CLI uses the same engine. The engine is
built at startup but connects only on the first query (TEST-07). With `APP_ENV=production`,
`create_app` refuses to start without `BLASTPROOF_DATABASE_URL` and `JWT_SECRET`; `JWT_SECRET` must
be at least 32 bytes (the HS256 digest size) whenever it is set.
**Why:** It mirrors D-087 for the running app (part of SEC-09): a URL pointing at the owner or a
superuser would quietly bypass the append-only grants. The production check sits in `create_app`,
not in `Settings`, because an existing test loads `Settings` with `APP_ENV=production` and no
secrets.
**Rejected:** a startup query (startup must stay offline); a `Settings` validator.

## D-092 · 2026-10-25 · Dashboard token shape, and `/v1/auth/me` built in step 5
**Decision:** HS256 only, with the claims `sub` (user id), `tv` (token_version), `iat`, `exp`
(iat + 8 h), `iss=blastproof` and `aud=blastproof-dashboard`, all required. `exp`, `iat` and the
claim types are checked in our code (exact expiry, 30 s leeway for `iat` in the future), so tests
can pass a fixed `now`. Every request reloads the user, so role, `is_active` and `token_version`
changes apply at once. `POST /v1/auth/login` returns `{access_token, token_type, expires_at, user}`.
`GET /v1/auth/me` was pulled into step 5 (owner's choice); the Stage 3 pull-forward now covers only
the two actions routes.
**Why:** A token from another app sharing the secret cannot be replayed here. Returning the user
saves the dashboard's login route a second call.
**Rejected:** putting the role in the token (a demotion would wait up to 8 hours).

## D-093 · 2026-10-25 · The admin CLI
**Decision:** `uv run python -m app.cli create-user|create-agent` (stdlib `argparse`; no new
dependency). It connects as `blastproof_app`. A password is never an argument: `getpass` twice, or
one line from stdin with `--password-stdin` (non-interactive use and tests). Passwords are 12 to
256 characters, emails are stored lower case and checked with a small pattern, names are 1 to 100
characters. `create-agent` prints the key once on stdout; logs go to stderr. Errors are one line
with no traceback, and unexpected ones print only the exception type (a message could hold a URL).
**Why:** Shell history and process lists expose arguments. `email-validator` would be a new
dependency for a check the database does not depend on.
**Rejected:** Typer or Click (new dependency); `--password` as an argument.

## D-094 · 2026-10-25 · Validation errors use the envelope and never repeat the input
**Decision:** A `RequestValidationError` handler (`app/core/errors.py`) answers 422 with
`{"error": {"code": "validation_error", "message": "Invalid request: body.password: String should
have at most 256 characters."}}`: field locations and rule texts only, at most 5 errors and 400
characters. `/openapi.json` describes every 422 as `ErrorResponse`, and the default
`HTTPValidationError` schemas are removed, so the dashboard's generated types match. Brought forward
from step 19 (CONV-04); the 404 envelope stays in step 19.
**Why:** Found in the step 5 verify review: FastAPI's default 422 body includes each error's
`input`, so an over-long login password was sent back in the response, where browser devtools and
proxy error logs can keep it.
**Rejected:** waiting for step 19; removing the length limits from `LoginRequest` (argon2 would then
hash any size the client sends).

## D-095 · 2026-10-25 · No self sign-up and no email verification in the MVP
**Decision:** Dashboard accounts are created only by the owner (`create-user`) or, from step 19,
by an admin (`POST /v1/users`), with an initial password handed over directly. Login is email and
password only. Emails are login names; Blastproof never sends mail, so they are not verified. Try-it
for visitors stays the landing page and demo video (F13). A read-only demo account and Google
sign-in with an invite allowlist are candidates for after the MVP. Confirmed with the owner after
step 5.
**Why:** Every account's agents act on the one shared demo company database, every previewed write
costs LLM calls, and capacity is small (2 shadow runs, the Neon free plan's 10 branches, one Fly
machine). A stranger could approve a mass delete and break the demo for everyone. Safe self-serve
would need a company database per user, per-user spend caps and abuse handling. And login is not
the hard part of trying Blastproof: installing Claude Code, `claude mcp add` and the hook are.
**Rejected:** an open sign-up page; open Google sign-in (both let strangers act on the shared
database); email verification now (nothing sends mail, and accounts are admin-made).

## D-096 · 2026-10-25 · The MCP route goes into FastAPI's router; one MCP server per app
**Decision:** Step 6 builds the `MCPServer` inside `create_app()`, calls `streamable_http_app()`
and adds that app's single `/mcp` route to FastAPI's own router
(`app.router.routes.extend(mcp_app.routes)`), not `app.mount`. Our lifespan enters
`mcp.session_manager.run()` around the existing startup and shutdown. A test checks the SDK app
still has exactly one route, so an SDK change cannot drop routes silently.
**Why:** MCP spike (`spikes/mcp_mounting.md`, Q1, Q2): `app.mount("/", ...)` serves `POST /mcp`
but takes every unmatched path, so FastAPI's 404 handling (and step 19's envelope) never runs;
`app.mount("/mcp", ..., streamable_http_path="/")` answers `POST /mcp` with a 307 to `/mcp/`.
Without `session_manager.run()` a request fails with "Task group is not initialized", and `run()`
works once per server instance, while tests build many apps.
**Rejected:** either `app.mount` form; a module-level MCP server.

## D-097 · 2026-10-25 · `/mcp` runs stateless with SSE responses
**Decision:** `streamable_http_app(stateless_http=True, json_response=False)`. No MCP sessions, so
`actions.session_id` is null for Claude Code (BE §6 already allows null; the column stays for
Phase 2). Server-to-client requests (sampling, elicitation) are not used.
**Why:** Claude Code 2.1.310 speaks protocol 2026-07-28: every request had no `Mcp-Session-Id`,
against stateful and stateless servers alike. In stateful mode, which only older clients use, a
session opened with agent A's key accepted agent B's key (the tool saw B, but sessions were bound
to nobody); stateless mode has no sessions to reuse. SSE mode sends `: ping` every 15 s while a tool
runs (longest silence 15.4 s on a 35 s call); JSON mode sent nothing for 35.3 s. Fly's proxy drops
connections idle for about 60 s, and `get_action_status` long-polls for up to 50 s. A call that
ends within 15 s still gets a plain JSON response.
**Rejected:** stateful mode (sessions not bound to agents); JSON responses (the spike's first
draft: simpler to read in tests, but silent for the whole long poll).

## D-098 · 2026-10-25 · How a tool knows the agent: middleware, contextvar, fail closed
**Decision:** A pure-ASGI middleware on `/mcp` calls `authenticate_agent_key` (D-090) and answers
401 with `WWW-Authenticate: Bearer` and the error envelope on failure. On success it sets a
`contextvar` (the plan's mechanism) and also puts the agent in `scope["state"]`. Tools read the
contextvar and refuse the call if it is unset. The SDK's `TokenVerifier`/`AuthSettings` are not
used. Step 6's tests drive two agents' keys through the real middleware: interleaved, concurrent,
and a key change between calls.
**Why:** MCP spike Q4 and Q5: the contextvar, `ctx.request_context.request.state` and re-reading
`ctx.headers` all saw the key sent on that request, in every mode, sequentially and concurrently.
The contextvar reaching the tool depends on how the SDK schedules handlers, which its docs do not
promise, so the tests guard it and a missing agent fails closed. With a wrong key, Claude Code
reports `AUTH_HEADER_REJECTED, HTTP 401` with our message and does not try OAuth, because an
`Authorization` header is configured.
**Rejected:** the SDK's OAuth-style auth (adds resource-metadata routes we do not need); trusting
`ctx.headers` alone (the SDK docs say headers are never an identity; the middleware has already
checked the key).

## D-099 · 2026-10-25 · MCP DNS-rebinding allowlist from config
**Decision:** Step 6 passes `TransportSecuritySettings(allowed_hosts=[...], allowed_origins=[...])`
built from config: `localhost:*` and `127.0.0.1:*`, plus the host of `PUBLIC_BASE_URL` (exactly as
sent, without a port on 443). Step 6 adds `PUBLIC_BASE_URL` to `Settings` (BE §11 already lists it).
In-process tests use the base URL `http://localhost:8000`.
**Why:** MCP spike Q6: protection is on by default for a mounted app. Passes: `localhost:8000`,
`127.0.0.1:8000`. Rejected with 421: `localhost` with no port, `testserver` (Starlette's TestClient
default) and a fly.dev name until it is allowlisted. A foreign `Origin` gets 403.
**Rejected:** turning protection off; hard-coding the Fly hostname.

## D-100 · 2026-10-25 · MCP tools return their outcome text; unexpected errors never log raw text
**Decision:** Tools catch their expected failures and return the `agent_messages.py` template as a
normal result, not as `ToolError`. Unexpected exceptions are caught in the tool, logged through
structlog as a masked `reason_code` with `action_id`, and the agent gets the `failed` template.
Step 6 also stops the SDK's stdlib `mcp` logger from writing exception text (level raised or
routed through masking), with a test that a raising tool leaves no raw value in the captured logs.
**Why:** MCP spike Q9: a `ToolError` reaches the agent prefixed ("Error executing tool X: ..."),
which breaks the word-for-word templates. An unexpected exception reaches the agent only as "Error
executing tool X", but the SDK logs the full traceback at ERROR, and in the spike that held an email
address and a URL with a password. This extends the Known issue on exception text in logs.
**Rejected:** relying on the SDK's generic message (the leak is in our logs, not the reply).

## D-101 · 2026-10-25 · Claude Code's MCP timeouts allow the 20 s wait and the 50 s long poll
**Decision:** No change to `REVIEW_WAIT_SECONDS` (20) or the 50 s `get_action_status` maximum.
Any future wait must stay well under Claude Code's 5-minute idle timeout for HTTP servers. The
deploy step measures Fly's idle cut on the real machine with a 50 s long poll.
**Why:** MCP spike Q7: through Claude Code, 25 s and 55 s tool calls returned in stateful, stateless
and JSON modes. Claude Code's docs: tool wall-clock default about 28 hours (`MCP_TOOL_TIMEOUT`),
idle timeout 5 minutes for HTTP servers (reset by progress notifications), calls over 2 minutes
move to the background in interactive mode. The 60 s Fly figure comes from Fly's community forum,
not its docs.
**Rejected:** shortening the long poll now, without a measurement.

## D-102 · 2026-10-25 · The first tool call creates the main `systems` row
**Decision:** `ensure_main_system()` gets or creates one row named `company` with the configured
`COMPANY_CONNECTION_PROFILE` (`neon` by default, `local` for tests) and the optional
`NEON_PROJECT_ID`/`NEON_PARENT_BRANCH_ID`, using `INSERT ... ON CONFLICT (name) DO NOTHING` so two
first calls at once are safe. If the stored row's profile differs from the configuration, the call
fails closed (`event=system_profile_mismatch`, the agent gets `failed: internal_error`, no action is
recorded). Step 20's bootstrap reuses the row. Production refuses to start with the `local`
profile or without `COMPANY_DB_READER_URL`.
**Why:** `actions.system_id` is NOT NULL from step 4, but bootstrap is step 20. Attributing actions
from one company database to another database's row would corrupt the audit trail.
**Rejected:** creating the row at startup (startup must stay offline, TEST-07); a migration that
inserts it (the profile is runtime configuration).

## D-103 · 2026-10-25 · A temporary read check refuses anything but one plain SELECT
**Decision:** Until the step 8 classifier, `reads.check_read()` (sqlglot, Postgres dialect) accepts
exactly one `SELECT` or set operation (WITH, UNION, comments and a trailing `;` are fine) and
refuses the rest with the `not_supported` message ("Blastproof can only run a single SELECT for
now; writes are not supported yet. Nothing was changed."): writes, data-modifying CTEs,
`SELECT INTO`, `FOR UPDATE/SHARE`, `EXPLAIN` (ANALYZE runs the statement), opaque commands (`DO`,
`CALL`, `SET`, `BEGIN`, `COPY`, `TABLE x`), several statements, empty or unparseable SQL, more than
20 KB, and side-effect functions (`pg_terminate_backend`, `pg_cancel_backend`, `pg_advisory_*`,
`pg_try_advisory_*`, `set_config`, `nextval`, `setval`, `pg_notify`, `dblink*`, `lo_*`). It runs in
a worker thread capped at 2 s; a timeout or crash is a refusal. The refusal is recorded as `failed`
with `failure_reason=not_supported` and a short code (`check`) in the audit event, never the SQL.
Behind it, each on its own: the `blastproof_reader` role (SELECT only, no sequence rights), a
`READ ONLY` transaction and the 5 s statement timeout. `retry_of` is left out of `query_database`
until step 17 stores it, so the tool never accepts a link it drops.
**Why:** The plan says "refuses writes", which needs some classification before step 8. A test
calls `run_read` directly with an INSERT and with `nextval`, and Postgres refuses both.
**Rejected:** relying on the reader role alone (side-effect functions such as advisory locks need no
table privilege); a regex check.

## D-104 · 2026-10-25 · Read errors tell the agent what Postgres said; logs get the code only
**Decision:** A Postgres error on a read returns `query_error`: "The query failed: {primary
message} (SQLSTATE {code}). Nothing was changed." (no DETAIL, capped at 500 characters). The text
goes to the agent only; the action stores `failure_reason=query_error_<sqlstate>` and the log has
the same code. The statement timeout is `failed: statement_timeout`; a company database that is
unset, unreachable or refuses the login is `failed: company_db_unavailable` (the exception class is
logged, never its text). `describe_table` for an unknown name, or one outside `public` (such as
`blastproof.applied_actions`), returns `table_not_found`: "No table {name} was found. Call
list_tables to see the tables you can read." Read results go back to the agent unmasked and made
JSON-safe (timestamps ISO 8601; numeric, UUID and interval as text; bytea as hex; NaN as text);
masking covers logs, prompts and the UI (CLAUDE.md), and the reader role can read the rows anyway.
**Why:** The `failed` template carries only a reason code, so an agent could not fix a typo in a
table name. Postgres messages can quote values (`invalid input syntax ...: "<value>"`), which is
fine for the agent that sent the query but not for logs.
**Rejected:** `failed: query_error` with no text (the agent would guess); logging the message.

## D-105 · 2026-10-25 · `/ready` checks the two databases now; Neon joins in step 10
**Decision:** `GET /ready` checks Blastproof's database (`SELECT 1` through the app engine, so the
role check runs too) and the company database (a reader connection), at the same time, each with a
3 s timeout. All up: 200 `{"status": "ready", "checks": {"blastproof_db": "ok", "company_db":
"ok"}}`. Otherwise 503 with the envelope `not_ready` and a message naming the failed checks; the
log line `not_ready` has each check's exception class, never its text. BE-23 stays open until step
10 adds the Neon API check.
**Why:** There is no Neon client before step 10; a check of something unused would only add a
failure mode.
**Rejected:** calling the Neon API with httpx directly now (duplicates step 10's client).

## D-106 · 2026-10-25 · `get_action_status` is an owner-scoped lookup with no waiting yet
**Decision:** It looks the ID up through `visible_actions(owner_id, is_admin=False)` (D-055) and
returns `{action_id, status, decision, message}`. A failed action carries its `failed` message; an
unknown, malformed or another owner's ID gets `not_found`. `wait_seconds` (0 to 50, default 30) is
validated by the SDK but has no effect: in step 6 every action is final when its call returns. It
records nothing; `tool_called` is logged. `visible_actions` is a minimal `Select` builder in
`app/db/actions.py`, so the CLAUDE.md rule holds from the first action query.
**Why:** The long poll needs the workflow runner (step 15) and the remaining templates (step 17).
**Rejected:** leaving the tool out until step 17 (Claude Code would see the tool list change).

## D-107 · 2026-10-25 · The `/mcp` middleware: exact path, the same 401 as routes, 503 when it cannot check
**Decision:** `AgentAuthMiddleware` guards the path `/mcp` exactly (the spike matched
`startswith("/mcp")`, which would also catch `/mcpx`). A missing, malformed, unknown or deactivated
key, or a deactivated owner, gets 401 `invalid_agent_key` with "The agent key is invalid or
deactivated." and `WWW-Authenticate: Bearer`, the same code and text as `get_current_agent` (one
shared constant). No database configured, or any error while checking, gets 503 `unavailable`
(fail closed); the log has the exception class only. The session used for the check is closed
before the tool runs, and each tool opens its own short transactions. It is added before
`RequestIdMiddleware`, so the request ID stays outermost and auth failures carry `X-Request-ID`.
**Why:** One door, one answer for a bad key (D-088). A long read must not hold a Blastproof
connection.
**Rejected:** the spike's `not_authenticated` code (that is the dashboard token's code).

## D-108 · 2026-10-25 · Step 6 tests speak raw JSON-RPC; the real client is the manual check
**Decision:** Tests post raw 2026-07-28 `tools/call` requests (the `_meta` protocol fields and the
`mcp-protocol-version`, `mcp-method`, `mcp-name` headers, as in the spike) through Starlette's
`TestClient` with `base_url="http://localhost:8000"`, and parse the reply from SSE or JSON
(`tests/conftest.py`). The concurrency test runs nine overlapping calls with `asyncio.gather` over
`httpx.ASGITransport` (one event loop, as uvicorn serves them) and asserts they really overlapped
(under 2 s for nine 0.3 s calls), instead of threads. Which agent a tool saw is read back from the
`actions` row it recorded.
**Why:** Step 5 removed the direct `httpx2` import that the SDK `Client` needs. Threads sharing one
`TestClient` still funnel into one portal loop, and would not prove overlap.
**Rejected:** adding `httpx2` as a direct dependency for tests.

## D-109 · 2026-10-25 · Company reads open one connection per call
**Decision:** `company_db.connect_reader()` opens one asyncpg connection per tool call (no pool),
with `TimeZone=UTC`, `extra_float_digits=1` and `statement_timeout=5s` as startup settings and a 10 s
connect timeout, plus a 20 s backstop over the whole read. `COMPANY_CONNECTION_PROFILE` picks
`COMPANY_DB_READER_URL` (`neon`) or `LOCAL_COMPANY_READER_URL` (`local`). URLs holding
`channel_binding` are refused at startup.
**Why:** It matches D-019 for `local` and keeps step 6 small. On Neon each call pays for TLS and a
possible cold start; pooling for `neon` can come later (Known issue).
**Rejected:** a pool now (lifecycle, Neon's idle closes and per-connection settings to reset, for a
dev-only latency gain).

## D-110 · 2026-10-25 · No library writes raw values into our logs
**Decision:** The JSON log handler carries a filter for `mcp.*` records: it keeps the bare format
string, drops arguments, traceback (`exc_info`, `exc_text`) and `stack_info`, and adds `exc_type`.
The `mcp` logger is set to WARNING (its INFO "Tool %r failed: %r" carries the error text) and
`sqlglot` to ERROR: sqlglot warns "'<sql>' contains unsupported syntax" with up to 100 characters
of the agent's SQL (found in step 6). Our tools log exception classes only. `Settings` now uses
`hide_input_in_errors=True`, because Pydantic's errors echo the rejected value: a too-short
`JWT_SECRET` or a reader URL with `channel_binding` would have been printed at startup (found in
step 6). `MCPServer()`'s `logging.basicConfig(RichHandler)` does nothing, because `create_app`
installs our handler first; a test checks no RichHandler is left on the root logger.
**Why:** D-100 and SEC-14. Each guard has a test that fails without it.
**Rejected:** masking the text with patterns (keeping an allowlist of fields is safer than a list
of what to hide).

## D-111 · 2026-10-25 · MCP tool result shapes and argument limits
**Decision:** Each tool returns a Pydantic model (`app/schemas/mcp.py`), which the SDK sends as
structured content plus the same JSON as text: `QueryDatabaseResult{action_id, status, columns,
rows, row_count, truncated, message}`, `TableList{action_id, status, tables[{name, description}],
message}`, `TableDescription{action_id, status, table, description, columns[{name, type, nullable,
description}], message}` and `ActionStatusResult{action_id, status, decision, message}`. `status` is
`executed` or `failed` for the three recorded tools. `action_id` is null only when nothing was
recorded (no agent in context, or the Blastproof database failed). Argument limits checked by the
SDK before our code: `sql` 1 to 100,000 characters (the real 20 KB cap is `check_read`'s, so that
refusal is recorded), `intent` 1 to 2,000, a table name 1 to 200, an action ID up to 100. A call
that fails SDK validation never reaches our code and is not recorded. The three read-only tools
carry `readOnlyHint`.
**Why:** Rows as lists keep the reply small; the agent gets the column names once.
**Rejected:** rows as objects (each column name repeated up to 500 times).

## D-112 · 2026-10-25 · `visible_actions` scopes reads made for someone; status repeats the original message
**Decision:** The CLAUDE.md rule "all action queries go through `visible_actions(user)`" covers every
read of actions made on behalf of a user or an agent (the dashboard, `get_action_status`, `retry_of`).
Internal writes that act on an action by the ID the same code path created or was handed by the
workflow (`create_action`, `set_status`, later the runner and commit) address it by primary key and
are not filtered by owner. Also, refining D-106: `get_action_status` on a failed action repeats the
message the agent got when it ran (`not_supported`, `table_not_found` with the stored table name);
a query error comes back as `failed: query_error_<sqlstate>`, because its Postgres text is never
stored (D-104). The longest `wait_seconds` is `STATUS_MAX_WAIT_SECONDS` (default 50), not a literal.
**Why:** Found in the step 6 self-review. An owner filter on an internal update protects nothing (the
ID did not come from a caller) and would get in the way of later system actors (the reconciler,
expiry, an admin approving). The leak `visible_actions` guards against is a caller reading another
user's action.
**Rejected:** passing an owner ID into every internal update; leaving the literal 50 next to an
existing setting.

## D-113 · 2026-10-25 · The step 18 hook entry sets `onFailure: block` and a short `timeout`
**Decision:** The `PreToolUse` entry for `claude_hook/pre_tool_use.py` carries `"onFailure": "block"` and a `timeout` of a few seconds. The script itself exits 2 only to deny on purpose.
**Why:** Hook spike: a hook that timed out (10 s sleep, `timeout: 3`) or crashed (exit 1) let the Bash call run; with `onFailure: block` both were blocked. A missing executable also ran (exit 127). A `python` command on a missing script exits 2, which blocks even without it, so exit codes alone are not a safe contract.
**Rejected:** relying on the script's own try/except (it cannot cut off a hang, and a startup failure never reaches it).

## D-114 · 2026-10-25 · Hook matcher covers PowerShell and Monitor, drops MultiEdit
**Decision:** Matcher `Bash|PowerShell|Monitor|Edit|Write|NotebookEdit|Read|Grep|Glob`. Shell-like tools fail closed when Blastproof is unreachable.
**Why:** The hook fired for PowerShell (active by default on Windows) and Monitor (runs a command); `MultiEdit` is not in Claude Code 2.1.310's tool list.
**Rejected:** keeping the old list (a PowerShell `psql` would skip the hook); a `*` matcher (Blastproof would see tools it has no rule for).

## D-115 · 2026-10-25 · `permissions.deny` syntax for the demo workspace
**Decision:** `Read(//d/<repo path>/backend/**)`, `Read(//**/.env)`, `Read(//**/.env.*)`, `Edit(.claude/**)` in the workspace's project settings. `//d/` and `//D/` both work, and a space in the path is fine.
**Why:** A Read deny blocked Read, Grep, Glob, `cat` and PowerShell `Get-Content` on the path, and holds in bypass mode. `Edit(.claude/**)` stopped the agent writing `settings.local.json` with `disableAllHooks`, which otherwise switches its hook off. `python -c open(...)` still reads the file: the accepted dev limitation (D-002) stands.
**Rejected:** relying on hooks alone for `.env` (the agent can disable a hook); counting the deny rule as the boundary (credential isolation is).

## D-116 · 2026-10-25 · Keep the command hook script; do not switch to an `http` hook
**Decision:** Step 18 stays a Python command hook.
**Why:** The `http` hook works (JSON deny, `onFailure`), but its `$VAR` header is silently empty (`Bearer`) unless `allowedEnvVars` lists it, and it cannot do the retry-once and local read deny-list fallback BE-103 needs.
**Rejected:** an `http` hook calling `/v1/hook/pre-tool-use` directly (revisit if the fallback is dropped).

## D-117 · 2026-10-25 · The demo workspace denies the whole repo, not only `backend/`
**Decision:** `Read(//d/<repo>/**)` plus credential-file rules (`.env*`, `.pgpass`, `pgpass.conf`, neonctl, `~/.ssh`, `~/.claude.json`), `Edit(.claude/**)`, `Edit(.mcp.json)`, and `permissions.blockReadsOutsideWorkingDirectories: true`. Supersedes D-115's `backend/**` rule.
**Why:** The repo root's `docker-compose.yml` holds the local superuser passwords, and BE-24 asks for credential files.
**Rejected:** denying only `backend/**`.

## D-118 · 2026-10-25 · Workspace tooling is `claude_hook/workspace.py`, key through stdin
**Decision:** One stdlib script creates, scans and verifies the workspace at `D:/My Workplace/blastproof-demo` (a sibling of the repo). The agent key arrives on stdin and is written only to `.claude/settings.local.json`; `.mcp.json` uses `${BLASTPROOF_AGENT_KEY}`, which Claude Code expands from that `env` block (checked with a fake key). `check` and `verify` print names and PASS/FAIL, never values; `verify` reads a canary before the real `.env`.
**Why:** Keeps the key out of argv, history and output, and makes DW-B07 repeatable.

## D-119 · 2026-10-25 · Local Docker databases stay reachable from agent code (known issue)
**Decision:** An agent can still use `python -c` against `localhost` with the guessable dev passwords. Recorded as part of D-002; fixing it needs random Compose passwords or a devcontainer. The deployed demo is not affected.

## D-120 · 2026-10-25 · `workspace.py verify` uses Sonnet for the deny checks
**Decision:** The canary and `.env` Read checks run `claude -p --model sonnet`; the environment check stays on Haiku.
**Why:** Haiku declined to call Read on a `.env` path at all, so there was no tool result to judge. A check with no result fails closed rather than passing.
**Rejected:** counting "no tool call" as a pass (proves nothing about the deny rule); a pass-through retry loop.

## D-121 · 2026-10-25 · Gate audit fixes: Docker, connectors, Neon key scan, `check_read` functions
**Decision:** (1) The demo workspace denies `docker` in Bash and PowerShell, because `docker exec` into `company-db` gives a superuser shell with no password (D-119 named only `python -c`). (2) Its settings set `disableClaudeAiConnectors: true`, so no claude.ai connector (a Neon or Postgres one, say) reaches the agent; `enabledMcpjsonServers` already limits project servers. (3) `workspace.py check` also flags a `NEON_API_KEY` variable. (4) `check_read` refuses `query_to_xml`, `cursor_to_xml` (they run SQL given as text, so they got past the function list), `pg_read_*`, `pg_ls_*` and `pg_stat_file`; `pg_sleep` stays allowed, the 5 s timeout stops it.
**Why:** Gate audit findings F1 to F4. All are dev-machine guardrails except `check_read`, which runs in production too. They are guardrails, not the boundary: `python -c` against the local passwords still works (D-119), and the deployed demo is the real isolation check.
**Rejected:** random Compose passwords now (a larger change, left to Deploy); scanning for `neonctl` (it matches the workspace's own deny rule); an allowlist of read functions now (step 8's classifier owns it).


## D-122 · 2026-10-25 · Pinning splices the tokenizer's character spans and re-parses to check
**Decision:** `pinned_sql` is the original text with each clock function replaced by `('<UTC instant>'::<type>)`, found by the tokenizer's character offsets (a `pg_catalog.` prefix and a `(p)` precision are part of the span). The result is parsed again and must give the same kind, statement types, tables, schemas and flags (all but `non_deterministic`) with no clock function left; otherwise the statement is `unknown` with reason `pin_check_failed`. `current_time` and `localtime` are pinned too, as `timetz` and `time`. DDL and unknown statements are not pinned (a `DEFAULT now()` must stay a default). Special strings (`'now'::timestamptz`, `today`, `tomorrow`, `yesterday`) cannot be pinned reliably, so they count as volatile: they set `non_deterministic`, and in a WHERE they set `volatile_in_where`. A bare `'now'` with no cast is only caught in fast-lane values (known gap: Postgres converts it implicitly in a comparison).
**Why:** Regenerating SQL from sqlglot's tree changes it: `current_time` becomes `CURRENT_TIME()`, which Postgres rejects, and `current_timestamp(3)` loses its precision. Splicing keeps every other byte, so `pinned_sql == sql` when there is nothing to pin. The re-parse is the fail-closed backstop for any splice mistake (tested by patching the splice). Pinned values are UTC wall-clock values because every company connection sets `TimeZone=UTC` (D-018, D-109); the integration test compares `pg_typeof` and values on `company-db`.
**Rejected:** `tree.sql()` (above); pinning special strings.

## D-123 · 2026-10-25 · Classifier limits: 500-node simplify bound, dedicated thread pool, byte cap
**Decision:** `has_where` is `simplify(where) is TRUE` only when the WHERE has at most 500 nodes. Above that, only the flattened top-level OR terms that name no column or subquery are simplified; what that misses is caught by the measured `fraction_of_table_gte` in pass 2. `classify_in_worker` runs `classify` on its own `ThreadPoolExecutor(max_workers=4, thread_name_prefix="classifier")` with `asyncio.wait_for(..., 2)`. Size is counted in bytes (20 KB), before any parsing. `sql_display` for an over-size input is built from its first 20 KB only.
**Why:** Measured on sqlglot 30.24.0: `simplify` is roughly quadratic (100 OR terms 114 ms, 400 terms 1.9 s, a 20 KB chain 39 s) and holds the GIL, so the 2 s cap would block the SQL but not free the thread; a separate pool keeps a runaway parse from starving argon2, which uses the default executor. Parsing the same 20 KB takes 0.06 s, and 3,000 nested parentheses raise `RecursionError` quickly (caught: `unparseable`). The first email-masking regex was cubic on a 20 KB run of letters and hung a test; it now finds an address from its `@` outward in bounded steps, and the phone regex is bounded to 40 characters (a test masks 20 KB of several fillers in under a second).
**Rejected:** a process pool (heavier, and the cap already blocks the SQL); no bound on simplify.

## D-124 · 2026-10-25 · Classification by allowlist; side-effect functions; one label fixed
**Decision:** (1) Kind comes from an allowlist of root types: `Select`/`SetOperation` read; DML, `TRUNCATE` and transaction control write; `Create`/`Drop`/`Alter`/`Grant`/`Revoke`/`Comment` and `SELECT ... INTO` ddl; everything else unknown (`Command`, `Set`, `Copy`, `FOR UPDATE`, bare `VALUES`, `TABLE x`). Transaction control is found from each statement's leading keywords, because sqlglot parses `START TRANSACTION`, `SAVEPOINT a` and `ABORT` as plain expressions. (2) Functions follow an allowlist (`SAFE_ANONYMOUS_FUNCTIONS`, `pg_sleep` on purpose): an untyped function not on it is unknown, counts as volatile, and makes a read a write. Typed functions pass unless the side-effect list names them. `check_read`'s side-effect list moved to `sql_functions.py` (with `setval`, `pg_reload_conf`, replication slots and others added); `nextval` is both a side effect and volatile. `check_read` itself is untouched until step 17. (3) `side_effect_functions` and `target_tables` are new fields. (4) The scenario `touch-applied-actions` now also lists "Delete or update without WHERE": it has no WHERE, so that rule really matches, and the old label named only one of the two.
**Why:** Fail closed (D-040, D-121 F4). Proposed for step 9, not done here: a rule key `side_effect_function: true` -> block, because no rule in backend plan section 9 blocks `terminate-backends` (labeled block, but the rules as written would send it to review); and a clearer reason for `unknown` than "Unparseable SQL", since `VACUUM` and `FOR UPDATE` parse fine ("Blastproof could not parse or does not support this statement"). The risk of the allowlist is a legitimate function missing from it; the list is generous and a test covers common ones.
**Rejected:** a deny list of statement types (what sqlglot calls `Command` is open-ended); letting reads skip the function check.

## D-125 · 2026-10-25 · Fast lane allows no RETURNING at all
**Decision:** `fast_lane_eligible` requires one `INSERT ... VALUES` of 1 to 10 rows with no WITH, ON CONFLICT, RETURNING or subquery anywhere, and values that are only literals, NULL, booleans, DEFAULT, `-n`, casts of those, or pinned clock functions. Any other function, including `upper()` and `gen_random_uuid()`, makes it ineligible.
**Why:** Backend plan step 8 says "no RETURNING beyond the primary key", but the classifier has no schema and cannot tell which column is the key. The stricter rule sends such inserts to preview, which is safe. Which tables may use the fast lane is still decided only by rules (step 9).
**Rejected:** guessing that `RETURNING id` is the primary key.

## D-126 · 2026-10-25 · `sql_display` masks by column name as well as by value shape
**Decision:** `sql_display` is built from `pinned_sql`'s tokens. A string literal that looks like an email or phone becomes `'[email]'` or `'[phone]'`; one tied to a sensitive-named column (email, phone, ssn, address, token, password, secret) in an INSERT column position, a SET assignment, or a comparison, IN or LIKE becomes `'[masked]'`; comments are masked by substring; SQL that will not tokenize goes through the regex alone. `'%@example.com'`, ISO dates and UUIDs are left alone. The heuristics live in `services/masking.py` for steps 9 to 12.
**Why:** The plan names the column heuristic only for step 9, but a password inserted into `password_hash` does not look like an email or phone. Masking too much is the safe direction; a date or ID that looks like a phone number may be masked, and that is accepted.
**Rejected:** masking only by value shape; reading the sensitive column list from `rules.yaml` now (step 9).

## D-127 · 2026-10-25 · Known limits of the classifier
**Decision:** Recorded, not fixed: (a) an `ORDER BY` on a non-unique key with `LIMIT` still picks tied rows differently each time, which needs the schema, so step 16's key-set check catches it as drift; (b) names without a schema resolve to `public` (safe because every call opens a new connection and `SET` is refused); (c) `COMMIT PREPARED` and `RELEASE SAVEPOINT` do not parse in sqlglot, so they are `unknown` and blocked; (d) the actions inside a MERGE are not counted as a DELETE or UPDATE with no WHERE.
**Why:** Each is either safe in the blocking direction or needs data the classifier does not have.

## D-128 · 2026-10-25 · A "No side-effect functions" rule
**Decision:** `rules.yaml` gains the SQL key `side_effect_function` and the block rule "No side-effect functions", placed after "Hands off Blastproof's schema". The scenario `terminate-backends` is now `decidable_from_sql: true` with `expected_rules: [No side-effect functions]`. The shipped file is the plan's 15 rules plus this one.
**Why:** No rule in backend plan section 9 blocked `pg_terminate_backend`, so it would have gone to review and been shadow-run (D-124). The classifier already records the function; a rule is the only place an outcome comes from.
**Rejected:** a code-level block outside the rules file (outcomes come only from rules); leaving the scenario to the judge.

## D-129 · 2026-10-25 · The file is checked hard at load
**Decision:** `load_policy` rejects duplicate YAML keys (PyYAML silently keeps the last), unknown keys and fields, strings where a bool or number belongs, unknown statement types and kinds, repeated rule names, empty reasons, and a `fast_lane: true` rule that is not an allow with `tables_any` or that also uses a diff key (pass 1 would skip it forever). A derived-group condition must be one boolean expression of bare columns, literals, comparisons, IN, IS [NOT] NULL, AND/OR/NOT and parentheses: no functions, subqueries, casts or `;`. Errors name the rule and key and never repeat the input value.
**Why:** The file decides what reaches a customer database. A typo that silently drops a block rule is the same as removing it. Derived-group SQL is later run against the company database, so it is limited to what is safe to run.
**Rejected:** accepting YAML as parsed; sandboxing derived-group SQL further (the file is trusted owner config).

## D-130 · 2026-10-25 · How each condition is read
**Decision:** `statement_in` matches any statement type in the tree (a CTE DELETE counts). `tables_any` and `schemas_any` match any listed name using the classifier's labels. `rows_affected` sums deleted, updated and inserted over every changed table, cascades included. `fraction_of_table` is (deleted + updated) / `rows_before` per table, the maximum over changed tables, compared exactly with `Fraction` (500 of 1000 is not above 0.5). `derived_group_gt` needs every entry to hold; a group on an unchanged table counts 0; a changed table with no count raises `PolicyFactsError`. Pass 1 gives `FAST_LANE` only if the write is eligible, a matched allow rule has `fast_lane: true`, and no review matched. Pass 2: block over review over allow, and no match means review.
**Why:** Each takes the stricter reading when the plan was silent. Bad or missing facts raise so the caller blocks (fail closed); the policy never queries a database.

## D-131 · 2026-10-25 · Known limits of the rules, and the MERGE gap closed
**Decision:** "Every row of a table" and "Most of a table" now list `merge` as well as `delete` and `update`, so a MERGE that deletes or rewrites rows is held to the same fractions (a test covers both). Still recorded, not fixed, and pinned by tests: (a) there is no code backstop, so removing a block rule such as "Unparseable SQL" from `rules.yaml` removes that block (the shipped-file test fails if a rule goes missing); (b) "Small insert" matches a CTE DELETE feeding an INSERT of 10 rows or fewer (allow, then the judge runs; deleted rows count toward the 10, the other rules still apply, and only the fast lane skips the judge and it cannot hold a CTE).
**Why:** The MERGE hole was one word to close and only made the rules stricter. A backstop would put outcomes outside the rules file, against "outcomes come only from rules"; the CTE case is bounded by the row total and the judge.
**Rejected:** a code backstop for block rules; a negation key to exclude CTE deletes.

## D-132 · 2026-10-25 · Column check is a pure function; startup does not query
**Decision:** `check_columns(policy, columns_by_table)` raises `PolicyFileError` naming a missing table or column in `important_columns`, `derived_groups` or `sensitive_columns`. Startup stays offline (TEST-07), so it only validates the file's shape; step 10's shadow run reads `information_schema` and calls `check_columns`, failing closed. An integration test runs it against `company_main`.
**Why:** The plan said "startup validates that every column exists", but startup must not touch the network or a database. The check is the same; it runs where the connection already exists.
