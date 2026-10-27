# Blastproof: Backend MVP Plan

Oct 24, 2026 · @Mrinanka Dhar

This plan takes the Blastproof backend from an empty repo to the MVP gate: the Replit-style incident caught end to end, in Claude Code, against a fake company database.

## 1. MVP scope

One FastAPI backend that intercepts Claude Code's database actions, previews writes on a Neon branch, explains the impact in plain language, routes each write to auto-approve, review or block, and commits approved changes with an audit trail.

**In the MVP:**

- MCP server with database tools, protected by per-agent API keys.
- Claude Code `PreToolUse` hook endpoint with rule-based checks for shell commands and file edits (no sandbox yet).
- SQL classifier, YAML policy rules, Neon shadow runs with row-level diffs.
- RAG over schema docs and business rules; LLM impact summary; LLM risk judge for gray areas.
- LangGraph approval workflow with human-in-the-loop pause and Postgres checkpointing.
- Commit with drift check, saved before-images and an append-only audit log.
- REST API for the dashboard: login, review queue, approve/reject, history, knowledge upload.
- LangSmith tracing, structured logs, an eval suite in CI, and a deployment to Fly.io.

**Not in the MVP** (see section 17): undo button, Docker sandboxes, memory, email/Slack actions, session pattern detection, Terraform, Grafana, Redis.

## 2. Outside setup

Do these before writing code; each takes a few minutes. Keep every key in a password manager, never in the repo. Confirm current free-tier limits when you sign up.

**Accounts and API keys:**

- [ ] **GitHub:** a new repo `blastproof`, with GitHub Actions enabled.
- [ ] **Google AI Studio** (aistudio.google.com): create a Gemini API key (`GOOGLE_API_KEY`) for the default provider (D-056; any provider LangChain supports also works, with its own key). Set a monthly project spend cap (e.g. $50).
- [ ] **Neon** (neon.tech): three projects. `blastproof-company` holds the fake company database for the deployed demo (the one agents act on there, and the one we branch). `blastproof-company-dev` is an identical copy for local development, so dev credentials can never touch the demo's data (Neon branches share role passwords with their parent, so a dev *branch* would not give that separation). `blastproof-app` holds Blastproof's own database for production. For each company project, note the project ID and its main branch ID (Neon names the default branch `production`; "main branch" in these plans means it, D-057), and create an API key scoped to that project if your plan offers project-scoped keys (check in the Neon spike; otherwise use separate keys and treat the dev key as dev-only). Create the `blastproof-company` key at deploy time, straight into Fly.io secrets, so it never sits on the dev machine (D-058). All three projects use Postgres 18 in the same region (D-057). Free plan in October 2026: 100 projects, 10 branches and 1 GB storage per project.
- [ ] **LangSmith** (smith.langchain.com): create an API key and a project named `blastproof-dev`.
- [ ] **Embeddings:** a free local model via `fastembed` (`BAAI/bge-small-en-v1.5`, no key needed; D-059). Voyage AI (needs an API key) was the alternative.
- [ ] **Fly.io** (fly.io): account and the `flyctl` CLI, for deployment in the last week.
- [ ] **Vercel AI Gateway** (vercel.com, needed right after the MVP for the Jev judge): enable AI Gateway, create an API key, and add a payment card, which the Gateway requires before serving requests.

**Tools on your machine:**

