# Plan audit: Blastproof spec, backend plan and frontend plan

Oct 24, 2026 · Prompt 0A (see `docs/WORKFLOW.md`)

Sources reviewed: `CLAUDE.md`, `docs/SPEC.md`, `docs/backend-plan.md`, `docs/frontend-plan.md`,
`PROGRESS.md`. No code was written. Live library documentation was not checked; the items in
section 4 come from prior knowledge, and each names the docs to verify against.

References: **SPEC** = `docs/SPEC.md`, **BE** = `docs/backend-plan.md`, **FE** = `docs/frontend-plan.md`.
Within each section, items are ranked from most to least damaging.

## Fix first (overall top 8)

1. Agents can still get the database credentials locally (5.1)
2. The demo DELETE will fail at commit because of `now()` (2.1)
3. The drift check has no data to compare against (3.1)
4. Commits are not idempotent and the approve race is open (5.2)
5. The demo schema's foreign keys can break the headline DELETE (2.2)
6. Demo step 1 cannot be auto-approved, and writes cannot meet the 5 s target (1.1)
7. The `actions` table can't feed the pages that read it (3.3)
8. The audit log is not actually append-only (5.3)

---

## 1. Contradictions

### 1.1 [Critical] Demo step 1 cannot work, and the latency target is unreachable

SPEC §17 step 1, "Add a note to customer 42", should be auto-approved instantly.

- The schema in BE step 3 has no notes table or column.
- BE step 9 sends every write that matches no rule to review. The only `allow` rule is "Small insert".
- BE step 13 runs the judge on allowed writes, and every write also gets a Neon branch and a Haiku summary.
- So even an approved write costs a branch, a compute cold start and two LLM calls, which conflicts
  with SPEC §10's target of under 5 s per write.

**Decide:** an insertable notes table, an allow rule for it, and whether the judge or summary is
skipped for rule-allowed small writes.

### 1.2 [High] Incident-replay scope

These sources disagree:

- SPEC §14: the Replit, PocketOS and Terraform replays "must be caught".
- SPEC §17 demo steps 3, 5 and 6: cloud API delete, undo, eval dashboard.
- CLAUDE.md: "incident replays must stay at 100%".
- BE §14 lists only: mass delete, DROP, delete without WHERE, `psql`, settings edit.
- BE has no cloud API tool, and `hook_rules` allows `curl -X DELETE https://api.<cloud>/volumes/...`.

**Decide:** what "incident replays" means for the MVP, and trim the SPEC demo to match.

### 1.3 [High] Who can create agents

