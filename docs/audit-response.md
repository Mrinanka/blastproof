# Audit response: proposed decisions

> **Historical record. Do not implement from this file.** The plans and `docs/decisions.md` are
> the source of truth. Rows marked *[superseded]* were changed when applied or by the follow-up
> review (D-017 to D-028).

Oct 24, 2026 · Status: **APPROVED and applied** (2026-10-24). Every "Now" row has been written
into the spec, the plans, CLAUDE.md and WORKFLOW, and logged in `docs/decisions.md` (D-001 to
D-016). Where this file and the plans differ, the plans win. Owner's answers to section 6:
(1) isolation only on the deployed demo, with dev on guardrails (D-002); (2) judge failure → review;
(3) all CASCADE plus SET NULL; (4) demo trimmed to the MVP.

Changes made while applying:

- **P1:** no devcontainer in dev. Dev instead gets its own Neon project, because branches share
  role passwords with their parent (D-003).
- **2.3:** "more than 50% of a table" goes to review, and only "every row" blocks. Blocking at 50%
  would have blocked the demo's own mass delete (D-009).
- **2.6:** hook events are stored, and their dashboard view is deferred.

Responds to `docs/plan-audit.md`. Item numbers like (5.1) refer to that file.

## 1. Verdict

The audit is accurate. Nothing in it kills the project, but about a third of it is architecture that
must be settled on paper now, because it changes the data model, the API contract or how the
security boundary works. Those are cheap to fix in a doc and expensive to fix after steps 4, 10 and 16.
The rest are step-level details. Each one goes into the step that builds it, or is checked by a spike.

Four design principles resolve most of the Critical and High items together. Adopt them first, and
the item-by-item fixes in section 3 mostly fall out of them.

## 2. Four principles that resolve the criticals

### P1. The security boundary is the process, not the hook (fixes 5.1, informs 2.6, 4.1)

Hooks and deny-lists are guardrails: they make the agent's experience clear and catch honest
mistakes. They are **not** a security boundary. `cat .e""nv`, `python -c`, symlinks and new tool
names will always find gaps. The real boundary is that **no credential exists anywhere the agent
process can read**.

- *[superseded by D-002/D-003: no devcontainer; dev runs on guardrails with its own Neon project]* **Dev:** Claude Code runs in a Docker devcontainer that mounts only `demo_workspace/`. The
  backend's `.env` is not in its filesystem at all. Native Claude Code sandboxing isn't available on
  Windows, so a devcontainer is the portable option. Check the Claude Code devcontainer docs in the
  hook spike.
- **Demo recording:** the agent talks to the *deployed* backend (Fly.io). The demo machine has no
  company credentials at all, which is also the strongest thing to show on video.
- **Defence in depth, still built:** hook matcher widened to `Bash|Edit|Write|MultiEdit|NotebookEdit|Read|Grep|Glob`,
  plus `permissions.deny` path rules in the workspace settings.
- **Spec change:** SPEC §13 "Hook bypass" mitigation is rewritten so it no longer suggests the hook
  is the barrier. This is a good interview answer: "hooks are UX; isolation is security."

### P2. Measure effects, don't predict them (fixes 2.2, 2.7, 3.1, most of 2.3)

Don't use sqlglot to *predict* what a write touches (rewriting `UPDATE … FROM` into SELECTs,
guessing cascades). On the disposable branch, **diff the tables themselves**:

*[superseded in detail by D-018: snapshots stay in temp tables on the branch, the write runs in a transaction that is rolled back, and only differences are returned]*

1. Before running the write: for every table in the company schema,
   `SELECT pk, md5(row_to_json(t)::text) FROM t`.
2. Run the (pinned, see P3) statement.
3. Take the same snapshot again. Rows that disappeared were deleted, new rows were inserted, and rows
   whose hash changed were updated.

This catches CTE writes, `UPDATE … FROM`, `ON DELETE CASCADE`, `SET NULL`, triggers and
`ON UPDATE CASCADE` with no special cases. At demo scale (about 50k users, about 10 tables) it costs
tens of milliseconds.

- **Rule:** every company table must have a primary key. A table without one is blocked in the MVP.
- **Persist** `(table, pk, before_hash)` for every affected row in a new `preview_rows` table. That
  is the data the drift check was missing (3.1): about 48k rows × about 60 bytes, which is fine.
- **Limitation (decisions.md):** full-table hashing scales to a few million rows. Beyond that, the
  next step is limiting to FK-reachable tables, or logical decoding on the branch.