- [ ] Python 3.13 and `uv` (package manager) (D-060).
- [ ] Docker Desktop (runs Blastproof's local Postgres).
- [ ] Node.js and Claude Code (the agent you will protect).
- [ ] `psql` or a GUI like TablePlus to inspect databases.
- [ ] An editor with Ruff support.


## 3. Tech stack and libraries

Every library below is a common industry choice with good documentation; nothing exotic.

| Purpose | Library / tool | Notes |
| --- | --- | --- |
| Web framework | FastAPI + Uvicorn | REST API, hook endpoint, hosts the MCP server |
| MCP server | `mcp` (official Python SDK v2, `MCPServer`; D-063) | Streamable HTTP transport, stateless with SSE responses, its `/mcp` route added to FastAPI's router (D-096, D-097) |
| Settings | `pydantic-settings` | Reads environment variables into a typed config object |
| Blastproof DB access | SQLAlchemy 2.0 (async) + `asyncpg` | Models and queries for Blastproof's own tables |
| Migrations | Alembic | Versioned schema changes |
| Company DB access | `asyncpg` (raw SQL) | Runs agent SQL on Neon branches and main |
| SQL parsing | `sqlglot` | Classify statements, find tables, add `RETURNING *` |
| Neon API | `httpx` | Create and delete branches |
| LLM calls | `langchain` + `langchain-google-genai` | `init_chat_model` lives in `langchain`; models are created from provider:model strings in config, so any provider works (add e.g. langchain-anthropic or langchain-openai); structured output into Pydantic models |
| Workflow | `langgraph` + `langgraph-checkpoint-postgres` + `psycopg[binary]` | Approval flow with interrupt and Postgres checkpoints; the checkpointer uses psycopg 3, not asyncpg |
| Vectors | `pgvector` (extension + Python package) | Default vector store behind a VectorStore interface; qdrant-client for the swappable Qdrant implementation |
| Embeddings | `fastembed` (or `voyageai`) | Local free model by default |
| Tracing | `langsmith` | Automatic for LangChain and LangGraph calls |
| Auth | `pyjwt`, `pwdlib` (argon2) | Dashboard login tokens and password hashing |
| Policy files | `pyyaml` | Loads rules from policies/rules.yaml |
| Logging | `structlog` | JSON logs with `action_id` on every line |
| Seed data | `faker` | Realistic fake company data |
| Testing | `pytest`, `pytest-asyncio`, `httpx` test client | Unit, integration and eval tests |
| Quality | `ruff` (lint + format), `mypy` (optional) | Run in CI |

**Models:** defaults are Gemini Flash for impact summaries (fast, cheap) and Gemini Pro for the LLM judge (better reasoning) (D-056; exact model names are picked from current docs in steps 12 and 13), but both are provider:model strings in config (SUMMARY\_MODEL, JUDGE\_MODEL), so Claude, GPT or others work with no code change. Rule: run the full eval suite with a new model and compare before switching. The embedding model is the exception: changing it means re-embedding every chunk and possibly a migration for the vector size, so pick it once. The risk judge is pluggable: Jev (TypeSafe AI), called through Vercel AI Gateway's HTTP API with httpx, is the second implementation.

## 4. Project structure

A standard layered layout: routes are thin, business logic lives in `services`, database code lives in `db`. Each folder has one job.

```text
blastproof/
  backend/
    app/
      main.py              # creates FastAPI app, adds the MCP route, adds routers
      config.py            # Settings from environment variables
      logging.py           # structlog setup
      api/                 # REST routes (thin): auth, actions, knowledge, agents, hook, health
      mcp/                 # MCP server and tool definitions
      core/                # security (API keys, JWT, passwords), errors, dependencies
      db/                  # SQLAlchemy models, session, repositories
      services/
        classifier.py      # sqlglot: read / write / ddl / unknown
        policy.py          # loads YAML rules, returns decision + reason
        neon.py            # Neon API client: create / delete branch
        shadow.py          # runs SQL on a branch, builds the diff
        knowledge.py       # schema scan, chunking, retrieval
        embeddings.py      # Embedder interface + fastembed
        vectorstores/      # VectorStore interface, pgvector.py, qdrant.py
        impact.py          # breakdown counts + LLM summary
        judge.py           # LLM risk judge
        commit.py          # drift check, before-image, apply, reconciler
        hook_rules.py      # shell and file rules for Claude Code
        agent_messages.py  # the exact text returned to agents for each outcome
        audit.py           # writes audit events
      workflow/
        graph.py           # LangGraph state, nodes, edges
        runner.py          # start / resume workflows, wait for result
      schemas/             # Pydantic request and response models
    migrations/            # Alembic
    policies/rules.yaml    # policy rules
    tests/                 # unit, integration
    evals/
      scenarios/           # incident replays + labeled actions (YAML), written in step 3
      run.py               # eval runner
    Dockerfile
    pyproject.toml
  frontend/                # Next.js dashboard (frontend plan)
  demo_company/            # schema.sql, seed.py, schema_docs.md
  claude_hook/             # pre_tool_use.py + example .claude/settings.json
  docker/                  # Postgres init scripts (roles)
  docker-compose.yml
  .github/workflows/ci.yml
```

## 5. Data model

Blastproof's own database has eleven tables, plus the checkpoint tables LangGraph creates for itself. The company database is untouched by this schema except for one small `blastproof` schema (below).

| Table | Key columns | Purpose |
| --- | --- | --- |
| `users` | id, email, display\_name, password\_hash, role (admin, member), is\_active, token\_version, created\_at | Dashboard logins. Other users only ever see `display_name`, never the email. Bumping `token_version` revokes every JWT for that user |
| `login_attempts` | id, email, ip, succeeded, created\_at | Login rate limiting: 5 failures per 15 minutes per email and IP, plus 20 failures per hour per email from any IP (so rotating IPs doesn't help) |
| `agents` | id, owner\_id, name, api\_key\_hash, key\_prefix, is\_active, created\_at | One row per agent, owned by one user; only the hash of the key is stored |
| `systems` | id, name, kind (postgres), connection\_profile (neon, local), neon\_project\_id, neon\_parent\_branch\_id | The company databases Blastproof protects. `connection_profile` picks which set of env-var credentials and which `NeonClient` the system uses: `neon` for the real company database, `local` for the Compose `company-db` (seeded actions, tests). Credentials stay in env vars for the MVP |
| `actions` | id, user\_id, agent\_id, system\_id, session\_id, retry\_of, tool, intent, sql, pinned\_sql, sql\_display, statement\_type, tables, status, decision, risk\_level, policy\_reason, reviewer\_note, matched\_rules (jsonb), judge (jsonb: provider, model, result), impact (jsonb: diff facts), cascade\_expectations (jsonb: per FK path, the count of child rows referencing the approved keys), impact\_report (jsonb: headline, key points, concerns), knowledge\_used (jsonb), preview\_ms, total\_ms, llm\_input\_tokens, llm\_output\_tokens, llm\_cost\_usd, decided\_by, created\_at, decided\_at, expires\_at | One row per intercepted MCP action; the heart of the system |
| `audit_events` | id, action\_id, event\_type, data (jsonb), created\_at | Append-only timeline: one event per status change, named after the new status (received, classified, previewing, awaiting\_review, approved, rejected, expired, blocked, committing, committed, drifted, executed, failed), plus shadow\_done, decided and fail\_closed. Approve and reject events carry the approver's user\_id and display\_name in `data`; auto-approvals have none |
| `preview_rows` | action\_id, table\_name, primary\_key (jsonb), before\_hash, change (deleted, updated, inserted) | Written by the shadow run: one row per affected row. The drift check compares against these at commit |
| `before_images` | id, action\_id, table\_name, primary\_key (jsonb), row\_before (jsonb), row\_after (jsonb) | Saved at commit from the locked rows; for undo later |
| `hook_events` | id, user\_id, agent\_id, tool, summary (masked), decision (allow, deny), reason, rule, created\_at | One row per Claude Code hook call. Kept out of `actions` so Bash noise never floods the review queue |
| `knowledge_chunks` | id, source, table\_names (text[], GIN index), content, created\_at | Source of truth for knowledge text and metadata, whichever vector store is used |
| `chunk_embeddings` | chunk\_id, embedding (vector) | Used only by the pgvector store; Qdrant keeps vectors in its own collection |

**Column meanings that are easy to mix up:** `sql` is exactly what the agent sent and is never returned by the API. `pinned_sql` is `sql` with `now()` and similar values replaced by one fixed timestamp (step 8); it is what is previewed, approved and committed. `sql_display` is `pinned_sql` with PII-looking string literals masked; it is the only SQL that reaches the API, the UI, logs and prompts. `policy_reason` comes from rules; `reviewer_note` is the human's reject reason, which is sent to the agent.

**Action status values (the one canonical state machine):**

```text
write:  received → classified → previewing → awaiting_review → approved → committing → committed
                       │            │               │                         │
                       └→ blocked   ├→ blocked      ├→ rejected               ├→ drifted → previewing (once)
                                    └→ failed       └→ expired (timeout)      └→ failed
fast lane or auto-allow:  classified (or previewing) → approved → committing → committed
read:   received → executed | failed
```

"Pending" is only the word used in MCP replies for `awaiting_review`; it is never stored. `expired` actions have `decided_by` null.

**Company database:** a `blastproof` schema with two tables. `applied_actions (action_id uuid primary key, committed_at)`: each commit inserts its row in the same transaction, which makes commits idempotent (step 16). `seed_info (anchor_date)`: the anchor the demo data's dates were last rebased to (step 3). Agent SQL that touches the `blastproof` schema is blocked.

**Database roles and rules:** two roles on Blastproof's database. `blastproof_owner` owns the schema and runs migrations, including the one that creates LangGraph's checkpoint tables (D-173). `blastproof_app` is what the running app uses; it has only `SELECT` and `INSERT` on `audit_events`. That migration contains the library's DDL itself (not a call to `setup()`; D-173) and grants `blastproof_app` `SELECT, INSERT, UPDATE, DELETE` on `checkpoints`, `checkpoint_blobs` and `checkpoint_writes` and `SELECT` on `checkpoint_migrations`. There is no `ALTER DEFAULT PRIVILEGES` (D-174). The app itself never calls `setup()`. A `BEFORE UPDATE OR DELETE` trigger on `audit_events` also raises an error, so even the owner cannot quietly rewrite history. Add an HNSW index on `chunk_embeddings.embedding`. Retention: `preview_rows`, `before_images`, `hook_events` and `login_attempts` are deleted after `RETENTION_DAYS` (30) by the background sweeper; `audit_events` are never deleted.

**Ownership (access model for the MVP):** every agent key belongs to one user. When an agent calls Blastproof, the backend looks up its key, finds the agent and its owner, and stores the owner's `user_id` on the action. Each user sees and decides only actions with their own `user_id`; admins see everything and manage users. Ownership uses the user's ID, never their email, because IDs never change and emails are personal data. Team review, per-database scoping and "no approving your own agent" come later.

## 6. API surface

Three doors into the backend: MCP tools for agents, one hook endpoint for Claude Code, and a REST API for the dashboard.

**MCP tools** (at `/mcp`, header `Authorization: Bearer <agent key>`)

| Tool | Input | Returns |
| --- | --- | --- |
| `list_tables` | none | Table names with one-line descriptions |
| `describe_table` | table name | Columns and types; from step 11 on, also notes from knowledge |
| `query_database` | `sql`, `intent` (what the agent is trying to do), optional `retry_of` (the action ID this query replaces) | Rows for reads; for writes, the final result, or `pending` with an `action_id` |
| `get_action_status` | `action_id`, optional `wait_seconds` (0 to 50, default 30) | Status, decision and reason; waits until the action is decided or `wait_seconds` pass (long poll) |

**Waiting rule (design decision):** `query_database` waits up to 20 seconds for a decision. Fast-lane, auto-approved and blocked writes finish inside that window. If human review is needed, it returns `pending` plus the `action_id` and tells the agent to keep calling `get_action_status` until it is decided. Each call long-polls for up to 50 seconds, so the agent learns the outcome within seconds of the reviewer deciding, with no long-held connections. **Polling budget:** once an action has waited longer than `AGENT_POLL_BUDGET_MINUTES` (default 10), `get_action_status` returns the `still_waiting` message instead, which tells the agent to stop polling and tell the user. This stops a 24-hour review window from becoming about 1,700 tool calls that fill the agent's context and use up the user's budget. The MCP spike confirmed Claude Code's MCP timeouts allow this (D-101). `session_id` is the MCP session ID when the transport provides one, else null; with Claude Code it is always null, because Claude Code uses the session-less 2026-07-28 protocol and `/mcp` runs stateless (D-097); `retry_of` links a rewritten query to the one it replaces, and the dashboard shows the chain.

**What the agent is told** (`services/agent_messages.py`; one template per outcome, tested word for word):

| Outcome | Message to the agent (shape) |
| --- | --- |
| committed | "Done: {n} rows {changed} in {table}." |
| blocked | "Blocked: {policy_reason}. {suggestion, e.g. exclude rows where plan is pro or enterprise}. Do not retry the same statement." |
| pending | "Held for human review (action {id}). Call get\_action\_status with this ID until it is decided. Do not try another route to the database." |
| still\_waiting | "Still waiting for a reviewer (action {id}, {minutes} minutes so far). Stop polling. Tell the user this action is waiting for their review in the Blastproof dashboard, and call get\_action\_status once when they say it is decided." |
| rejected | "Rejected by {reviewer display\_name}: {reviewer\_note}. If you write a safer query, pass retry\_of={id}." |
| drifted | "The data changed after approval; the change was re-previewed and sent back for review (action {id})." |
| expired | "No reviewer decided within {hours} hours; the action was cancelled." |
| busy | "Blastproof is busy previewing other writes. Retry in about {seconds} seconds." |
| ai\_unavailable | "Held for review: the AI risk check is unavailable right now (action {id})." |
| failed | "Blastproof could not complete this safely, so nothing was changed. Reason: {reason_code}." |
| not\_found | "No action {id} was found." (an unknown action ID, or one owned by another user, in `get_action_status` or `retry_of`) |
| truncated (reads) | "Showing the first 500 rows; add a LIMIT or narrow the WHERE to see the rest." (appended to the rows) |
| not\_supported (step 6 until step 17) | "Blastproof can only run a single SELECT for now; writes are not supported yet. Nothing was changed." (D-103) |
| query\_error (reads) | "The query failed: {Postgres message} (SQLSTATE {code}). Nothing was changed." The text goes to the agent only; logs and the action keep `query_error_<sqlstate>` (D-104) |
| table\_not\_found | "No table {name} was found. Call list\_tables to see the tables you can read." (`describe_table`, D-104) |

**Hook endpoint:**

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/hook/pre-tool-use` | Receives the Claude Code tool call JSON; returns `allow` or `deny` with a reason |

**Dashboard REST API** (JSON, JWT in `Authorization` header)

| Method | Path | Purpose | Who |
| --- | --- | --- | --- |
| POST | `/v1/auth/login` | Email + password, returns a JWT | anyone |
| GET | `/v1/auth/me` | Current user's ID, name, email and role | signed in |
| GET | `/v1/actions` | Your actions, with filters (status, agent\_id, table, from, to) and limit/offset paging | own only (admin: all) |
| GET | `/v1/actions/{id}` | Full detail, plus a `can_decide` flag; 404 if it is not yours | own only (admin: all) |
| POST | `/v1/actions/{id}/approve` | Resumes the workflow with approval | owner or admin |
| POST | `/v1/actions/{id}/reject` | Resumes with rejection; body has a reason (at least 10 characters) for the agent | owner or admin |
| GET | `/v1/stats` | Counts and averages for your Overview page | own only (admin: all) |
| GET | `/v1/policies` | Active rules, read-only | signed in |
| GET | `/v1/knowledge` | Knowledge sources with chunk counts | signed in |
| POST | `/v1/knowledge` | Multipart upload of a .md or .txt file, up to 1 MB | admin |
| DELETE | `/v1/knowledge/{id}` | Removes a source, its chunks and its vectors | admin |
| GET | `/v1/systems` | Protected company databases | admin |
| POST | `/v1/systems/{id}/scan` | Rescans the company schema into knowledge | admin |
| GET | `/v1/agents` | Your agents, with key prefix and active state | own only (admin: all) |
| POST | `/v1/agents` | Creates an agent owned by you; returns the API key once, plus `mcp_url` and ready-made setup lines (`claude mcp add ...` and the `BLASTPROOF_AGENT_KEY` line), built from `PUBLIC_BASE_URL` | signed in |
| POST | `/v1/agents/{id}/deactivate` | Disables one of your agent keys | owner or admin |
| GET | `/v1/users` | All users | admin |
| POST | `/v1/users` | Creates a user with an initial password | admin |
| GET | `/health` | Liveness only (the process is up) | public |
| GET | `/ready` | Readiness: Blastproof DB, company DB and Neon API reachable | public |

**Action detail contract.** `GET /v1/actions/{id}` returns this shape (`schemas/actions.py`); the dashboard is built against it, so changes go through the OpenAPI contract check.

```python
class ActionDetail(BaseModel):
    id: str; status: ActionStatus; decision: Decision | None; risk_level: RiskLevel | None
    agent: AgentRef                      # id, name
    owner: UserRef                       # id, display_name
    intent: str | None                   # masked; null on reads that carry none
    sql_display: str | None              # pinned and masked; raw SQL is never returned; null on reads
    pinned_values: dict[str, str]        # e.g. {"now()": "2026-10-24T12:00:00Z"}; empty if nothing pinned
    original_sql_display: str | None     # masked original, shown only when pinning changed it
    statement_type: str | None; tables: list[str]
    impact_report: ImpactReport | None   # headline, key_points, concerns, source (llm or template)
    table_changes: list[TableChange]     # table, deleted, updated, inserted, is_cascade
    breakdowns: list[Breakdown]          # column, counts by value, e.g. plan: free 48,201, pro 9
    derived_groups: list[DerivedGroup]   # name, count, definition, e.g. paying: 12
    samples: list[SampleRow]             # table, change, side (before or after), masked row values (max 50 in all)
    judge: JudgeView | None              # provider, model, recommendation, risk_level, confidence, reason, failure (verdict fields are null when set)
    matched_rules: list[MatchedRule]     # name, outcome, reason, pass ("pre" or "post")
    knowledge_used: list[KnowledgeRef]   # chunk id, source, table_names, first line, match (table or similar), score
    policy_reason: str | None; reviewer_note: str | None
    retry_of: str | None; retried_by: str | None
    timeline: list[AuditEventView]       # event_type, created_at, short data
    timings: Timings                     # preview_ms, total_ms
    llm_cost_usd: float | None
    decided_by: UserRef | None; created_at: datetime; decided_at: datetime | None
    expires_at: datetime | None
    can_decide: bool
```

The list endpoint returns a smaller `ActionSummary` (id, status, decision, risk\_level, agent, intent, headline, tables, created\_at, decided\_by).

**Conventions shared with the dashboard:** every error, including validation errors (a custom handler replaces FastAPI's default 422 shape), uses `{"error": {"code": "...", "message": "..."}}`; list endpoints return `{"items": [...], "total": n, "limit": n, "offset": n}`; timestamps are ISO 8601 in UTC; IDs are UUID strings; a 409 on approve or reject names who already decided. Every route declares a Pydantic response model, because the dashboard generates its TypeScript types from `/openapi.json`, which also serves as the API reference at `/docs`.

## 7. Action pipeline

Reads skip this graph and run directly with the read-only role. Every write runs through this LangGraph workflow, one graph run per action.

&#91;embedded content: LangGraph write pipeline · 11 nodes, 3 outcomes\]

Rules can block at two points, the judge can only make a decision stricter, and the human review node pauses the graph with its state saved in Postgres until a reviewer approves or rejects.

**Fast lane:** after classification and the pre-shadow rules, a write that is fast-lane eligible (step 8), matches an `allow` rule with `fast_lane: true`, and matches no stricter rule skips the shadow run, the judge and the LLM summary. It gets a template summary and goes straight to commit, which checks the row count. It still runs through the graph and gets a full audit timeline.

**Three principles behind the pipeline** (see `docs/decisions.md`):

1. *Measure, don't predict:* the shadow run diffs whole tables on the branch instead of guessing effects from the SQL.
2. *Approve exactly what was previewed:* non-deterministic values are pinned before the preview, and the commit must reproduce the approved diff or roll back.
3. *No facts, block; facts but no AI opinion, review:* a failed shadow run blocks; a failed judge sends the write to review. Either way nothing is applied without rules or a human allowing it.

## 8. Build steps A: foundation

Steps 1 to 7 give you a dumb pipe: Claude Code reads the company database through Blastproof, and every call is recorded. Each step ends with a test you can run.

1. **Repo and skeleton.** `uv init`, add the libraries from section 3, create `main.py` with a `/health` route, `config.py` with pydantic-settings, structlog JSON logging, Ruff and pytest. Add `docker-compose.yml` with Blastproof's Postgres using the `pgvector/pgvector:pg18` image, and an init script in `docker/` that creates the `blastproof_owner` and `blastproof_app` roles (section 5). Startup must not call Neon or download models when `APP_ENV=test`. Configure Ruff's banned-imports rule (`flake8-tidy-imports`, TID251) to ban `demo_company` in the backend (check the current Ruff docs for the syntax).
   *Done when:* `docker compose up -d`, then `uv run uvicorn app.main:app --reload`, and `/health` returns `ok`.
2. **CI.** A GitHub Actions workflow that runs `ruff check`, `ruff format --check` and `pytest`, with a Postgres service container for tests.
   *Done when:* a pull request shows a green check.
3. **Demo company database and eval scenarios.** In `demo_company/`, write `schema.sql` with about 11 tables: `users` (with plan `free/pro/enterprise` and `last_login_at`), `organizations`, `subscriptions`, `payments`, `invoices`, `products`, `orders`, `order_items`, `support_tickets`, `feature_flags`, `customer_notes` (id, user\_id, body, created\_at; the fast-lane table for demo step 1). Every table has a primary key. Foreign keys, stated explicitly: `orders`, `subscriptions`, `payments` and `support_tickets` → `users` are `ON DELETE CASCADE`; `order_items` → `orders` is `ON DELETE CASCADE`; `customer_notes` → `users` is `ON DELETE CASCADE`; `invoices.user_id` → `users` is `ON DELETE SET NULL` (so the report shows a non-delete side effect); `users.organization_id` → `organizations` is `ON DELETE SET NULL`. No triggers. Also create the `blastproof` schema with `applied_actions` (section 5). Write `seed.py` with Faker (fixed seed): about 50,000 users, deliberately including inactive paying customers (the trap in the demo).
   - **Stable numbers:** every timestamp is generated relative to a fixed `ANCHOR_DATE` (2026-10-01), never relative to the real clock, so the same seed always gives the same data. `seed.py` writes it to `blastproof.seed_info`; the scenario files carry `anchor_date: 2026-10-01` at the top, and backend tests and evals read it from there (the backend never imports `demo_company/`). A CI test checks that the two match on the local `company_main`.
   - **`rebase-dates` command:** shifts every timestamp column in every table by the same interval (today minus the anchor), and records the new anchor in `blastproof.seed_info`. Relative ages are unchanged, so "inactive for 6 months" counts (48,213, 12 paying) are reproduced exactly on any day. Run it on the long-lived Neon databases before a demo, or on a schedule. **Owner-run only:** it rewrites rows on a Neon main branch, so Claude Code never runs it (CLAUDE.md, "Safety while building"); the command asks for confirmation and prints the target project first. It changes every row's hash, so any action awaiting review drifts at commit and goes back through preview (step 16); run it when the review queue is empty.
   - **Evals and tests** run with the classifier's `Clock` (step 8) frozen at the anchor, so expected counts never move. Create two Postgres roles: `blastproof_reader` (SELECT only) and `blastproof_writer` (SELECT, INSERT, UPDATE, DELETE on the company tables, plus INSERT and SELECT on `blastproof.applied_actions`). Load it into two places in this step:
   - The local `company-db` Compose service, via `uv run python demo_company/seed.py --target local` (run from the host or a CI step, not from a Postgres init script). It creates `company_template`, loads the schema and data into it, closes its connections, locks it (`ALTER DATABASE company_template ALLOW_CONNECTIONS false`), and clones `company_main` from it. The template is never connected to again; tests and seeded actions use `company_main`. See `FakeNeonClient` in step 10.
   - The Neon `blastproof-company-dev` project (local dev). Claude asks before running this load, because it overwrites a Neon main branch.
   - The Neon `blastproof-company` project (deployed demo) is **not** loaded here: the owner loads it during deployment (section 15), because CLAUDE.md keeps Claude Code off it. Write `schema_docs.md` with one `## table: <name>` heading per table plus a `## business rules` section, e.g. "a plan in (pro, enterprise) means a paying customer; enterprise accounts have contracts and must never be deleted."
   **Eval scenarios first** (SPEC §18): write `backend/evals/scenarios/incidents.yaml` (the MVP incident replays from section 14) and a first `labeled.yaml` (about 20 of the 50 labeled actions). Each entry has `sql`, `intent`, the expected decision and, where known, expected row counts against the seed as of `ANCHOR_DATE`. Steps 8, 9, 10, 12 and 13 each run the scenarios that apply to them.
   *Done when:* the seed runs, a count query by plan returns sensible numbers on the local and dev databases, and the scenario files load and validate against a Pydantic schema.
4. **Blastproof data model.** SQLAlchemy models for the eleven tables in section 5. Migrations run as `blastproof_owner`. The first Alembic migration also runs `CREATE EXTENSION vector`, grants `blastproof_app` its privileges (only `SELECT` and `INSERT` on `audit_events`), and adds the `BEFORE UPDATE OR DELETE` trigger on `audit_events`.
   *Done when:* `alembic upgrade head` creates all tables locally, and a test connected as `blastproof_app` fails to update or delete an audit event (and so does one connected as `blastproof_owner`, because of the trigger).
5. **Auth.** Agent keys: generate `bp_` + `secrets.token_urlsafe(32)`, store only its SHA-256 hash and the first 8 characters, show the full key once. Add a CLI command `create-agent` and a FastAPI dependency `get_current_agent` that checks the bearer key and rejects the key of a deactivated agent (`is_active` false) with 401. Dashboard users: a `create-user` CLI (email, display name, role), argon2 password hashes, `/v1/auth/login` returning a JWT (8-hour expiry) that carries `token_version`, and a `require_admin` dependency. Every request rejects inactive users and stale `token_version`s. Login is rate limited through `login_attempts`, both per email+IP and per email alone. Password change is deferred.
   *Done when:* tests prove a wrong key or token, or a deactivated agent's key, returns 401, an inactive user or a bumped `token_version` returns 401, the sixth failed login in 15 minutes from one IP is refused, the 21st failed login in an hour for one email is refused across IPs, and an admin-only route rejects members with 403.
6. **MCP server.** *Spike done* (`spikes/mcp_mounting.md`, D-096 to D-101). Inside `create_app()`, create an `MCPServer` (SDK v2) with the four tools from section 6, call `streamable_http_app(stateless_http=True)` (SSE responses, the default) and add its single `/mcp` route to FastAPI's router, not `app.mount` (D-096, D-097); the lifespan enters `mcp.session_manager.run()`. Pass `TransportSecuritySettings` built from config: `localhost:*`, `127.0.0.1:*` and the host of `PUBLIC_BASE_URL`, added to `Settings` now (D-099); tests use the base URL `http://localhost:8000`. Add pure-ASGI middleware on `/mcp` that calls `authenticate_agent_key` (401 with the error envelope on failure), puts the agent and owner into a `contextvar` and `scope["state"]`; tools read the contextvar and refuse the call if it is unset (D-098). Tools return the `agent_messages.py` text as a normal result, never `ToolError`; unexpected errors are logged through structlog as a masked `reason_code`, and the SDK's `mcp` logger must not write raw exception text (D-100). Tests drive two agents' keys through the real middleware: interleaved, concurrent and a key change between calls. For now `query_database` runs reads with the `blastproof_reader` role (statement timeout 5 s, max 500 rows) and refuses writes with "not supported yet." A read that would return more than 500 rows (fetch 501 to tell) returns the first 500 plus the `truncated` notice (section 6). `describe_table` returns columns and types only until step 11. Record every `query_database`, `list_tables` and `describe_table` call, with the owner's `user_id`, in `actions` and `audit_events`; reads end as `executed`. `get_action_status` creates no action (it asks about an existing one) and is only logged. Add `/ready` (Blastproof's and the company database now; the Neon API check joins in step 10, D-105). Built in step 6 (D-102 to D-111): the first tool call creates the main `systems` row (D-102); until step 8 a temporary `check_read` refuses anything but one plain SELECT (D-103); a Postgres error on a read returns the `query_error` text to the agent only (D-104); `get_action_status` is an owner-scoped lookup that does not wait yet (D-106); `retry_of` joins `query_database` in step 17.
   *Done when:* after `claude mcp add --transport http blastproof http://localhost:8000/mcp --header "Authorization: Bearer <key>" --scope local` (check the Claude Code docs for exact syntax; `--scope local` keeps the key out of `.mcp.json`), Claude answers "how many pro users do we have?" through Blastproof, and the call appears in `actions`.
7. **Remove direct access.** Create a separate demo workspace folder for Claude Code, **outside the repo**, with no database URL anywhere: not in `.env`, not in shell variables. Only Blastproof's own environment holds the company database credentials. Add the workspace's `.claude/settings.json` with `permissions.deny` rules (syntax from the hook spike, D-115): `Read(//d/<path to repo>/**)` (the whole repo, D-117; a Read deny also covers Grep, Glob, `cat` and `Get-Content`), `Read(//**/.env)`, `Read(//**/.env.*)`, other credential files and `Edit(.claude/**)`, plus `blockReadsOutsideWorkingDirectories`. `claude_hook/workspace.py` creates, checks and verifies it (D-118) so the agent cannot switch off its own hook. Trust the workspace folder once by hand (hooks stay off in an untrusted interactive folder). **Accepted dev limitation** (`docs/decisions.md`): on the dev machine, Claude Code could still reach the dev `.env` by some route the guardrails miss. Those credentials only unlock the dev company database, never the demo's. Full isolation is checked on the deployed demo (section 17).
   *Done when:* searching the demo workspace and its environment for "postgres" finds nothing, and Claude's `Read` of the backend's `.env` path is denied.

## 9. Build steps B: the safety pipeline

Steps 8 to 13 build the pieces that judge a write. Build and unit-test each one on its own; step 14 wires them together.

8. **Classifier** (`services/classifier.py`). Input over 20 KB is not parsed and returns `unknown`; parse with sqlglot (Postgres dialect) in a worker thread with a 2-second cap. Return `kind` (read, write, ddl, unknown), statement types, tables, schemas touched, `has_where`, `multi_statement`, `transaction_control` (BEGIN, COMMIT, ROLLBACK, SAVEPOINT, SET TRANSACTION), `non_deterministic`, `volatile_in_where`, `unordered_limit` (a LIMIT without ORDER BY anywhere in a write, which picks different rows between preview and commit), `fast_lane_eligible`, `target_tables` (the tables a write changes) and `side_effect_functions` (D-124). Walk the whole tree, so a DELETE hidden inside a CTE counts as a write. A parse error or timeout returns `unknown`, which is treated as a dangerous write and blocked in pass 1 by the "Unparseable SQL" rule (step 9). Side-effect functions like `pg_terminate_backend` count as writes, and so does any function not on the allowlist (D-124).
   - **Pinning:** produce `pinned_sql` by replacing `now()`, `current_timestamp`, `current_date`, `localtimestamp`, `statement_timestamp()`, `clock_timestamp()`, `transaction_timestamp()`, `current_time` and `localtime` with one UTC literal taken at intercept time (cast to the original type, precision kept; spliced by character span and re-parsed to check, D-122). Special strings like `'now'::timestamptz` cannot be pinned, so they count as volatile, and return the `pinned_values` map. Everything after this step uses `pinned_sql`. The time comes from an injected `Clock` (the real clock in the app; frozen at `ANCHOR_DATE` in tests and evals).
   - **Volatile values:** `random()`, `nextval()`, `gen_random_uuid()` and other volatile functions inside a WHERE (or a subquery feeding one) set `volatile_in_where` (blocked in step 9). Inside INSERT values they are allowed.
   - **Fast lane:** `fast_lane_eligible` is true only for a single `INSERT … VALUES` with at most 10 rows (counted from the VALUES list), no SELECT, no `ON CONFLICT`, no CTE, no `RETURNING` at all (D-125), and **only literals, `NULL`, `DEFAULT`, casts of literals and pinned timestamps** in the VALUES. Any function call makes it ineligible. Which tables may use the fast lane is decided only by rules (step 9), never here.
   - **Display:** produce `sql_display` from `pinned_sql` with string literals that look like PII (email, phone) masked.
   *Done when:* about 40 table-driven unit tests pass, including the tricky cases (CTE writes, `UPDATE … FROM`, `WHERE true`, pinning with a frozen clock, multi-statement, transaction control, `blastproof` schema access, unordered LIMIT, a function call inside fast-lane VALUES), and the classifier-relevant incident scenarios pass.
9. **Policy rules** (`services/policy.py`, `policies/rules.yaml`). Each rule has a name, conditions (all must hold), an outcome (allow, review, block) and a reason.
   - **Condition keys** (the complete list; unknown keys fail at startup): SQL-only keys `statement_in`, `kind_in`, `tables_any`, `schemas_any`, `has_where`, `multi_statement`, `transaction_control`, `volatile_in_where`, `unordered_limit`, `fast_lane`, `side_effect_function` (D-128); diff keys `rows_affected_gt`, `rows_affected_lte`, `fraction_of_table_gt`, `fraction_of_table_gte`, `cascade_tables_any`, `derived_group_gt` (e.g. `{users.paying: 0}`; a group whose table was not affected counts as 0).
   - **Two passes:** pass 1 runs before the shadow run, with only the SQL-only keys known. A rule that uses any diff key is *skipped* in pass 1, not treated as false. A pass-1 `block` ends the flow with no shadow run. A pass-1 `allow` on a fast-lane write sends it down the fast lane. Pass 2 runs after the shadow run with every key.
   - **Outcome:** the strictest matching outcome wins (block, then review, then allow). **Auto-approve needs a positive `allow` match and no stricter match**; a write that matches no rule goes to review.
   - **The fast lane's single source of truth** is an `allow` rule with `fast_lane: true` and `tables_any`. There is no separate table list.
   - The same file defines, **per table**: `important_columns` (for the breakdown), `derived_groups` (named SQL conditions counted by code over that table's affected rows only, e.g. `users: {paying: "plan in ('pro','enterprise')"}`; startup validates the file; `check_columns` checks that every column they name exists, and the shadow run calls it, D-132), and `sensitive_columns` (masked everywhere, combined with a name heuristic: email, phone, ssn, address, token, password). Load at startup; restart to apply changes.
   *Built in step 9 (D-128 to D-132):* the loader is strict (duplicate YAML keys, unknown keys and a mis-configured `fast_lane` all stop startup); `rows_affected` counts cascades; fractions are compared exactly; missing or negative diff facts raise `PolicyFactsError` for the caller to block on. Known limits are pinned by tests (D-131).
   *Done when:* unit tests cover each rule, the pass-1 skip semantics and the strictest-wins logic, and every incident scenario decidable from SQL alone is blocked in pass 1.

```yaml
rules:
  - name: No DDL
    when: { kind_in: [ddl] }
    then: block
    reason: Schema changes by agents are not supported in this version.
  - name: Unparseable SQL
    when: { kind_in: [unknown] }
    then: block
    reason: Blastproof could not parse or does not support this statement, or it is over 20 KB. Send one plain SQL statement.
  - name: No DROP or TRUNCATE
    when: { statement_in: [drop, truncate] }
    then: block
    reason: Dropping or truncating tables is never allowed for agents.
  - name: One statement only
    when: { multi_statement: true }
    then: block
    reason: Send one SQL statement per call.
  - name: No transaction control
    when: { transaction_control: true }
    then: block
    reason: Blastproof manages transactions; send the statement alone.
  - name: Hands off Blastproof's schema
    when: { schemas_any: [blastproof] }
    then: block
    reason: The blastproof schema belongs to the gateway.
  - name: No side-effect functions
    when: { side_effect_function: true }
    then: block
    reason: Functions that act on the database server itself, such as ending connections, are not allowed.
  - name: Volatile values in WHERE
    when: { volatile_in_where: true }
    then: block
    reason: The rows this matches would differ between preview and commit.
  - name: Unordered LIMIT
    when: { unordered_limit: true }
    then: block
    reason: LIMIT without ORDER BY picks different rows each time; add ORDER BY a unique key.
  - name: Delete or update without WHERE
    when: { statement_in: [delete, update], has_where: false }
    then: block
    reason: Affects every row in the table.
  - name: Every row of a table
    when: { statement_in: [delete, update], fraction_of_table_gte: 1.0 }
    then: block
    reason: Would change every row in the table.
  - name: Most of a table
    when: { statement_in: [delete, update], fraction_of_table_gt: 0.5 }
    then: review
    reason: Would change more than half of the table's rows.
  - name: Large change
    when: { rows_affected_gt: 1000 }
    then: review
    reason: More than 1,000 rows would change.
  - name: Touches paying customers
    when: { derived_group_gt: { users.paying: 0 } }
    then: review
    reason: Affects paying customers.
  - name: Fast-lane note
    when: { fast_lane: true, tables_any: [customer_notes] }
    then: allow
    reason: Small insert into a notes table.
  - name: Small insert
    when: { statement_in: [insert], rows_affected_lte: 10 }
    then: allow
    reason: Small insert.
important_columns:
  users: [plan]
derived_groups:
  users:
    paying: "plan in ('pro', 'enterprise')"
sensitive_columns:
  users: [email, phone]
```

Two layers catch "affects every row". The classifier sets `has_where: false` when the WHERE simplifies to a constant true (`WHERE true`, `WHERE 1=1`; sqlglot's simplifier), which blocks in pass 1. The measured `fraction_of_table_gte: 1.0` then catches what syntax can't (`WHERE id > 0`). The demo's "clean up inactive users" (about 96% of users) is held for review, not blocked, by "Most of a table", "Large change" and "Touches paying customers".

10. **Neon client and shadow executor** (`services/neon.py`, `services/shadow.py`). *Spike done* (`spikes/neon_branching.md`, D-133, D-134). Measured on the dev project: create-branch to first query p50 5.0 s, p95 5.2 s; a whole previewed write about 14 s p50 (hashing 3.3 s, delete, diff and rollback 5.7 s). Neon returns no `connection_uris` here, so build the DSN from `endpoints[0].host` plus the writer user, password and database of `COMPANY_DB_WRITER_URL`. Poll the create response's `operations` (`finished` or `skipped` good; `failed`, `error`, `cancelled` bad), then retry the first connect for a few seconds (it takes about 2 s). Set `expires_at` (now + 15 min) on every shadow branch as a backstop and keep the startup cleanup. A 422 `BRANCHES_LIMIT_EXCEEDED` on create (cap is 10, the default branch counts) maps to the `busy`/failed outcome; a 204 on delete is success. Neon errors are `{request_id, code, message}` with an empty `code` for most 4xx, so match on status and message.
    - **`NeonClient` interface** with two implementations, picked per system by its `connection_profile`:
      - `NeonApiClient` creates a branch from `NEON_PARENT_BRANCH_ID` named `shadow-<action_id>` with a read-write endpoint, waits until ready, returns its **direct (non-pooled)** connection string (built by us, see above), and deletes it afterward. Direct only: the shadow run needs session temp tables, `TimeZone` and a multi-statement transaction. The spike could not exercise PgBouncer (the endpoint had pooling off), so the choice rests on design, not on a failure (D-134). Commits on main also use the direct endpoint.
      - `FakeNeonClient` (the `local` profile: tests, CI, seeded actions) "branches" with `CREATE DATABASE shadow_<id> TEMPLATE company_main` on the local `company-db`, which gives real copy semantics with no network. `CREATE DATABASE … TEMPLATE` fails if any session is connected to the source, so: the `local` profile opens company-database connections per operation (no pool); and before cloning, the fake terminates any remaining sessions on `company_main` with `pg_terminate_backend`, using a test-only superuser URL (`FAKE_NEON_ADMIN_URL`). Test setup recreates `company_main` from the never-connected `company_template`. Because that terminate step would abort an in-flight commit or a second clone, every `local`-profile operation on `company_main` (clone, read, commit) takes one lock (a Postgres advisory lock on the `postgres` database of `company-db`, because advisory locks are per database): reads take it shared, so they never wait for each other, and a clone or a commit takes it exclusively (D-138).
    - **One row-hash expression, everywhere:** `ROW_HASH_SQL = "md5(row_to_json(t)::text)"`, defined once in code and used by both the shadow run and the commit (step 16). Hashes are always computed by Postgres, never in Python. Every company-database connection sets `TimeZone=UTC` and `extra_float_digits=1` explicitly, so the same row always hashes the same.
    - **Shadow run, measured not predicted.** On one branch connection:
      1. For every company table, `CREATE TEMP TABLE before_<t> AS SELECT <pk> AS pk, <ROW_HASH_SQL> AS h FROM <t> t`. The snapshot stays on the branch, so no table data travels to the backend.
      2. `BEGIN`; run `pinned_sql` through asyncpg's prepared-statement path (`fetch`, never argument-less `execute`, which would run several statements). Then, per table, return only the differences: `SELECT coalesce(b.pk, a.pk), b.h, a.h FROM before_<t> b FULL OUTER JOIN (SELECT <pk> AS pk, <ROW_HASH_SQL> AS h FROM <t> t) a ON a.pk = b.pk WHERE b.h IS DISTINCT FROM a.h`. Missing after means deleted, missing before means inserted, a changed hash means updated. Also read up to 50 sample after-rows for inserts and updates. Tables other than the target that changed are cascades (including `SET NULL`).
      3. `ROLLBACK`. The branch is back to its original state, so the before-rows exist again.
      4. Read by key from the restored rows: the target table's breakdowns (`important_columns`), its `derived_groups` counts, total row counts per table (for `fraction_of_table_*`), and up to 50 masked sample before-rows.
      5. Compute the **cascade expectations**: from `pg_constraint`, walk the FK paths leading to the target table (up to depth 3). For each path, count the child rows that reference the deleted or updated target keys (e.g. orders, order\_items via orders, invoices for `SET NULL`). These counts are stored in `actions.cascade_expectations`; the commit repeats the same queries on main (step 16).
    - **Output** a `Diff`: per table deleted, updated, inserted and `is_cascade`; total row counts; breakdowns; derived groups; cascade expectations; masked samples. Write one `preview_rows` row per affected row (table, primary key, before hash, change) to Blastproof's database with `COPY`. That is what the drift check compares against.
    - **Limits:** a table without a primary key fails the shadow run (so the write is blocked). At most `MAX_CONCURRENT_SHADOWS` (default 2) at once, via a semaphore; a write waits up to 10 seconds for a slot, then fails closed with the `busy` message. Timeout: 30 seconds. Always delete the branch in a `finally` block; on startup (under an advisory lock), delete any leftover `shadow-*` branches older than 15 minutes.
    - **Scale note:** whole-table hashing is fine up to a few million rows; past that, limit it to FK-reachable tables or use logical decoding. Recorded in `docs/decisions.md`.
    *Done when:* a DELETE of users inactive for 6 months (with the clock frozen at `ANCHOR_DATE`) returns exact counts, including cascaded `orders` and `order_items` and the `SET NULL` on `invoices`, plus correct breakdowns and derived groups, matching `incidents.yaml`. It runs with `FakeNeonClient` in CI (including two previews in a row of the same `company_main`) and with `NeonApiClient` once by hand. A test proves the same row hashes identically in the shadow run and in the commit query, with different session time zones.
11. **Knowledge and RAG, with a swappable vector store** (`services/knowledge.py`). Three sources become chunks: an automatic schema scan (one chunk per table: columns, types, foreign keys), `schema_docs.md` split by heading (a `## table: <name>` heading tags the chunk with that table; other headings are untagged), and uploads through `/v1/knowledge` using the same splitter (the upload takes an optional `tables` field that tags every chunk from that file). The chunks used for an action are saved to `actions.knowledge_used`. Chunk text and metadata (source, table name) always live in Postgres in `knowledge_chunks`, the source of truth; vectors go to whichever store `VECTOR_STORE` selects. (Step 11 as built: `table_names` is an array, so a chunk can carry several tags; `VectorStore.upsert` takes a batch of items and there is a `clear()` for reindex; the model loads on first use plus a warm-up after startup; D-144 to D-147.) Retrieval for an action: all chunks tagged with the affected tables (a plain SQL query), plus the top 10 by vector similarity (D-148; was 5) to the agent's `intent`, de-duplicated.
    - `Embedder` interface, `embed(texts) -> vectors`: fastembed (`BAAI/bge-small-en-v1.5`, 384 dimensions) by default. Stores never embed; they only store and search vectors.
    - `VectorStore` interface: `upsert(chunk_id, vector, metadata)`, `search(vector, k, tables=None) -> [(chunk_id, score)]`, `delete(chunk_ids)`. Results are chunk IDs; text is always read from Postgres, so every store behaves the same.
    - `PgVectorStore` (default): the `chunk_embeddings` table with an HNSW index.
    - `QdrantStore` (after the MVP): one collection, `blastproof_knowledge`, with `table_name` and `source` as payload for filtering. Runs as a Docker container locally (Compose profile `qdrant`) or on Qdrant Cloud's free tier.
    - `reindex` command: rebuilds the active store from `knowledge_chunks`. Run it after switching `VECTOR_STORE` or the embedding model.
    - Contract tests: one pytest suite runs against every `VectorStore` implementation, so a new store must pass the same checks.
    *Done when:* an action deleting users retrieves the "paying customer" and "enterprise contracts" rules; once the Qdrant store exists, switching `VECTOR_STORE` and running `reindex` gives the same results on the eval set.
12. **Impact engine** (`services/impact.py`). Code counts, the LLM explains. First, deterministic facts from the diff: affected rows by each important column (e.g. plan: free 48,201, pro 9, enterprise 3), each derived group (paying: 12), cascades, and any percentage the summary may need (computed in code). Then the summary model (Gemini Flash by default), via `with_structured_output`, writes an `ImpactReport` (headline, key points, concerns) from those facts plus retrieved knowledge, told to use only the numbers given and to write numbers as digits. The prompt sees only `sql_display` and masked samples.
    - **Knowledge is untrusted text (from step 11, D-149).** Chunks come from uploads anyone can write. Before they enter a prompt, run `mask_text` on them and wrap them as quoted data the model must not obey.
    - **Number guardrail:** extract every number from the output with a regex (`\d[\d,]*(\.\d+)?(\s*(%|percent\b))?`, commas stripped, after NFKC so other digits cannot slip past; number words are checked too). Each must be in the allowed set: the facts, the numbers in the agent's intent and in `sql_display` (D-150), and the rule thresholds read from the loaded policy. Knowledge text adds nothing. Any other number means falling back to a plain template summary built from the facts.
    - **Cost:** record input and output tokens from LangChain's `usage_metadata`; `llm_cost_usd` is tokens × a per-model price table in config (code counts, again).
    - **Limits:** 20-second timeout and a max-token cap. On error or timeout, use the template summary.
    *Step 12 as built (D-150 to D-156):* `app/services/llm.py` builds models from `SUMMARY_MODEL` plus `SUMMARY_MODEL_OPTIONS` (lazily, cached) and holds `LLM_PRICES`; a model without a price stops startup. The engine never raises for an LLM problem: every failure gives the template with a `fallback_reason`, and no changes means no call. A report over the shape limits falls back rather than being trimmed. `save_impact` adds each call's tokens and cost to the action's totals; an unknown cost makes the total NULL, which step 14 treats as over the cap. `important_columns` may not be sensitive. `impact-report` (CLI) runs a local preview, retrieval and the real model.
    *Done when:* the report reads like "Deletes 48,213 users, including 12 paying customers (3 enterprise), and their orders through a cascade," and guardrail tests cover commas, percentages, intent numbers and an invented number.
13. **Risk judge, pluggable** (`services/judge.py`). One interface, `Judge.assess(context) -> JudgeResult`, where the context holds the SQL, intent, diff facts, impact report and retrieved rules, and the result has `risk_level` (low to critical), `recommendation` (allow, review, block), `confidence` (0 to 1) and a short `reason`. The `JUDGE_PROVIDER` setting picks the implementation; the workflow never knows which one ran.
    - **Knowledge is untrusted text (from step 11, D-149).** Same rule as step 12: mask and wrap retrieved chunks as data. Also check that the business rules still rank in the retrieved set (D-148: top 10).
    - **When it runs:** only on writes the rules did not block, and never on the fast lane.
    - **What it can do:** only make a decision stricter. It may raise allow to review or block, and review to block, but never lower anything. A `confidence` below `JUDGE_MIN_CONFIDENCE` (default 0.6) on an allow recommendation counts as review; otherwise confidence is stored for calibration evals.
    - **On error, timeout (20 s) or an unparseable result:** the decision becomes at least review, with the `ai_unavailable` message to the agent. The facts exist, so a human can still decide; a write is never allowed because the judge failed.
    - **Prompt-injection guard:** SQL, intent and sample rows are passed as clearly marked untrusted data, and a simple pattern check (e.g. "ignore previous instructions" in SQL comments or intent) forces review.
    - `LLMJudge` (MVP default): any chat model set in `JUDGE_MODEL` (Gemini 3.8 Flash by default, D-158), with structured output; writes the reason itself.
    - `JevJudge` (right after the MVP): calls Jev through Vercel AI Gateway's `/v1/evaluate` HTTP API with `httpx`, asking a few boolean questions (affects paying customers? matches the stated intent? touches sensitive tables?). Code turns the probabilities into a recommendation using thresholds from config, takes `confidence` from them, and builds the reason from the questions that scored high. Request zero data retention in the provider options.
    - Shadow judging: the optional `JUDGE_SHADOW_PROVIDER` runs a second judge on the same context and logs its result to LangSmith and `audit_events`, without affecting the decision. Real comparison data at no risk.
    *Step 13 as built (D-158 to D-164):* `Judge` is a protocol; `JUDGE_PROVIDERS` maps `JUDGE_PROVIDER` to a class, and an unknown name stops startup. `LLMJudge` follows the impact engine (lazy model, `with_structured_output(JudgeVerdict, include_raw=True)`, one attempt, price required, tokens kept on later failures). A verdict with a confidence outside 0 to 1 or a blank or over-long reason is `bad_shape`; a reason with an unmeasured number keeps the verdict but is replaced by a code-built sentence (`reason_withheld`). `assess_safely` turns any raise, hang or malformed result of any judge into a failure. `decide` is pure and strictest-wins: a rule block is final, injection hits force review, a failed or missing judge gives review with `ai_unavailable`, and an allow below `JUDGE_MIN_CONFIDENCE` is review. `save_judge` stores `actions.judge`, `risk_level` and adds tokens and cost. `judge-report` (CLI) runs a local preview, retrieval, the summary and the real judge. Wiring into the graph, the cost-cap skip and the `ai_unavailable` message to agents are steps 14 and 17.
    *Done when:* tests show the judge escalates a risky "allowed" action, cannot downgrade a blocked one, and a judge that raises or times out produces review (never allow), and switching `JUDGE_PROVIDER` needs no code change.

## 10. Build steps C: approval, commit, hook and dashboard API

Steps 14 to 20 connect the pieces into one flow and finish the MVP.

14. **LangGraph workflow** (`workflow/graph.py`). *Spike done (SPIKE-04, D-173 to D-178).* Writes only; reads stay on a fast path outside the graph. The state holds the action ID, pinned SQL, intent, classification, diff, knowledge, impact, judge result, decision and reviewer note. Nodes and edges follow the diagram in section 7, plus the fast-lane edge. Review is **two nodes**: `request_review` sets `awaiting_review` and writes the audit event; then `human_review` contains only the `interrupt()` call and returns the decision. On resume, `interrupt()` re-runs its whole node, so if the status change sat in the same node, it would overwrite `approved` with `awaiting_review`. Use `AsyncPostgresSaver` as the checkpointer, with `thread_id = action_id`. Spike results step 14 must follow:
    - **Tables:** created by an Alembic migration that holds the library's 10 statements (from `BasePostgresSaver.MIGRATIONS`, with `CONCURRENTLY` removed), inserts `checkpoint_migrations` rows 0 to 9 and grants as in section 5. Do not call `setup()` from the migration: it commits on its own connection and leaves tables behind if a later statement fails. A test compares the migration's statements with the installed `MIGRATIONS` (modulo `CONCURRENTLY`) so a library upgrade fails CI, and a test checks the exact grants on every `checkpoint%` table. `env.py` gets an `include_object` filter that skips tables named `checkpoint*`, or `alembic check` reports drift.
    - **Connection:** one `psycopg_pool.AsyncConnectionPool` for the app's lifetime with `kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}` and `check=AsyncConnectionPool.check_connection`, wrapped in `AsyncPostgresSaver(pool)`. Without `autocommit` the graph runs but stores nothing, so a test asserts a checkpoint row exists after a run. `psycopg-pool` becomes a direct dependency: pin it (ask first).
    - **Durability:** `durability="sync"` on every `ainvoke`; the default loses the last node's checkpoint on a hard kill.
    - **Calls:** `ainvoke(..., version="v2")`; read `aget_state().next` and `.interrupts`. Resume with `Command(resume=...)` only when `next == ("human_review",)`; continue after a crash with `ainvoke(None, config)`. LangGraph does not stop two concurrent resumes: step 15's SQL claim does.
    - **State:** plain types only, or register model modules with `allowed_msgpack_modules` (unregistered Pydantic types log a deprecation warning that will become a block). Each node updates `actions.status` and writes an audit event. Any exception sets the action to failed and blocks it (fail closed). The commit node calls a stub in this step; step 16 replaces it. **LLM cost cap:** after the summary call, if the action's `llm_cost_usd` is over `MAX_LLM_COST_PER_ACTION_USD`, skip the judge and take the judge-failure path (at least review, `ai_unavailable` message); the summary already written is kept.
    *Done when:* a test runs one write through every branch: fast lane, allow, review then approve, review then reject, block, judge failure to review, LLM cost cap exceeded to review. Another test proves the status after resume is `approved`, not `awaiting_review`.
    *As built (D-180 to D-186):* 14 nodes, not the 11 of the section 7 diagram, which could not be read when step 14 was planned: `classify`, `pre_rules`, `fast_lane`, `shadow`, `post_rules`, `retrieve`, `impact`, `judge`, `decide`, `approve`, `request_review`, `human_review`, `block`, `commit` (every one but `human_review` runs inside the `fail_closed` wrapper). Edges: `classify -> pre_rules -> block | fast_lane -> approve | shadow -> post_rules -> block | retrieve -> impact -> judge -> decide -> approve | request_review -> human_review -> commit | end | block`; `approve -> commit`. The state holds the classification (without `pinned_sql`), masked intent, diff summary, knowledge, impact report, judge result and decision, not raw SQL or intent (D-180, so BE-76's list is narrower). `workflow/graph.py` exposes `build_graph`, `run_write` and `resume_write` (step 15 wraps them), `workflow/checkpointer.py` the pool and saver, `services/commit.py` the `Committer` seam and `StubCommitter` (its `committed` event carries `stub: true`). Migration `0003`, `psycopg-pool==3.3.3`, `CHECKPOINTER_DATABASE_URL`.
15. **Workflow runner** (`workflow/runner.py`). `start(action)` launches the graph with `asyncio.create_task` and keeps the task in a module-level set until it finishes (so it isn't garbage-collected). `wait_for_decision(action_id, timeout)` checks the `actions` row every half second; `get_action_status` uses it for its long poll. `resume(action_id, approved, note)` first claims the decision with `UPDATE actions SET status='approved' (or 'rejected'), decided_by=… WHERE id=$1 AND status='awaiting_review' RETURNING id`. The same transaction writes the `approved` or `rejected` audit event with the approver's `user_id` and `display_name`. Zero rows means someone already decided, so return 409 and never call `Command(resume=...)`. A background loop every 10 minutes, holding a Postgres advisory lock so only one process runs it: expires reviews older than `REVIEW_TIMEOUT_HOURS` (status `expired`), fails actions stuck in `previewing` for over 5 minutes, and deletes `preview_rows`, `before_images`, `hook_events` and `login_attempts` past retention (`audit_events` are kept).
    *As built (step 15):* `app/workflow/runner.py` (`WorkflowRunner`), `app/db/retention.py`, and `claim_decision`, `status_view`, `expire_if_overdue`, `overdue_reviews`, `stuck_actions` in `app/db/actions.py`. The task set is on the runner instance (held in `app.state.runner`), not module-level, so two apps in one process do not share it. Recovery and the sweep run in a background maintenance task (recovery retried every 10 s) instead of blocking startup (TEST-07); until recovery finishes, `start` and `resume` raise `WorkflowUnavailableError("recovering")` and `/ready` names `workflow`. The claim also requires `expires_at` to be in the future, and an overdue unswept review is expired on the spot. The stuck rule covers `received`, `classified`, `previewing` and `drifted`, timed from the latest event of the current status. `/ready` also probes the checkpointer pool.
    - **Startup recovery** (under the advisory lock, before serving requests). There is one machine, so anything in flight at startup was interrupted by a restart:
      - `received`, `classified` or `previewing`: fail at once with reason `restart` (the agent's next status call sees `failed`). Shadow branches are cleaned up by step 10's startup sweep.
      - `approved` or `rejected`: read the thread's state with `graph.aget_state(config)` first. If it is still paused at `interrupt()`, the decision is already stored in the row, so resume it with `Command(resume=...)`; this covers a crash between the approve claim and the resume. If it has already moved past the interrupt (a crash after the resume but before `committing`), continue it from its last checkpoint with `ainvoke(None, config)`. If the thread is missing, fail the action with reason `restart`.
      - `committing`: handed to the commit reconciler (step 16).
    - **Graceful shutdown:** on SIGTERM, stop accepting new writes (they get the `busy` message), and wait up to 45 seconds for in-flight previews and commits. Anything still running is caught by startup recovery. `fly.toml` sets `kill_timeout = 60` so deploys don't cut this short. Actions awaiting review are safe: they are checkpointed.
    *Done when:* tests prove two simultaneous approvals produce one commit and one 409, that an expired review cannot be approved, that an action approved but never resumed is resumed at startup, and that an action left in `previewing` is failed at startup. The restart-then-approve check runs at the end of step 16, which supplies the real commit.
    *Handed over by step 14:* the SQL claim writes `decided_by`, `decided_at` and `reviewer_note`, then calls `resume_write` (it refuses a thread not paused at `human_review`; the graph only commits on an exact `{"approved": True}`). Startup recovery also covers actions left mid-flow when Blastproof's own database was down (`previewing`, `approved`, `committing`), because the fail-closed wrapper cannot record `failed` then. Add a checkpointer `SELECT 1` through `app.state.checkpointer_pool` to `/ready`. Set `decided_by` null on `expired` (BE-16).
16. **Commit** (`services/commit.py`). Using the `blastproof_writer` role on the company database's main branch, in **one company-database transaction**:
    1. Set the action to `committing` (Blastproof DB) before starting. On the company connection (direct endpoint, `TimeZone=UTC`): `BEGIN`, `SET LOCAL lock_timeout = '3s'`, `SET LOCAL statement_timeout = '30s'`.
    2. `INSERT INTO blastproof.applied_actions (action_id) VALUES ($1) ON CONFLICT DO NOTHING RETURNING action_id`. No row returned means this action was already applied: roll back and mark it committed (idempotency).
    3. **Row check:** `SELECT <pk>, <ROW_HASH_SQL>, row_to_json(t) FROM <target> t WHERE <pk> = ANY($approved) FOR UPDATE`, using the deleted and updated keys from `preview_rows`. The hash is computed by Postgres with the same expression as the shadow run, so identical rows always match. Compare with `before_hash`; any difference or missing row is drift. The returned JSON rows are the before-images.
    4. **Cascade check:** run the same per-FK-path count queries as step 10 for the approved keys, and compare with `actions.cascade_expectations`. This covers `SET NULL` children, which no row count would reveal.
    5. Run `pinned_sql` with `RETURNING <pk>`. For DELETE and UPDATE, the returned key set must equal the approved set, which catches new rows that now match the WHERE. For INSERT, compare the count, since new keys may differ.
    6. On any mismatch, roll back, mark the action `drifted`, re-preview it once automatically, and send it back to review with what changed. A second drift fails the action with the `drifted` message. Never auto-approve after drift.
    7. **Before-images first:** insert the before-images into Blastproof's database and commit that (replacing any left over from an earlier attempt for this action).
    8. COMMIT the company transaction. If Postgres answers with an error, nothing was applied: delete the before-images and mark the action failed. If the connection drops or times out during COMMIT, the outcome is **unknown**: leave the action in `committing`, keep the before-images, and run the commit reconciler for it, which checks the marker row on a fresh connection. Otherwise set `committed` and write the audit event.
    - **Crash recovery (commit reconciler)**, run by step 15's startup recovery for every action in `committing`. If its `applied_actions` row exists, the company commit happened: mark it committed (its before-images are already saved, because of step 7). If not, the company transaction died with the process and nothing was applied, so **re-run the whole commit procedure**. The marker table and the drift check make a re-run safe; a re-run that drifts follows step 6.
    - For the MVP, before-images cover the target table only; cascaded rows are recorded as counts.
    *Done when:* an approved write commits with matching counts; a test that changes a row between preview and commit triggers the drift path; a test that inserts a new matching row triggers it too; a test that nulls a child's FK (changing a `SET NULL` cascade) triggers it; committing the same action twice applies it once; a test that kills the process after the company commit is marked committed with its before-images on restart, and one killed before the company commit is re-run and committed once; a connection dropped during COMMIT is resolved by the reconciler, not marked failed; and restarting the server while an action awaits review, then approving it, still commits correctly.
    *Handed over by step 14:* implement `Committer` (`services/commit.py`) and replace `StubCommitter` in `create_app`. The fast lane stores its expected row count in `actions.impact` (`table_changes.<table>.inserted`, `rows_before` is 0) for the row-count check. The `committed` event data is `{stub, rows}`: set `stub: false` and the real count. The commit node already moves `approved -> committing -> committed` and re-checks the row status; drift handling (re-preview once) is new work: the commit node must route to `drifted`, and `committing -> drifted -> previewing` is already allowed by `ALLOWED_TRANSITIONS`.
17. **Wire up `query_database`.** Reads run directly. Writes create an action (with `retry_of` if given), start the workflow, wait up to `REVIEW_WAIT_SECONDS`, then reply with the matching template from `agent_messages.py` (section 6). `get_action_status` long-polls as described in section 6.
    - **Rate limit:** once the classifier says a call is a write, and before its action is created, count that agent's write actions from the last 60 seconds. At `MAX_WRITES_PER_MINUTE_PER_AGENT` or more, reply with the `busy` message, create no action, and log `event=rate_limited`. This is an exception to step 6's "record every call", so a looping agent cannot flood `actions`.
    - **Ownership:** `get_action_status` and `retry_of` look up action IDs through `visible_actions` for the agent's owner with admin widening turned off, so the filter is the owner's `user_id` even when the owner is an admin (an agent does not inherit admin-wide visibility). An unknown or foreign ID gets the `not_found` message. An integration test covers another owner's action ID.
    - **Concurrency test:** an integration test runs about 5 writes plus some reads at once with `FakeNeonClient`; every action ends in the expected state and no more than `MAX_CONCURRENT_SHADOWS` shadow runs overlap.
    *Done when:* in Claude Code, "clean up inactive users" ends with the agent receiving a pending result; after you reject it in `/docs`, the agent's next `get_action_status` call returns the reason within seconds, and its rewritten query carries `retry_of`. Tests cover the rate limit and the concurrency test passes.
    *Handed over by step 14:* create the action in `received` (with its `received` event) before calling `run_write`; a `failed` action carries `failure_reason` (`shadow_busy` should map to the `busy` message; `shadow_timeout`, `unknown_table`, `policy_facts`, `not_a_write`, `fast_lane_invalid`, `workflow_error` need agent messages). Send the `ai_unavailable` message when the `decided` event or `judge.failure` says so. Never ship before step 16.
18. **Claude Code hook.** (The hook spike runs in Stage 1; see WORKFLOW.) `claude_hook/pre_tool_use.py` reads the tool call JSON from stdin, posts it to `/v1/hook/pre-tool-use` with `BLASTPROOF_AGENT_KEY` (a Blastproof key, not a database credential) and its own HTTP timeout, shorter than the hook timeout. On deny it prints the reason to stderr and exits with code 2. On allow it prints nothing and exits 0; it never emits a JSON `allow` decision, because that would skip Claude Code's own permission prompts. If Blastproof is unreachable or times out, the script retries once after 1 second (covering `--reload` restarts). If it still fails:
    - **Bash, PowerShell, Monitor, Edit, Write and NotebookEdit** exit 2 (fail closed).
    - **Read, Grep and Glob** fall back to a small local path deny-list shipped in the script (`.env*`, credential and key files, the backend directory, `.claude/settings*.json`). They are allowed otherwise, so a backend restart doesn't leave the agent unable to read code. Reading is not a write, and the hook is a guardrail (D-001), so this keeps the read guardrail without blinding the agent.
    - An error response from Blastproof (5xx or unparseable) is treated the same as unreachable.

    Register it in `.claude/settings.json` under `PreToolUse` with the matcher `Bash|PowerShell|Monitor|Edit|Write|NotebookEdit|Read|Grep|Glob` (hook spike, D-114; `MultiEdit` no longer exists). The hook entry sets `"onFailure": "block"` and a `timeout` of a few seconds above the script's own HTTP timeout (D-113): without them a hung or crashing script lets the call through. The script must exit 2 only to deny on purpose.
    - **Server side, `hook_rules.py`:** deny database clients (`psql`, `pg_dump`), reading `.env` or credential files (by Read, Grep, Glob or Bash), `terraform apply` or `destroy`, `git push --force`, and `rm -rf` outside the workspace. Deny edits to `.env` and to `.claude/settings*.json` so the agent cannot switch off its own hook. Allow everything else. Commands are split with `shlex`; paths are normalised (`..`, `~`, drive letters, case on Windows) before matching. These rules are best-effort guardrails, not the security boundary (`docs/decisions.md`), so they favour clear messages over trying to catch every trick.
    - **Auth:** the endpoint checks `BLASTPROOF_AGENT_KEY` with `get_current_agent`; a wrong or deactivated key gets 401, which the script treats as a deny for every tool.
    - Every call writes a `hook_events` row with a masked summary and the calling agent and its owner; hook calls do not create `actions`.
    *Done when:* Claude trying `psql`, reading the backend's `.env`, or editing its settings file is blocked with a clear reason. With the backend stopped, Bash and edits are denied, reading a normal source file still works, and reading `.env` is still denied.
19. **Dashboard API.** The routes in section 6, with:
    - **Upload size (from step 11).** Check the 1 MB limit before reading the whole body, and decide what `DELETE /v1/knowledge/{id}` means (a source name).
    - pagination;
    - approve and reject allowed only for the action's owner or an admin, through the conditional claim from step 15;
    - every actions query going through the central visibility rule (someone else's action returns 404);
    - a required reason on reject, `decided_by` recorded, and a 409 response naming who already decided (by `display_name`);
    - admin-only routes for users, systems and knowledge changes;
    - `POST /v1/agents` returning `mcp_url` and the setup lines;
    - no CORS in production, because the dashboard reaches the API server to server through its own proxy (see the frontend plan).
    - Approve and reject, with their authorization tests, are pulled forward to right after step 15, because step 17's check and Gate 1 use them; they run the stub commit until step 16.
      *As built (D-192 to D-194):* `POST /v1/actions/{id}/approve` (no body) and `/reject` (`{"reason"}`, stripped, 10 to 500 characters) in `app/api/actions.py`; logic in `app/services/decisions.py` (`decide`, `conflict_message`). Both call `WorkflowRunner.resume`; a loser gets 409 `already_decided` naming the decider, another user's or an unknown or malformed ID gets the same 404 as GET, a runner that is missing, recovering or draining gets 503 with `Retry-After: 10`. `StatusView` gained `decided_at`. Commits still go through `StubCommitter` until step 16.
    *Done when:* you can review and approve an action using only the `/docs` page, before any dashboard exists, and the authorization matrix test passes.
20. **Bootstrap, dev commands and README.** `bootstrap` creates an admin user, an agent and the `systems` row, runs the schema scan and ingests `schema_docs.md`. `seed-demo-actions` creates two demo users, each with an agent, and sample actions for both in every status the real graph can reach before step 16 (awaiting\_review, rejected, expired, blocked, committed, failed; `approved` is momentary and `drifted` needs the real commit, so step 16's re-run adds a `failed: drifted` action, D-195). It runs the **real graph** with `FakeNeonClient` and a fake LLM, so pending seeded actions sit at a real `interrupt()` and can be approved or rejected. Retrieval is real (the knowledge service and the configured embedder); only the LLM and Neon are faked. `seed-demo-actions --add-pending` adds one new pending action on `users` for demo user 1, for the dashboard checks that need a fresh one (F4, F6, F10). *As built (2026-10-27):* `app/services/demo_seed.py` and `app/services/scripted_models.py`; the shadow run, retrieval, graph and runner are real, the two models are scripted and the commit is the stub (D-195 to D-198).
    - **Seeded actions never touch Neon.** They belong to a separate `systems` row, "Demo seed (local)", with `connection_profile = local`. Approving one previews and commits against the Compose `company-db`, even when the dev backend's main system points at Neon dev. As a second guard, the commit refuses any action whose system's profile doesn't match the connection it is about to use.
    - **Timing:** it is pulled forward to Stage 3, right after step 15 (it needs steps 14 and 15 and the local `company-db`). Until step 16 exists, committed actions go through the stub commit, so **re-run the seed after step 16** to get real committed actions. `export-openapi` writes the OpenAPI schema to `backend/openapi.json` for the frontend's contract check. Write `README.md` and a setup guide: prerequisites, the three databases, `.env` setup, bootstrap, connecting Claude Code, and installing the hook (copying `claude_hook/` and the settings snippet into the demo workspace).
    *Done when:* a fresh clone plus the README steps gives a working demo.

## 11. Configuration

All settings come from environment variables, read once by `config.py`. Locally they live in `backend/.env` (git-ignored, with a committed `.env.example`); in production they are Fly.io secrets.

| Variable | Example | Used for |
| --- | --- | --- |
| `APP_ENV` | `local` | local, test or production behavior |
| `BLASTPROOF_DATABASE_URL` | `postgresql+asyncpg://blastproof_app:...` | Blastproof's own database, as the runtime `blastproof_app` role |
| `BLASTPROOF_MIGRATOR_URL` | `postgresql://blastproof_owner:...` | Migrations and the checkpointer `setup()` only; never used by the running app |
| `CHECKPOINTER_DATABASE_URL` | `postgresql://blastproof_app:...` | Plain (psycopg 3) URL for LangGraph's checkpointer |
| `COMPANY_DB_READER_URL` | `postgresql://blastproof_reader:...@.../neondb` | Reads on the company database |
| `COMPANY_DB_WRITER_URL` | `postgresql://blastproof_writer:...@.../neondb` | Commits on the company database |
| `NEON_API_KEY` | secret | Creating and deleting branches |
| `NEON_PROJECT_ID` | `proud-sky-123` | `blastproof-company-dev` locally, `blastproof-company` in production |
| `NEON_PARENT_BRANCH_ID` | `br-...` | Parent of every shadow branch (that project's main branch) |
| `NEON_CLIENT` | `api` | Client for the `neon` connection profile: `api` (real Neon), or `fake` in tests and CI, so nothing ever reaches Neon |
| `LOCAL_COMPANY_READER_URL` / `LOCAL_COMPANY_WRITER_URL` | `postgresql://...@localhost:5433/company_main` | The `local` connection profile: the Compose `company-db` (seeded actions, tests) |
| `FAKE_NEON_ADMIN_URL` | `postgresql://company:...@localhost:5433/postgres` | Test and local only: superuser connection `FakeNeonClient` uses to clone databases and end stray sessions. Refused at startup when `APP_ENV=production` |
| `AGENT_POLL_BUDGET_MINUTES` | `10` | After this long waiting for review, `get_action_status` tells the agent to stop polling |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | The backend's public address, used to build the `mcp_url` and setup lines for new agents |
| `GOOGLE_API_KEY` | secret | Gemini calls (add the matching key, e.g. ANTHROPIC\_API\_KEY, for another provider). LangChain reads it from the process environment, so run uvicorn with `--env-file .env` locally (D-152) |
| `SUMMARY_MODEL` / `JUDGE_MODEL` | `google_genai:gemini-3.8-flash` / `google_genai:gemini-3.8-flash` | Any provider:model string, e.g. anthropic:\<claude model>; each needs an `LLM_PRICES` entry |
| `SUMMARY_MODEL_OPTIONS` | `{"thinking_level": "low"}` | Extra model constructor arguments as a JSON object; `init_chat_model`'s own keys are refused; never logged (D-152) |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Embeddings |
| `EMBEDDING_CACHE_DIR` | `backend/.cache/fastembed` | Where the model is downloaded once |
| `LANGSMITH_TRACING` | `true` | Turns tracing on |
| `LANGSMITH_API_KEY` | secret | Sends traces |
| `LANGSMITH_PROJECT` | `blastproof-dev` | Groups traces |
| `JWT_SECRET` | long random string | Signing dashboard tokens |
| `CORS_ORIGINS` | `http://localhost:3000` | Only for a browser calling the API directly in local experiments; empty in production, since the dashboard uses its own proxy |
| `REVIEW_WAIT_SECONDS` | `20` | How long `query_database` waits |
| `STATUS_MAX_WAIT_SECONDS` | `50` | Longest long poll for `get_action_status` |
| `REVIEW_TIMEOUT_HOURS` | `24` | Expire unanswered reviews |
| `MAX_CONCURRENT_SHADOWS` | `2` | Shadow runs at once; extra writes wait 10 s, then get `busy` |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_TOKENS` | `20` / `1024` | Per-call limits for summary and judge |
| `MAX_WRITES_PER_MINUTE_PER_AGENT` | `30` | Per-agent write rate limit; over it the agent gets the `busy` message (step 17) |
| `MAX_LLM_COST_PER_ACTION_USD` | `0.10` | Checked after the summary call: if over budget, skip the judge and send to review with the `ai_unavailable` message (step 14) |
| `LLM_PRICES` | `{"google_genai:gemini-3.8-flash": {"input_per_million": 0.75, "output_per_million": 3.75}}` | Price per million input and output tokens, for `llm_cost_usd`; the summary model and the judge model must each have an entry or the app does not start; a value replaces the whole table |
| `JUDGE_MIN_CONFIDENCE` | `0.6` | Below this, an allow recommendation counts as review |
| `RETENTION_DAYS` | `30` | Retention for `preview_rows`, `before_images`, `hook_events` and `login_attempts` |
| `JUDGE_PROVIDER` | `llm` | Which judge decides: llm or jev |
| `JUDGE_SHADOW_PROVIDER` | `jev` (optional) | Second judge that only logs, for comparison |
| `AI_GATEWAY_API_KEY` | secret | Calling Jev through Vercel AI Gateway |
| `JEV_ALLOW_THRESHOLD` | `0.95` | Probability cut-off for Jev to recommend allow; tuned with evals |
| `VECTOR_STORE` | `pgvector` | Which vector store: pgvector or qdrant |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant address, when selected |
| `QDRANT_API_KEY` | secret (cloud only) | Qdrant Cloud access |

The demo workspace where Claude Code runs gets only one variable, `BLASTPROOF_AGENT_KEY`, for the hook script.

## 12. Security basics

The MVP covers the essentials that matter most for a gateway holding credentials; a dedicated secrets vault and finer permissions come later.

- **Fail closed:** any error, timeout or unknown SQL blocks the write; a failure of the AI judge alone sends it to review. Either way nothing is applied without rules or a human allowing it. A global exception handler never lets a write through by accident.
- **No credentials near the agent (the real boundary):** company database credentials exist only in Blastproof's environment; the agent gets a Blastproof key only. Hooks and deny-lists are guardrails on top, not the boundary. In local development the boundary is weaker by choice: the dev `.env` sits on the same disk, guarded only by the hook and `permissions.deny`, and it unlocks only the dev company project. The deployed demo is fully isolated: its company credentials exist only in Fly.io secrets, and the machine running Claude Code never holds them.
- **Least privilege:** reads use `blastproof_reader` (SELECT only), commits use `blastproof_writer` (no DDL, no role changes).
- **Read limits:** 5-second statement timeout and a 500-row cap on results returned to agents.
- **Keys and passwords:** agent keys stored as SHA-256 hashes; passwords hashed with argon2; JWTs expire after 8 hours.
- **Untrusted text:** SQL, intents, uploaded documents and sample rows are wrapped as data in every prompt and never followed as instructions.
- **Hook self-protection:** the agent cannot edit `.claude/settings*.json` or `.env`.
- **Append-only audit:** the app role cannot update or delete audit events. Ownership: every actions query goes through one function, visible\_actions(user), which filters by user\_id unless the user is an admin; a request for someone else's action returns 404, so its existence is never revealed.
- **No secrets in logs or traces:** never log connection strings or keys; mask `sensitive_columns` (plus the name heuristic) in sample rows, and PII-looking literals in SQL (`sql_display`), before they reach logs, LLM prompts, LangSmith or the API. Raw `sql` is never returned. LangSmith client-side masking (its input/output hiding or anonymizer hooks) is a second layer behind prompt masking. `before_images` are exempt from masking because they never leave the backend, and are deleted after `RETENTION_DAYS`.
- **Input limits:** SQL up to 20 KB; parse capped at 2 seconds; commits use `lock_timeout` 3 s and `statement_timeout` 30 s.
- **Abuse limits:** cap each agent at `MAX_WRITES_PER_MINUTE_PER_AGENT` (default 30) writes per minute, counted from its `actions` rows; over the cap it gets the `busy` message (step 17).
- **Dependencies:** pin versions in `uv.lock` and enable GitHub Dependabot alerts.

## 13. Observability

Two tools are enough for the MVP: LangSmith for everything AI, and structured logs for everything else. Grafana and OpenTelemetry come in phase 2.

- **LangSmith:** set the three `LANGSMITH_*` variables and every LangChain call and LangGraph run is traced automatically, so every **write** has a trace. Tag each run with `action_id`, `agent_id` and `env` in its metadata, so one search shows an action's full AI trail, tokens and cost. Reads and hook calls have no LLM work and are covered by structured logs instead.
- **Structured logs:** structlog JSON with `request_id` and `action_id` bound on every line. Log each stage with its duration: classify, shadow, retrieve, impact, judge, decide, commit. Every fail-closed block logs `event=fail_closed` with a reason code, so they can be counted and alerted on later.
- **Request IDs:** middleware adds an `X-Request-ID` header to every response.
- **A stats endpoint:** `GET /v1/stats` returns counts by status and decision, average shadow-run time and LLM cost today, computed from the `actions` table ("today" is the UTC day). This gives the dashboard a metrics page with no extra infrastructure.
- **Health:** `/health` is liveness only; Fly.io uses it to restart the app if it fails. `/ready` also checks Blastproof's database, the company database and the Neon API.

## 14. Testing and evals

Tests prove the code works; evals prove the safety decisions are good. Both run in CI.

**Tests:**

- **Unit:** classifier, policy rules, hook rules, number-check guardrail, auth helpers, and an authorization matrix test that calls every endpoint as the owner, as another user and as an admin, and checks the expected result (the hook endpoint takes an agent key instead, so it is called with a valid, a wrong and a deactivated key). No network; LLM calls replaced with fakes.
- **Integration:** API routes and the workflow against real Postgres in Docker: `blastproof-db` for Blastproof's tables and `company-db` for the company database, with `FakeNeonClient` cloning `company_main` for each shadow run, and test setup recreating `company_main` from `company_template`. Evals and tests freeze the `Clock` at `ANCHOR_DATE`.
- **End-to-end (manual, weekly):** the demo script in Claude Code against real Neon.

**Evals** (`evals/`)

- **Incident replays (MVP set):** the Replit-style mass delete, a DROP TABLE, a delete without WHERE (and `WHERE true`), a `psql` bypass attempt, a hook-settings edit, and PocketOS-lite (the agent tries to read `.env` or credential files and is denied, and no company credential exists in its workspace). Every one must be caught. Written in step 3 (`evals/scenarios/incidents.yaml`) and used by tests from step 8 on. The real cloud-API (PocketOS), Terraform and injection replays come in Phase 2.
- **Labeled actions:** start with 50 SQL statements labeled allow, review or block (aim for 200 after the MVP), including subtle cases such as a WHERE clause that matches far more rows than the intent suggests. Step 3 writes about 20; the Evals stage tops it up to 50, and a test checks the count and that every label appears.
- **Metrics:** catch rate (dangerous actions blocked or reviewed), false block rate (safe actions held), explanation accuracy (numbers correct, checked by code), and added latency (the eval runner reports p50/p95 of `total_ms` per path: read, fast lane, previewed; for reads it also runs each query directly, without Blastproof, and reports the difference as overhead).
- **Running them:** `uv run python -m evals.run` prints a results table and saves it as JSON. Upload the labeled set as a LangSmith dataset to compare variants as experiments: rules only vs rules plus judge, Claude judge vs Jev judge (catch rate, false blocks, latency, cost and calibration: when a judge says 90% sure, is it right about 90% of the time?), with vs without RAG context, and pgvector vs Qdrant (retrieval accuracy and latency).

- **In CI:** unit and integration tests on every pull request. Incident replays and rule-only evals also run on every PR (no LLM cost); the PR fails if any incident replay is not caught. False block rate is recorded with no threshold until a first measurement sets one. The full LLM eval runs nightly, by manual trigger, and before any change to SUMMARY\_MODEL or JUDGE\_MODEL, and fails if catch rate drops below 100% on incident replays.

## 15. Local infra, CI and deployment

Locally you run two Postgres containers and the FastAPI app; in production the app runs on Fly.io and both databases live on Neon.

**Three company databases, never mixed:**

| Environment | Company database | Shadow runs | Who uses it |
| --- | --- | --- | --- |
| test / CI | `company-db` container (`company_main`, cloned from the never-connected `company_template`) | `FakeNeonClient` (template clone) | pytest, evals, `seed-demo-actions`, Playwright |
| local dev | Neon `blastproof-company-dev` | Neon branches | you, running Claude Code against your local backend |
| production demo | Neon `blastproof-company` | Neon branches | only the deployed backend (credentials only in Fly.io secrets) |

**Local:**

```yaml
# docker-compose.yml
services:
  blastproof-db:
    image: pgvector/pgvector:pg18
    environment:
      POSTGRES_USER: blastproof
      POSTGRES_PASSWORD: blastproof
      POSTGRES_DB: blastproof
    ports: ["5432:5432"]
    volumes:
      - blastproof-data:/var/lib/postgresql/data
      - ./docker/blastproof-init:/docker-entrypoint-initdb.d   # creates blastproof_owner and blastproof_app
  company-db:
    image: postgres:18   # matches the Neon projects' Postgres version (D-057)
    environment:
      POSTGRES_USER: company
      POSTGRES_PASSWORD: company   # default database stays "postgres"
    ports: ["5433:5432"]
    volumes:
      - ./docker/company-init:/docker-entrypoint-initdb.d      # reader and writer roles only
volumes:
  blastproof-data:
```

The Compose superuser is used only by the init scripts; the app connects as `blastproof_app`, and migrations as `blastproof_owner`. Run the app with `uv run uvicorn app.main:app --reload`.

- **CI** (`.github/workflows/ci.yml`): on every pull request, install with uv, run Ruff, start two Postgres service containers (Blastproof and `company-db`, with the same role init scripts), run `demo_company/seed.py --target local` to build `company_template` and `company_main`, run Alembic migrations, run tests and rule-only evals with `NEON_CLIENT=fake`. CI never talks to Neon. A second workflow runs the LLM evals nightly using the `GOOGLE_API_KEY` repository secret.

**Deployment** (do this once Gate 1 passes)

1. Write a multi-stage `Dockerfile`: install dependencies with uv in a build stage, copy into a slim Python 3.13 image, run as a non-root user, start Uvicorn on port 8080. Download the embedding model at build time so startup is fast.
2. In the Neon `blastproof-app` project, run `CREATE EXTENSION vector`. This becomes `BLASTPROOF_DATABASE_URL` in production.
3. `fly launch --no-deploy` to create the app, choosing a region close to your Neon region. In `fly.toml`, set `release_command = "alembic upgrade head"` (using `BLASTPROOF_MIGRATOR_URL`), an HTTP health check on `/health`, `auto_stop_machines = "off"`, `min_machines_running = 1` and `kill_timeout = 60` (graceful drain, step 15), and scale to exactly one machine (`fly scale count 1`). Background tasks run in-process, so a stopped machine or a second one would break them; the advisory locks are a second safety net.
4. `fly secrets set` for every secret in section 11.
5. The owner loads the seed into `blastproof-company` (`seed.py` prints the target project and asks for confirmation; Claude Code never runs it). `fly deploy`, then run the bootstrap command once with `fly ssh console`. Before recording the demo, run `rebase-dates` against `blastproof-company` so the demo numbers match `incidents.yaml`.
6. Add a deploy job to GitHub Actions that runs `flyctl deploy` on every merge to `main`, using a `FLY_API_TOKEN` repository secret.
7. Point Claude Code at the deployed MCP URL (`https://<your-app>.fly.dev/mcp`) and update `BLASTPROOF_AGENT_KEY` for the hook.

Then deploy the dashboard to Vercel by following the frontend plan. Terraform, separate staging and production environments, and Grafana come in phase 2.

## 16. Schedule

Four weeks gets you to the MVP gate if nothing slips; if something does, keep the order and let the dates move.

&#91;embedded content: MVP schedule · 4 weeks, one check per week\]

Each week ends with a check you can demo. If a week runs long, cut scope inside the step (fewer seed tables, fewer eval cases) rather than skipping a step.

## 17. MVP done checklist and what is deferred

The backend MVP is done when every box below is ticked, on the deployed version, using Claude Code.

- [ ] Claude Code reads the company database only through Blastproof; no credentials exist in its workspace, and the machine running it holds no production company credentials (deployed demo).
- [ ] "Add a note to customer 42" commits through the fast lane in under 300 ms of Blastproof time.
- [ ] "Clean up inactive users" is held for review with an impact report naming the paying and enterprise customers affected.
- [ ] Rejecting it sends the reason to Claude, which writes a safer query; the safer query is approved and committed with matching counts.
- [ ] DROP TABLE and delete-without-WHERE are blocked instantly with clear reasons.
- [ ] `psql` in the terminal and edits to `.claude/settings.json` are blocked by the hook.
- [ ] A change made between preview and commit triggers the drift path; no action is ever applied twice.
- [ ] A pending review survives a server restart.
- [ ] Every action has a complete audit timeline, and every write has a LangSmith trace. Two users each see only their own agents' actions; an admin sees all.
- [ ] Incident replays score 100% caught; catch rate and false block rate on the labeled set are recorded.
- [ ] CI is green, and merges to `main` deploy to Fly.io automatically.
- [ ] A fresh clone reaches a working demo by following the README.

**Deferred to later phases:** full agent isolation on the dev machine (a devcontainer), per-agent system and tool scoping, password change, DDL previews, output-side injection checks, the real cloud-API, Terraform and injection incident replays, a dashboard view of hook events, the Jev judge and shadow judging (the pluggable interface is already in the MVP), the Qdrant vector store (its interface is also in the MVP), the undo button (before-images are already saved), cascaded-row before-images, Docker sandboxes for shell commands, memory of past decisions, email or Slack actions, session pattern detection, a policy editor in the dashboard, observe mode, Terraform, Grafana and OpenTelemetry, a secrets vault, Redis or a job queue, a load test (latency comes from the eval runner and concurrency from one integration test, D-034), an LLM-as-judge explanation score in LangSmith (D-036), turning reviewer overrides of the judge into eval cases (the data is already in `actions`, D-043), alerts (D-044).