- Admin-only: FE §1 ("admins manage agents and knowledge") and FE §16 ("Agents, systems and
  knowledge changes are now admin-only").
- Any signed-in user: BE §6 (`POST /v1/agents`), FE §5, FE §7 and FE F11.

### 1.4 [High] DDL handling

- SPEC §8 and SPEC §7 module 4 say schema changes are previewed by a shadow run.
- BE step 8 classifies statements as `ddl`, but no rule handles it, so DDL defaults to review.
- BE §12 says `blastproof_writer` has no DDL rights.
- So approved DDL fails on the shadow branch and again at commit.

**Decide:** block all DDL in the MVP (recommended), and add the rule.

### 1.5 [Med] Per-agent scoping

SPEC §6 step 2 and SPEC §13 say each agent has "a scoped list of allowed systems and tools". The
BE §5 data model has no agent-to-system or agent-to-tool mapping.

### 1.6 [Med] Local stack and test database

- SPEC §16: Compose brings up "the full stack plus a demo company database".
- BE §15: Compose has only `blastproof-db`, and the company database lives on Neon.
- BE §14 integration tests need "a local fake that copies a test database", but no test company
  database is defined anywhere.

### 1.7 [Med] Dev and prod share one company database

- CLAUDE.md: "Only connect to the development Neon projects."
- BE §2 creates one `blastproof-company` project, used by local dev and by the Fly deployment.
  Local commits change the production demo data.

### 1.8 [Med] Users have no name

- BE §5 `users` has id, email, password_hash, role, and no name.
- These all need a name: BE §6 `/auth/me` returns one, the FE §12 Playwright test checks "your
  name as the decider", and the 409 response "names who decided".
- BE §5 also says to avoid exposing email.

### 1.9 [Med] Status vocabulary

BE §5 lists `awaiting_review` and has no `drifted`, `expired` or `pending`. But:

- BE step 16 "marks the action drifted".
- BE step 15 auto-rejects timed-out reviews, so `decided_by` is null.
- BE step 20 and FE §12 seed status "pending".
- The MCP tool returns `pending`.

### 1.10 [Med] Single-statement and transaction rule

CLAUDE.md says one statement per write and to reject BEGIN/COMMIT/ROLLBACK. BE step 8 only *flags*
`multi_statement`; no rule in step 9 blocks it, and transaction control isn't mentioned.

### 1.11 [Low] Next.js route layout

- FE §6 uses a real `app/dashboard/` segment and `app/middleware.ts`.
- FE F4 says a "`(dashboard)` layout", which is a route group and adds nothing to the URL.
- Middleware can't live under `app/` (see 4.5).

### 1.12 [Low] LangSmith trace for every action

BE §17 requires one. Reads (fast path, no LLM) and hook calls produce no LangSmith run.

### 1.13 [Low] Small drift between the spec and plans

- Models: SPEC §12 says Sonnet for summaries; BE §3 says Haiku.
- Polling: SPEC §12 says 3–5 s; FE Overview uses 15 s.
- Injection checks: SPEC §7 checks tool inputs and *outputs*; BE step 13 checks inputs only.
- Secrets: SPEC §13 calls for a secrets manager; BE uses env vars, and SPEC was never updated.
- CD: SPEC §16 has staging then production; BE deploys `main` straight to production.
- File name: CLAUDE.md says `docs/spec.md`, but the file is `docs/SPEC.md`, which breaks on a
  case-sensitive CI.

## 2. Ambiguous enough that two engineers would build different things

### 2.1 [Critical] What happens on drift (BE step 16)

- The demo query will almost certainly be `WHERE last_login_at < now() - interval '6 months'`.
- `now()` moves between preview and commit, so users cross the boundary and the row count no
  longer matches. The commit rolls back and goes "back through preview".
- Not specified: whether the reviewer must approve again; what the agent is told; how many loops
  are allowed; whether `non_deterministic` (flagged in step 8) does anything at all.
- SPEC §19 says "pin values", but no step does it.

**Fix:** rewrite `now()` to a pinned timestamp literal during preview, and commit the rewritten SQL.
Show both versions to the reviewer.

### 2.2 [Critical] Demo schema foreign keys (BE steps 3 and 10)

- Only `orders → users` is declared `ON DELETE CASCADE`.
- `order_items`, `subscriptions`, `payments`, `invoices` and `support_tickets` all reference users
  or orders. Any `RESTRICT` among them makes the headline DELETE fail with an FK error.
- Cascade detection by "count every table before and after" also misses `SET NULL`,
  `ON UPDATE CASCADE`, triggers, and tables where inserts and deletes cancel out.

**Specify:** the FK action for every child table, and how cascades are detected (suggested:
`pg_constraint` plus count deltas).

### 2.3 [High] Policy rule language (BE step 9)

- The list of condition keys isn't defined.
- Unclear what `rows_affected_gt` means in the pre-shadow pass: false, skipped, or unknown.
- Unclear whether a pre-shadow `block` skips the shadow run.
- Unclear whether auto-approval needs a positive `allow` match, or just no stricter match.
- `has_where: false` is defeated by `WHERE true`.

### 2.4 [High] When the judge runs and what it can do (BE step 13)

- Not stated whether it runs on writes that rules already blocked.
- "May raise allow to review": can it raise to block?
- How `confidence` is used is not stated.
- What a judge error or timeout produces, block or review, is not stated.

### 2.5 [High] How the agent learns the outcome (BE §6, step 17)

- `get_action_status` doesn't say whether it returns at once or waits (long-poll).
- Claude Code won't poll by itself; the turn just ends with "pending". So the demo's "reason
  reaches Claude, which rewrites" depends on the user nudging it.
- Nothing links the rewritten query to the rejected one: `session_id` has no defined source
  (MCP session header or Claude Code session ID?).

### 2.6 [High] Hook calls as actions (BE step 18)

- Not stated whether each Bash, Edit or Write call creates an `actions` row, with what status, or
  whether it shows in the dashboard. Every Bash call adds up fast.
- "rm -rf outside workspace" is ambiguous: regex, shlex or bashlex parsing? How are paths
  normalised (`..`, symlinks, Windows drive letters)?

### 2.7 [Med] How UPDATE previews get before-values (BE step 10)

"First select the matching rows" means rewriting `UPDATE … FROM … JOIN` into a SELECT. Also
unhandled: statements that already have `RETURNING`, writes inside CTEs, and tables with no
primary key.

### 2.8 [Med] Number guardrail (BE step 12)

Not defined:

- how numbers are normalised (`48,213` vs `48213`, percentages, spelled-out numbers);
- whether numbers taken from the intent ("6 months"), the rules ("1,000") or the knowledge text
  count as facts.

### 2.9 [Med] Where "sensitive" columns come from (BE §12, CLAUDE.md)

- No source is named: `rules.yaml` has `important_columns` but no `sensitive_columns`. A name
  heuristic? Schema docs?
- Literals inside `actions.sql` (e.g. `WHERE email='…'`) reach the UI and prompts unmasked.

### 2.10 [Med] `GET /v1/actions/{id}` and `impact` have no defined shape

- FE §7 needs: intent, headline, key points, concerns, per-table counts with cascades, breakdown,
  samples, judge result plus which judge ran, matched rules, timeline.
- The frontend will be built against guesses until this is written down.

### 2.11 [Low] Knowledge tagging

No step says how `schema_docs.md` headings map to tables, or how uploaded files get table tags.

### 2.12 [Low] Agent identity inside MCP tools

No step says how the key's agent and owner get from the middleware into the FastMCP tool: a
contextvar, or reading the request from `Context`?

## 3. "Done when" checks that no step builds

### 3.1 [Critical] The drift check has nothing to compare against (BE step 16)

- Step 16 compares locked rows with "the shadow run's before rows".
- But step 10's `Diff` keeps only primary keys plus 50 sample rows, `before_images` are written
  *at commit*, and the branch is deleted right after the shadow run.
- Nothing persists the full before-rows between preview and commit, which can be up to 24 h.

### 3.2 [High] BE step 12: "including 12 paying customers (3 enterprise)"

- 12 is a derived total (pro 9 + enterprise 3). "Paying" comes from a business rule in the RAG
  text, not from code.
- No step computes derived groups, so the guardrail in step 12 would *reject* "12".
- Needs a code-level definition, e.g. a `derived_groups` entry in `rules.yaml` like
  `paying: plan in (pro, enterprise)`.

### 3.3 [High] FE F9, plus FE §7 detail

- FE F9 needs "LLM cost today" and "average preview time" to match a manual SQL count.
- `actions` has no cost, token or duration columns. Cost only exists in LangSmith.
- Also missing from BE §5 `actions`, though FE §7 uses all of them:
  - `intent`, which the review queue shows;
  - the reviewer's reject reason, which the agent needs (`reason` would otherwise mix policy and
    reviewer meanings);
  - matched rules;
  - judge provider;
  - the structured impact report fields.