### P3. Approve exactly what was previewed (fixes 2.1, 5.2, the rest of 3.1)

- **Pin non-determinism at classification:** rewrite `now()`, `current_timestamp`, `current_date`,
  `localtimestamp`, `statement_timestamp()` and `clock_timestamp()` to one timestamp literal taken
  at intercept time. The **rewritten SQL is what is previewed, shown, approved and committed**. The
  reviewer sees both the original and the pinned version.
- `random()`, `nextval()` and volatile functions inside a WHERE are **blocked**. Inside INSERT
  values they are allowed, and inserts are drift-checked by count, not by PK.
- *[superseded in detail by D-021 and D-022 and backend plan step 16: cascade check by FK path, before-images saved before the company COMMIT, re-run instead of fail]* **Commit, as one transaction on the company DB:**
  1. `SET LOCAL lock_timeout = '3s'` and `SET LOCAL statement_timeout = '30s'`.
  2. `INSERT INTO blastproof.applied_actions(action_id) … ON CONFLICT DO NOTHING RETURNING`. No row
     back means the action was already applied, so stop (idempotency).
  3. `SELECT * … WHERE pk = ANY($approved) FOR UPDATE`. Hash and compare with `preview_rows`. These
     rows are also the before-images.
  4. Run the pinned SQL `RETURNING pk`. The returned PK set must equal the approved set, which
     catches new matching rows (phantoms).
  5. Compare cascade counts with the preview.
  6. COMMIT, then write the Blastproof-side status, before-images and audit event.
- *[superseded by D-022: with no marker row, re-run the commit]* **Crash recovery:** a `committing`
  status, plus a startup reconciler. If the marker row exists in the company DB, mark the action
  committed; if it doesn't, mark it failed and tell the owner.
- **Approve race:** `UPDATE actions SET status='approved' … WHERE id=$1 AND status='awaiting_review' RETURNING`
  runs before `Command(resume)`. Zero rows back means 409.
- **On drift:** roll back, mark the action `drifted`, re-preview once automatically, and send it
  back to review with "data changed since you approved: was X, now Y". A second drift fails the
  action and tells the agent to retry. Never auto-approve after drift.
- `blastproof.applied_actions` lives in its own schema. The policy blocks any agent SQL that touches
  the `blastproof` schema.

### P4. A fast lane for trivially safe writes (fixes 1.1)

The demo opens with "Add a note to customer 42 → instant". A branch plus two LLM calls cannot be
instant, so:

- Add a `customer_notes` table to the demo schema.
- **Fast lane:** an `INSERT … VALUES` (no SELECT, no `ON CONFLICT DO UPDATE`, ≤ 10 rows, counted from
  the VALUES list) into a table listed under `fast_lane_tables` in `rules.yaml` *[superseded by D-024:
  literals only, and the allow rule is the single source of truth; there is no `fast_lane_tables`]*. It skips the shadow
  run, the judge and the LLM summary. It gets a template summary and commits with `RETURNING`
  count = expected. Target: under 300 ms.
- Every other write goes through the full path. The **"under 5 s" target becomes "measured in the
  Neon spike"**. Branch creation plus compute start is the unknown, so record the real p50/p95 and
  set the target from that.

## 3. Item-by-item decisions

**Where** = which document or step the fix lands in. **When:** *Now* = patch the plans before step 1;
*Step N* = add to that step's text so its Prompt 1 picks it up; *Spike* = verify against live docs.

### Contradictions

