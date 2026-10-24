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