### 3.4 [High] FE F7, the FE §12 Playwright test and BE step 19 approve seeded actions

- `seed-demo-actions` (BE step 20) creates rows "without Neon or an LLM", so no LangGraph thread
  exists.
- `Command(resume=…)` therefore has nothing to resume.

**Decide:** seeding runs a fake-node graph up to `interrupt()`, or decisions on seeded actions don't
resume anything.

### 3.5 [Med] BE step 15 "restart then approve still commits" needs step 16

Step 15's check needs step 16's commit. BE step 14's "review then approve" branch also needs a
commit node, so the order calls for a stub.

### 3.6 [Med] FE F11 "a new key works by copy-pasting"

- The hook also needs `pre_tool_use.py` and `.claude/settings.json` in the workspace, and nothing
  installs them.
- The frontend only knows `BLASTPROOF_API_URL`, which is server-only and possibly an internal
  address. It has no public MCP URL to pre-fill.

### 3.7 [Med] FE F10 "an uploaded rule changes the next impact report"

- Upload chunks have no table tags (2.11), so they only come back through the top-5 vector search
  on the intent.
- The effect also depends on LLM wording, and a frontend step now needs the whole Neon and LLM
  pipeline.

### 3.8 [Low] Other gaps

- BE §17 and step 20 say "a fresh clone reaches a working demo by following the README", but no
  step writes the README or setup guide.