| # | Decision | Where | When |
|---|---|---|---|
| 1.1 | P4 fast lane plus `customer_notes`; latency target set from spike data | SPEC §10, §17; BE step 3, 9 | Now |
| 1.2 | MVP incident replays = Replit mass delete (plus 4 SQL and hook variants already listed), plus a **PocketOS-lite**: the agent hunts for credentials and finds none or is denied. Real cloud-API, Terraform and injection replays move to Gate 3. SPEC §17 demo trimmed to steps 1, 2, a bypass attempt, and the numbers; steps 3–5 are marked "Phase 2". CLAUDE.md wording becomes "MVP incident replays must stay at 100%". | SPEC §14, §17; BE §14; CLAUDE.md | Now |
| 1.3 | **Any signed-in user creates their own agents** (matches SPEC §4). Fix FE §1 and FE §16. | FE §1, §16 | Now |
| 1.4 | Block all DDL in the MVP, with an explicit rule | BE step 9 rules.yaml; SPEC §8 note | Now |
| 1.5 | Deferred: MVP has one system; all agents may use it. Note it in SPEC §13. | SPEC §13 | Now |
| 1.6 / 1.7 / 5.9 | *[superseded by D-003 and D-019: dev is a separate Neon project, not a branch; the template is `company_template`, never connected, with `company_main` cloned from it]* Three company DBs. **Test/CI:** a `company-db` service in Compose, with a `FakeNeonClient` that "branches" via `CREATE DATABASE shadow_x TEMPLATE company_test` (real copy semantics, no network). **Dev:** a Neon `dev` branch in `blastproof-company`, used as the parent of dev shadow branches. **Prod demo:** Neon `main`, touched only by the deployed backend. Add `NEON_PARENT_BRANCH_ID` per environment. | BE §2, §11, §14, §15 | Now |
| 1.8 | Add `users.display_name`. The API exposes the name, never the email, to other users. | BE §5 | Now |
| 1.9 | One canonical state machine (below). "pending" is the MCP word for `awaiting_review` and is never stored. | BE §5 | Now |
| 1.10 | Multi-statement SQL and any transaction-control statement → pre-shadow **block** rule | BE step 8, 9 | Now |
| 1.11 | A real `app/dashboard/` segment; Next 16 `src/proxy.ts` handles the auth redirect; the catch-all route handler is called the "BFF route" in the docs to avoid the name clash | FE §4, §6, F3, F4 | Now |
| 1.12 | "Every **write** has a LangSmith trace; reads and hook calls have structured logs" | BE §17 | Now |
| 1.13 | Haiku for summaries (SPEC updated); review queue 5 s and overview 15 s (SPEC says "queue 3–5 s"); output-side injection checks deferred to the session-patterns phase; MVP secrets = env vars / Fly secrets (SPEC updated); MVP CD = main → prod (SPEC says staging is Phase 2); `git mv docs/SPEC.md docs/spec.md` | SPEC; repo | Now |

**Status state machine (1.9):**

```text
write:  received → classified → previewing → awaiting_review → approved → committing → committed
                                    │               │   │                       │
                                    ├→ blocked      │   ├→ rejected             ├→ drifted → previewing (once)
                                    └→ failed       │   └→ expired (timeout)    └→ failed
fast lane / auto-allow: classified → approved → committing → committed
read:   received → executed | failed
```

### Ambiguities

| # | Decision | Where | When |
|---|---|---|---|
| 2.1 | P3 pinning | BE step 8, 16 | Now |
| 2.2 | Demo FKs: every child of `users`/`orders` uses `ON DELETE CASCADE` except `invoices.user_id` → `SET NULL` (shows a non-delete cascade in the report). Detection via the P2 diff, not FK analysis. | BE step 3, 10 | Now |
| 2.3 | *[superseded by D-009: over 50% goes to review and only "every row" blocks; derived groups are per table (D-023)]* **Rule semantics:** documented condition keys (`statement_in`, `tables_any`, `has_where`, `rows_affected_gt/lte`, `fraction_of_table_gt`, `cascade_tables_any`, `derived_group_gt`, `touches_schema`). Pass 1 evaluates SQL-only keys; a rule with diff-dependent keys is *skipped* in pass 1, not false. A pass-1 block skips the shadow run. **Auto-approve needs a positive allow match AND no stricter match**; the default is review. New rule: `fraction_of_table_gt: 0.5 → block`, which catches `WHERE true`, `WHERE id > 0` and every other way around `has_where`. | BE step 9 | Now |
| 2.4 | **Judge:** runs only on writes not already blocked; may raise to review **or block**; `confidence < 0.6` → at least review; confidence is otherwise logged for calibration evals. **Judge error or timeout → review**, never allow. **Shadow run or diff failure → block.** Rule of thumb: *no facts → block; facts but no AI opinion → review.* Fail-closed is defined as "never applied without a human". | BE step 13; CLAUDE.md wording | Now |
| 2.5 | *[amended by D-020: 10-minute polling budget]* `get_action_status(action_id, wait_seconds≤50)` long-polls. The pending message tells the agent to keep calling it until decided. `query_database` takes an optional `retry_of` action_id; the reject message says "pass retry_of=<id>". The dashboard shows the chain. `session_id` = MCP session ID when present, else null (sessions matter in Phase 2). Check Claude Code's MCP tool timeout in the MCP spike. | BE §6, step 17 | Now (contract) |
| 2.6 | *[superseded by D-014: no dashboard view in the MVP; D-026 adds the offline read fallback]* Hook calls go to a separate `hook_events` table (tool, summary, decision, reason, agent, user), not `actions`. The dashboard shows only denies in MVP. Rules use `shlex` plus normalised paths, documented as best-effort guardrails (P1). | BE §5, step 18 | Now (table) |
| 2.7 | Solved by P2 | — | — |
| 2.8 | Numbers are extracted with a regex, commas stripped. Allowed set = facts ∪ numbers in intent ∪ rule thresholds. Percentages must be computed in code and put in the facts. The prompt requires digits only. | BE step 12 | Step 12 |
| 2.9 | `sensitive_columns` per table in `rules.yaml` ∪ a name heuristic (email, phone, ssn, address, token, password). Stored `actions.sql` is raw (needed for commit) and never returned. The API, UI and prompts get `sql_display`, with string literals that look like PII masked. | BE §5, §12, step 12 | Now (column) |
| 2.10 | Write the `ActionDetail` Pydantic schema into BE §6 now. It is the FE/BE contract. | BE §6 | Now |
| 2.11 | `schema_docs.md` headings use `## table: users`. Uploads take an optional `tables` field. | BE step 11, FE F10 | Step 11 |
| 2.12 | A `contextvar` set by the `/mcp` auth middleware, read by the tools. Confirmed or replaced in the MCP spike. | BE step 6 | Spike |

### Unbuilt "Done when" dependencies

| # | Decision | Where | When |
|---|---|---|---|
| 3.1 | P2 `preview_rows` plus P3 | BE §5, 10, 16 | Now |
| 3.2 | `derived_groups: { paying: "plan in ('pro','enterprise')" }` in `rules.yaml`, counted in code | BE step 9, 12 | Now |
| 3.3 | `actions` gains: `intent`, `sql_display`, `pinned_sql`, `policy_reason`, `reviewer_note`, `matched_rules` (jsonb), `judge` (jsonb: provider, model, result), `impact_report` (jsonb), `preview_ms`, `total_ms`, `llm_input_tokens`, `llm_output_tokens`, `llm_cost_usd` (tokens from LangChain `usage_metadata` × a price table in config: code counts), `retry_of`, `expires_at` | BE §5 | Now |
| 3.4 | `seed-demo-actions` runs the **real graph** with `FakeNeonClient` + fake LLM up to `interrupt()`, so seeded actions are resumable and the Playwright test exercises the real path | BE step 20 (pulled forward to Stage 3) | Now |
| 3.5 | Step 14 includes a stub commit node; step 15's restart check runs after step 16 | BE step 14–16 | Now |
| 3.6 | Backend config `PUBLIC_BASE_URL`; `POST /v1/agents` returns `mcp_url` and the ready-made setup snippets; hook files installed by copying `claude_hook/` (documented) | BE §6, §11; FE F11 | Step 19 |
| 3.7 | F10 "Done when" becomes deterministic: an uploaded doc tagged `users` appears in the action detail's "knowledge used" list for the next users action | FE F10; `ActionDetail.knowledge_used` | Now |
| 3.8 | README / setup guide added to step 20; `describe_table` returns only the schema until step 11 | BE step 6, 20 | Now |

### Library assumptions (section 4): all go to spikes

The spikes already in `WORKFLOW.md` cover these. Two changes:

- **Move the Claude Code hook spike to Stage 1**, before step 7, because P1's isolation design
  depends on it (devcontainer, matcher names, timeout behaviour, `disableAllHooks`, settings
  precedence, bypass mode). Step 18 stays in Stage 4.
- **Add explicit questions to each spike:**
  - **MCP:** mount path, lifespan, header access, Claude Code MCP tool timeout.
  - **Neon:** branch p50/p95, PG version and whether PG18 `RETURNING OLD` is available, free-plan
    branch and compute caps, `expires_at`.
  - **LangGraph:** psycopg3 setup, `setup()` run from a migration, node re-run on resume.
  - **Next 16:** `proxy.ts`, and CSP vs a static landing page. Fix: apply CSP to `/dashboard/*`
    only, so the landing page stays static.

### Production gaps (section 5)