- BE step 6 `describe_table` returns "notes from knowledge", which doesn't exist until step 11.

## 4. Library and API assumptions to verify

### 4.1 [Critical] Claude Code hooks

- **Return value:** exit 2 with stderr to deny. For "allow", output nothing; don't emit JSON
  `permissionDecision: "allow"`, because that skips Claude Code's own permission prompts.
- **Timeouts:** check the default hook timeout and what a timeout does. A timeout is likely a
  non-blocking error, which would mean fail-*open*. The script needs its own HTTP timeout shorter
  than the hook's.
- **Tool names:** the matcher `Bash|Edit|Write` leaves out `Read`, `NotebookEdit` and possibly
  `MultiEdit`.
- **Hook protection:** check how hooks are snapshotted at startup, `disableAllHooks`, and settings
  precedence (`settings.local.json`, `~/.claude/settings.json`).
- **Bypass mode:** check the `bypassPermissions` behaviour.
- **Docs:** code.claude.com/docs/en/hooks, /settings, /iam.

### 4.2 [High] MCP Python SDK

- Whether FastMCP still lives at `mcp.server.fastmcp` in the current major version.
- `streamable_http_app()` already serves at `/mcp` by default, so mounting it at `/mcp` gives
  `/mcp/mcp`.
- The lifespan needs `session_manager.run()`.
- Stateless vs session mode.
- How a tool reads request headers.
- The SDK's built-in `TokenVerifier` vs custom middleware.
- **Docs:** python-sdk README, MCP spec "Authorization", and Claude Code's `claude mcp add --header`
  docs. Use `--scope local` so the key isn't written into `.mcp.json`.

### 4.3 [High] LangGraph Postgres checkpointer

- It uses **psycopg 3**, not asyncpg. That means a new dependency, a plain `postgresql://` URL,
  `autocommit=True` and `dict_row`.
- `setup()` needs DDL rights (conflicts with 5.3).
- On resume, `interrupt()` re-runs the node from the start, so side effects before the interrupt
  happen twice.
- **Docs:** LangGraph "persistence" and "interrupts" (v1.x).

### 4.4 [High] LangChain `init_chat_model`

It lives in the `langchain` package. BE §3 lists only `langchain-anthropic`, so this is a
dependency to approve.

### 4.5 [Med] Next.js 16

- `middleware.ts` was renamed to `proxy.ts` (Node runtime) and must sit at the `src/` root. That
  name collides with the plan's "proxy" route handler.
- A nonce-based CSP (FE §11) forces dynamic rendering, which conflicts with a static landing page
  (F13).
- **Docs:** nextjs.org file conventions (`proxy`), CSP guide.

### 4.6 [Med] Neon API

- The create-branch payload, polling of `operations`, and building connection URIs for a
  non-owner role.
- Whether branch `expires_at` exists; it could replace the startup cleanup.
- Free-plan limits on branches and concurrent computes (pricing changed in 2025).
- The default Postgres version (Compose pins pg16).
- If PG18 is available, `RETURNING old.*, new.*` removes 2.7 entirely.
- **Docs:** api-docs.neon.tech, Neon plans page.

### 4.7 [Med] asyncpg `execute(sql)`

Without arguments it uses the simple query protocol, which runs *multiple statements*. Use `fetch`
or prepared statements, and enforce one statement via sqlglot. **Docs:** asyncpg API reference.

### 4.8 [Med] Fly.io defaults

`fly launch` creates 2 machines with auto-stop on, which kills in-process asyncio tasks and the
sweeper and adds cold starts to hook calls. **Docs:** fly.toml reference, autostop.

### 4.9 [Low] shadcn `toast`

It's deprecated in favour of `sonner`; check `form` too. **Docs:** ui.shadcn.com.

### 4.10 [Low] Jev on Vercel AI Gateway

Vercel AI Gateway's documented endpoints are OpenAI-compatible chat endpoints; a `/v1/evaluate`
endpoint is unconfirmed. Verify before designing `JevJudge`. Post-MVP, so low priority.

## 5. Missing pieces for a production MVP

### 5.1 [Critical] Agents can still get the database credentials locally

- `backend/.env` is on the same disk as the demo workspace.
- The Read tool isn't hooked, so `Read("D:/My Workplace/Blastproof/backend/.env")` works.
- Bash deny-lists are easy to get around (`cat .e""nv`, `python -c`). That is exactly the PocketOS
  pattern, and BE step 7's grep check won't notice.

**Needs:**

- the backend running as another OS user or in Docker with the env injected;
- `Read`, `Grep` and `Glob` added to the hook matcher;
- Claude Code `permissions.deny` path rules.

### 5.2 [Critical] Commits are not idempotent and the approve race is open

- **Crash mid-commit:** if the process dies between the company-database commit and the status
  update, nothing resumes or reconciles the action. It sits in `approved` forever, or a retry
  inserts twice. SPEC §10 requires "no action applied twice".
  **Needs:** an `action_id` marker table in the company database, written in the same transaction,
  plus a startup reconciler.
- **Simultaneous approvals:** two approvals at once can both call `Command(resume)`.
  **Needs:** a conditional `UPDATE … WHERE status='awaiting_review' RETURNING` before resuming.
- **Two databases, one "transaction":** BE step 16 calls the commit "one transaction", but it spans
  two databases. The order of writes needs to be specified.

### 5.3 [High] The audit log is not actually append-only, and the roles are mixed

- There is one `BLASTPROOF_DATABASE_URL`, so migrations and the app run as the role that *owns*
  `audit_events`. REVOKE from the owner is cosmetic, and the local Compose user is a superuser.

**Needs:** separate migrator and app roles. Run the checkpointer `setup()` in a migration.

### 5.4 [High] Background work on multiple machines

The sweeper and startup branch cleanup run on every machine. **Needs:** a single pinned machine with
autostop off, or a Postgres advisory lock. Also keep references to `create_task` handles so tasks
aren't garbage-collected.

### 5.5 [High] Shadow-run concurrency

Branch and compute caps make concurrent writes fail in confusing ways. **Needs:** a semaphore plus a
clear fail-closed "busy" response.

### 5.6 [Med] Timeouts and budgets

- LLM calls: per-call timeout, max tokens and a per-action cost cap. What the agent is told when
  the provider is down: all writes blocked by design, but say so.
- Commit on production: `lock_timeout` and `statement_timeout`; a 48k-row `FOR UPDATE` lock on
  production also needs one.
- Input limits: maximum SQL size and a sqlglot parse time limit.

### 5.7 [Med] Account basics

- Login rate limiting and lockout.
- Password change.
- User deactivation (`users` has no `is_active`).
- JWT revocation (e.g. a `token_version`).

### 5.8 [Med] PII retention

- `before_images` hold raw PII forever: that needs a retention policy, and the masking rule must
  explicitly exempt them.
- Add LangSmith client-side masking as a second layer behind prompt masking.

### 5.9 [Med] Test company database

CI and the Playwright workflow (FE §13) need a local company database in Compose and a defined Neon
fake. Backend startup must not call Neon, or download models, when `APP_ENV=test`.

### 5.10 [Low] Operations

- Backups and point-in-time restore for the Blastproof database; the audit log is the product's
  record.
- A readiness check covering the company database and the Neon API, not just the Blastproof
  database.
- A metric or log event for every fail-closed block.