| # | Decision | Where | When |
|---|---|---|---|
| 5.1 | P1 | SPEC §13; BE step 7, 18; WORKFLOW | Now |
| 5.2 | P3 | BE step 15, 16 | Now |
| 5.3 | Separate roles: `blastproof_owner` (migrations, `setup()`) and `blastproof_app` (runtime, INSERT/SELECT only on `audit_events`). **Plus** a `BEFORE UPDATE OR DELETE` trigger that raises an error. Compose init script creates both roles; two URLs in config. | BE §5, §11, §15, step 4 | Now |
| 5.4 | Fly: one machine, `auto_stop_machines = "off"`, `min_machines_running = 1`. Sweeper and reconciler also take a Postgres advisory lock. Task handles are kept in a set. | BE §15, step 15 | Step 15 |
| 5.5 | `MAX_CONCURRENT_SHADOWS=2` semaphore; wait ≤ 10 s, then fail closed with "busy, retry". | BE step 10 | Step 10 |
| 5.6 | Config table of limits: LLM timeout 20 s, max tokens, per-action cost cap; SQL ≤ 20 KB; sqlglot parse in a thread with a 2 s cap; commit timeouts from P3. When the LLM provider is down, the agent is told "held for review: AI check unavailable". | BE §11, §12 | Now (table) |
| 5.7 | MVP: `users.is_active`, `users.token_version` (in the JWT; checked per request), login rate limit (Postgres counter: 5 per 15 min per email+IP). Password change deferred. | BE §5, step 5 | Now (columns) |
| 5.8 | `before_images` and `preview_rows` retention of 30 days (sweeper); the masking rule explicitly exempts before-images, which never leave the backend; LangSmith client-side masking via `hide_inputs`/anonymizer as a second layer | BE §12 | Step 13 |
| 5.10 | `/ready` checks Blastproof DB, company DB and Neon API; `/health` stays liveness-only. Every fail-closed event logs `event=fail_closed` with a reason code. | BE §13 | Step 1 / 15 |

## 4. What the audit missed

1. **SPEC §18 says write the incident replays first; the backend plan builds them in Stage 4.**
   Fix: in step 3, write `evals/scenarios/*.yaml` (sql, intent, expected decision, expected counts)
   alongside the demo DB. Steps 8, 9, 10 and 13 each run the scenarios that apply to them. This
   gives Claude Code an objective target from day one and is the most valuable change for agentic
   coding in this list.
2. **No step says what the agent sees for each outcome.** The text returned to the agent is the
   product's UX for half its users. Add one table to BE §6: outcome → exact message template
   (committed, blocked, pending, rejected with reason, drifted, busy, expired, AI unavailable).
3. **Schedule honesty.** Applying this adds about 3–4 days, mostly in steps 3, 10 and 16. Put it into
   week 5's buffer rather than cutting scope.

## 5. New dependencies to approve (CLAUDE.md: ask first)

| Package | Why | Needed by |
|---|---|---|
| `psycopg[binary]` | The LangGraph Postgres checkpointer requires psycopg 3 (4.3) | Step 14 |
| `langchain` | `init_chat_model` lives here, not in `langchain-anthropic` (4.4) | Step 12 |

No other new packages: `shlex`, `hashlib` and `contextvars` are stdlib.

## 6. Genuine choices for the owner

*[answered 2026-10-24; see the status note at the top and D-002, D-008, D-010, D-011]*

Each has a recommendation; only these need an explicit answer, and everything else can be approved
as a block.

1. **Agent isolation in dev (P1):** devcontainer (recommended) or "dev with a weaker boundary;
   only the deployed demo is truly isolated"?
2. **Judge failure (2.4):** review (recommended) or block?
3. **Demo FK design (2.2):** all CASCADE plus one SET NULL (recommended), or include one RESTRICT so
   the agent also hits an FK error?
4. **Demo scope (1.2):** trim SPEC §17 to the MVP (recommended), or keep PocketOS in the MVP by
   adding a fake `cloud_api` MCP tool (about 2 extra days)?

## 7. Next steps, in order

*[done through step 2; the current next step is in `PROGRESS.md`]*

1. Owner answers section 6 and approves (or edits) the rest.
2. One session applies every "Now" row to SPEC, BE, FE, CLAUDE.md and WORKFLOW (spike move), and
   appends each decision to `docs/decisions.md`. "Step N" rows are written into that step's text.
3. Owner reads the diff of the plans, then commits: `docs: apply plan audit decisions`.
4. Outside setup (BE §2, FE §2), now including the Neon `dev` branch.
5. Prompt 0B (`REQUIREMENTS.md`), run against the fixed plans, in a fresh session.
6. Stage 1, step 1.
