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
- [ ] **Anthropic Console** (console.anthropic.com): create an API key for Claude models (the default; any provider LangChain supports also works, with its own key). Set a monthly spend limit (e.g. $50).
- [ ] **Neon** (neon.tech): two projects. `blastproof-company` holds the fake company database (the one agents act on, and the one we branch). `blastproof-app` holds Blastproof's own database for production. Create a Neon API key and note the `blastproof-company` project ID and its main branch ID.
- [ ] **LangSmith** (smith.langchain.com): create an API key and a project named `blastproof-dev`.
- [ ] **Embeddings:** pick one. Default: a free local model via `fastembed` (no key needed). Alternative: Voyage AI, which Anthropic recommends for embeddings (needs an API key).
- [ ] **Fly.io** (fly.io): account and the `flyctl` CLI, for deployment in the last week.
- [ ] **Vercel AI Gateway** (vercel.com, needed right after the MVP for the Jev judge): enable AI Gateway, create an API key, and add a payment card, which the Gateway requires before serving requests.

**Tools on your machine:**

- [ ] Python 3.12 and `uv` (package manager).
- [ ] Docker Desktop (runs Blastproof's local Postgres).
- [ ] Node.js and Claude Code (the agent you will protect).
- [ ] `psql` or a GUI like TablePlus to inspect databases.
- [ ] An editor with Ruff support.


## 3. Tech stack and libraries

Every library below is a common industry choice with good documentation; nothing exotic.

| Purpose | Library / tool | Notes |
| --- | --- | --- |
| Web framework | FastAPI + Uvicorn | REST API, hook endpoint, hosts the MCP server |
| MCP server | `mcp` (official Python SDK, FastMCP) | Streamable HTTP transport, mounted inside FastAPI at `/mcp` |
| Settings | `pydantic-settings` | Reads environment variables into a typed config object |
| Blastproof DB access | SQLAlchemy 2.0 (async) + `asyncpg` | Models and queries for Blastproof's own tables |
| Migrations | Alembic | Versioned schema changes |
| Company DB access | `asyncpg` (raw SQL) | Runs agent SQL on Neon branches and main |
| SQL parsing | `sqlglot` | Classify statements, find tables, add `RETURNING *` |
| Neon API | `httpx` | Create and delete branches |
| LLM calls | `langchain-anthropic` | Created with init\_chat\_model from provider:model strings in config, so any provider works (add e.g. langchain-google-genai or langchain-openai); structured output into Pydantic models |
| Workflow | `langgraph` + `langgraph-checkpoint-postgres` | Approval flow with interrupt and Postgres checkpoints |
| Vectors | `pgvector` (extension + Python package) | Default vector store behind a VectorStore interface; qdrant-client for the swappable Qdrant implementation |
| Embeddings | `fastembed` (or `voyageai`) | Local free model by default |
| Tracing | `langsmith` | Automatic for LangChain and LangGraph calls |
| Auth | `pyjwt`, `pwdlib` (argon2) | Dashboard login tokens and password hashing |
| Policy files | `pyyaml` | Loads rules from policies/rules.yaml |
| Logging | `structlog` | JSON logs with `action_id` on every line |
| Seed data | `faker` | Realistic fake company data |
| Testing | `pytest`, `pytest-asyncio`, `httpx` test client | Unit, integration and eval tests |
| Quality | `ruff` (lint + format), `mypy` (optional) | Run in CI |

**Models:** defaults are Claude Haiku for impact summaries (fast, cheap) and Claude Sonnet for the LLM judge (better reasoning), but both are provider:model strings in config (SUMMARY\_MODEL, JUDGE\_MODEL), so Gemini, GPT or others work with no code change. Rule: run the full eval suite with a new model and compare before switching. The embedding model is the exception: changing it means re-embedding every chunk and possibly a migration for the vector size, so pick it once. The risk judge is pluggable: Jev (TypeSafe AI), called through Vercel AI Gateway's HTTP API with httpx, is the second implementation.

## 4. Project structure

A standard layered layout: routes are thin, business logic lives in `services`, database code lives in `db`. Each folder has one job.

```text
blastproof/
  backend/
    app/
      main.py              # creates FastAPI app, mounts MCP, adds routers
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
        commit.py          # drift check, before-image, apply
        hook_rules.py      # shell and file rules for Claude Code
        audit.py           # writes audit events
      workflow/
        graph.py           # LangGraph state, nodes, edges
        runner.py          # start / resume workflows, wait for result
      schemas/             # Pydantic request and response models
    migrations/            # Alembic
    policies/rules.yaml    # policy rules
    tests/                 # unit, integration
    evals/                 # scenarios + eval runner
    Dockerfile
    pyproject.toml
  frontend/                # Next.js dashboard (frontend plan)
  demo_company/            # schema.sql, seed.py, schema_docs.md
  claude_hook/             # pre_tool_use.py + example .claude/settings.json
  docker-compose.yml
  .github/workflows/ci.yml
```

## 5. Data model

Blastproof's own database has eight tables, plus the checkpoint tables LangGraph creates for itself. The company database is separate and untouched by this schema.

| Table | Key columns | Purpose |
| --- | --- | --- |
| `users` | id, email, password\_hash, role (admin, member) | Dashboard logins |
| `agents` | id, owner\_id, name, api\_key\_hash, key\_prefix, is\_active, created\_at | One row per agent, owned by one user; only the hash of the key is stored |
| `systems` | id, name, kind (postgres), neon\_project\_id, neon\_main\_branch\_id | The company databases Blastproof protects; credentials stay in env vars for the MVP |
| `actions` | id, user\_id, agent\_id, system\_id, session\_id, tool, sql, statement\_type, tables, status, decision, risk\_level, reason, impact (jsonb), summary, decided\_by, created\_at, decided\_at | One row per intercepted action; the heart of the system |
| `audit_events` | id, action\_id, event\_type, data (jsonb), created\_at | Append-only timeline: received, classified, shadow\_done, decided, approved, committed, failed |
| `before_images` | id, action\_id, table\_name, primary\_key (jsonb), row\_before (jsonb), row\_after (jsonb) | Saved at commit; powers the drift check now and undo later |
| `knowledge_chunks` | id, source, table\_name, content, created\_at | Source of truth for knowledge text and metadata, whichever vector store is used |
| `chunk_embeddings` | chunk\_id, embedding (vector) | Used only by the pgvector store; Qdrant keeps vectors in its own collection |

**Action status values:** `received` -> `classified` -> `previewing` -> `awaiting_review` -> `approved` or `rejected` or `blocked` -> `committed` or `failed`. Reads go `received` -> `executed`.

**Rules:** the app's database role cannot `UPDATE` or `DELETE` on `audit_events`, which makes it append-only. Add an HNSW index on `chunk_embeddings.embedding`.

**Ownership (access model for the MVP):** every agent key belongs to one user. When an agent calls Blastproof, the backend looks up its key, finds the agent and its owner, and stores the owner's `user_id` on the action. Each user sees and decides only actions with their own `user_id`; admins see everything and manage users. Ownership uses the user's ID, never their email, because IDs never change and emails are personal data. Team review, per-database scoping and "no approving your own agent" come later.

## 6. API surface

Three doors into the backend: MCP tools for agents, one hook endpoint for Claude Code, and a REST API for the dashboard.

**MCP tools** (at `/mcp`, header `Authorization: Bearer <agent key>`)

| Tool | Input | Returns |
| --- | --- | --- |
| `list_tables` | none | Table names with one-line descriptions |
| `describe_table` | table name | Columns, types, notes from knowledge |
| `query_database` | `sql`, `intent` (what the agent is trying to do) | Rows for reads; for writes, the final result, or `pending` with an `action_id` |
| `get_action_status` | `action_id` | Status, decision and reason |

**Waiting rule (design decision):** `query_database` waits up to 20 seconds for a decision. Auto-approved or blocked writes finish inside that window. If human review is needed, it returns `pending` plus the `action_id`, and Claude checks back with `get_action_status`. Simple, and no long-held connections.

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
| POST | `/v1/agents` | Creates an agent owned by you; returns the API key once | signed in |
| POST | `/v1/agents/{id}/deactivate` | Disables one of your agent keys | owner or admin |
| GET | `/v1/users` | All users | admin |
| POST | `/v1/users` | Creates a user with an initial password | admin |
| GET | `/health` | Liveness and database check | public |

**Conventions shared with the dashboard:** every error, including validation errors (a custom handler replaces FastAPI's default 422 shape), uses `{"error": {"code": "...", "message": "..."}}`; list endpoints return `{"items": [...], "total": n, "limit": n, "offset": n}`; timestamps are ISO 8601 in UTC; IDs are UUID strings; a 409 on approve or reject names who already decided. Every route declares a Pydantic response model, because the dashboard generates its TypeScript types from `/openapi.json`, which also serves as the API reference at `/docs`.

## 7. Action pipeline

Reads skip this graph and run directly with the read-only role. Every write runs through this LangGraph workflow, one graph run per action.

&#91;embedded content: LangGraph write pipeline · 11 nodes, 3 outcomes\]

Rules can block at two points, the judge can only make a decision stricter, and the human review node pauses the graph with its state saved in Postgres until a reviewer approves or rejects.

## 8. Build steps A: foundation

Steps 1 to 7 give you a dumb pipe: Claude Code reads the company database through Blastproof, and every call is recorded. Each step ends with a test you can run.

1. **Repo and skeleton.** `uv init`, add the libraries from section 3, create `main.py` with a `/health` route, `config.py` with pydantic-settings, structlog JSON logging, Ruff and pytest. Add `docker-compose.yml` with one service: Blastproof's Postgres using the `pgvector/pgvector:pg16` image.
   *Done when:* `docker compose up -d`, then `uv run uvicorn app.main:app --reload`, and `/health` returns `ok`.
2. **CI.** A GitHub Actions workflow that runs `ruff check`, `ruff format --check` and `pytest`, with a Postgres service container for tests.
   *Done when:* a pull request shows a green check.
3. **Demo company database.** In `demo_company/`, write `schema.sql` with about 10 tables: `users` (with plan `free/pro/enterprise` and `last_login_at`), `organizations`, `subscriptions`, `payments`, `invoices`, `products`, `orders`, `order_items`, `support_tickets`, `feature_flags`. Give orders an `ON DELETE CASCADE` to `users` so cascades exist. Write `seed.py` with Faker: about 50,000 users, deliberately including inactive paying customers (the trap in the demo). Load it into the Neon `blastproof-company` main branch. Create two Postgres roles: `blastproof_reader` (SELECT only) and `blastproof_writer` (SELECT, INSERT, UPDATE, DELETE). Write `schema_docs.md` describing each table and the business rules, e.g. "a plan in (pro, enterprise) means a paying customer; enterprise accounts have contracts and must never be deleted."
   *Done when:* the seed runs, and a count query by plan on Neon returns sensible numbers.
4. **Blastproof data model.** SQLAlchemy models for the eight tables in section 5. First Alembic migration also runs `CREATE EXTENSION vector` and revokes UPDATE and DELETE on `audit_events` from the app role.
   *Done when:* `alembic upgrade head` creates all tables locally.
5. **Auth.** Agent keys: generate `bp_` + `secrets.token_urlsafe(32)`, store only its SHA-256 hash and the first 8 characters, show the full key once. Add a CLI command `create-agent` and a FastAPI dependency `get_current_agent` that checks the bearer key. Dashboard users: a `create-user` CLI, argon2 password hashes, `/v1/auth/login` returning a JWT (8-hour expiry), and a `require_admin` dependency.
   *Done when:* tests prove a wrong key or token returns 401 and an admin-only route rejects members with 403.
6. **MCP server.** Create a FastMCP server with the four tools from section 6 and mount it at `/mcp` (follow the SDK README for mounting into FastAPI, including its lifespan). Add middleware that checks the agent key on `/mcp`. For now `query_database` runs reads with the `blastproof_reader` role (statement timeout 5 s, max 500 rows) and refuses writes with "not supported yet." Look up the key's agent and its owner, and record every call, with the owner's `user_id`, in `actions` and `audit_events`.
   *Done when:* after `claude mcp add --transport http blastproof http://localhost:8000/mcp --header "Authorization: Bearer <key>"` (check the Claude Code docs for exact syntax), Claude answers "how many pro users do we have?" through Blastproof, and the call appears in `actions`.
7. **Remove direct access.** Create a separate demo workspace folder for Claude Code with no database URL anywhere: not in `.env`, not in shell variables. Only Blastproof's own environment holds the company database credentials.
   *Done when:* searching the demo workspace and its environment for "postgres" finds nothing.

## 9. Build steps B: the safety pipeline

Steps 8 to 13 build the pieces that judge a write. Build and unit-test each one on its own; step 14 wires them together.

8. **Classifier** (`services/classifier.py`). Parse with sqlglot (Postgres dialect) and return `kind` (read, write, ddl, unknown), statement types, tables, `has_where`, `multi_statement`, and `non_deterministic` (uses `now()`, `random()`, sequences). Walk the whole tree, so a DELETE hidden inside a CTE counts as a write. A parse error returns `unknown`, which is treated as a dangerous write. Side-effect functions like `pg_terminate_backend` count as writes.
   *Done when:* about 30 table-driven unit tests pass, including the tricky cases.
9. **Policy rules** (`services/policy.py`, `policies/rules.yaml`). Each rule has a name, conditions, an outcome (allow, review, block) and a reason. Evaluate every rule; the strictest outcome wins (block, then review, then allow). Any write that matches no rule defaults to review. Rules run twice: before the shadow run (decidable from SQL alone, like DROP TABLE) and after it (row counts, cascades). The same file lists `important_columns` per table (e.g. `users: [plan]`) for the impact breakdown. Load at startup; restart to apply changes.
   *Done when:* unit tests cover each rule and the strictest-wins logic.

```yaml
rules:
  - name: No DROP or TRUNCATE
    when: { statement_in: [drop, truncate] }
    then: block
    reason: Dropping or truncating tables is never allowed for agents.
  - name: Delete or update without WHERE
    when: { statement_in: [delete, update], has_where: false }
    then: block
    reason: Affects every row in the table.
  - name: Large change
    when: { rows_affected_gt: 1000 }
    then: review
    reason: More than 1,000 rows would change.
  - name: Small insert
    when: { statement_in: [insert], rows_affected_lte: 10 }
    then: allow
    reason: Small insert.
important_columns:
  users: [plan]
```

10. **Neon client and shadow executor** (`services/neon.py`, `services/shadow.py`). The Neon client creates a branch from main named `shadow-<action_id>` with a read-write endpoint, waits until it is ready, returns its connection string, and deletes it afterward. The shadow run: count rows in every table, use sqlglot to add `RETURNING *` to the statement (for UPDATE, first select the matching rows to get the before values), execute it, count again, and compare. Tables whose counts changed beyond the target table are cascades. Output a `Diff`: per table, rows deleted, updated, inserted; affected primary keys; up to 50 sample rows. Always delete the branch in a `finally` block; on startup, delete any leftover `shadow-*` branches older than 15 minutes. Timeout: 30 seconds.
    *Done when:* a DELETE of users inactive for 6 months returns exact counts, including cascaded `orders`.
11. **Knowledge and RAG, with a swappable vector store** (`services/knowledge.py`). Three sources become chunks: an automatic schema scan (one chunk per table: columns, types, foreign keys), `schema_docs.md` split by heading, and uploads through `/v1/knowledge` using the same splitter. Chunk text and metadata (source, table name) always live in Postgres in `knowledge_chunks`, the source of truth; vectors go to whichever store `VECTOR_STORE` selects. Retrieval for an action: all chunks tagged with the affected tables (a plain SQL query), plus the top 5 by vector similarity to the agent's `intent`, de-duplicated.
    - `Embedder` interface, `embed(texts) -> vectors`: fastembed (`BAAI/bge-small-en-v1.5`, 384 dimensions) by default. Stores never embed; they only store and search vectors.
    - `VectorStore` interface: `upsert(chunk_id, vector, metadata)`, `search(vector, k, tables=None) -> [(chunk_id, score)]`, `delete(chunk_ids)`. Results are chunk IDs; text is always read from Postgres, so every store behaves the same.
    - `PgVectorStore` (default): the `chunk_embeddings` table with an HNSW index.
    - `QdrantStore` (after the MVP): one collection, `blastproof_knowledge`, with `table_name` and `source` as payload for filtering. Runs as a Docker container locally (Compose profile `qdrant`) or on Qdrant Cloud's free tier.
    - `reindex` command: rebuilds the active store from `knowledge_chunks`. Run it after switching `VECTOR_STORE` or the embedding model.
    - Contract tests: one pytest suite runs against every `VectorStore` implementation, so a new store must pass the same checks.
    *Done when:* an action deleting users retrieves the "paying customer" and "enterprise contracts" rules; once the Qdrant store exists, switching `VECTOR_STORE` and running `reindex` gives the same results on the eval set.
12. **Impact engine** (`services/impact.py`). Code counts, the LLM explains. First, deterministic counts of affected rows by each important column (e.g. plan: free 48,201, pro 9, enterprise 3). Then the summary model (Claude Haiku by default), via `with_structured_output`, writes an `ImpactReport` (headline, key points, concerns) from those facts plus retrieved knowledge, told to use only the numbers given. Guardrail: every number in the output must appear in the facts; if not, fall back to a plain template summary.
    *Done when:* the report reads like "Deletes 48,213 users, including 12 paying customers (3 enterprise), and their orders through a cascade."
13. **Risk judge, pluggable** (`services/judge.py`). One interface, `Judge.assess(context) -> JudgeResult`, where the context holds the SQL, intent, diff facts, impact report and retrieved rules, and the result has `risk_level` (low to critical), `recommendation` (allow, review, block), `confidence` (0 to 1) and a short `reason`. The `JUDGE_PROVIDER` setting picks the implementation; the workflow never knows which one ran. Whatever the provider, the judge can only make a decision stricter: it may raise allow to review, but never lower review or block. Prompt-injection guard: SQL, intent and sample rows are passed as clearly marked untrusted data, and a simple pattern check (e.g. "ignore previous instructions" in SQL comments or intent) forces review.
    - `LLMJudge` (MVP default): any chat model set in `JUDGE_MODEL` (Claude Sonnet by default), with structured output; writes the reason itself.
    - `JevJudge` (right after the MVP): calls Jev through Vercel AI Gateway's `/v1/evaluate` HTTP API with `httpx`, asking a few boolean questions (affects paying customers? matches the stated intent? touches sensitive tables?). Code turns the probabilities into a recommendation using thresholds from config, takes `confidence` from them, and builds the reason from the questions that scored high. Request zero data retention in the provider options.
    - Shadow judging: the optional `JUDGE_SHADOW_PROVIDER` runs a second judge on the same context and logs its result to LangSmith and `audit_events`, without affecting the decision. Real comparison data at no risk.
    *Done when:* tests show the judge escalates a risky "allowed" action and cannot downgrade a blocked one, and switching `JUDGE_PROVIDER` needs no code change.

## 10. Build steps C: approval, commit, hook and dashboard API

Steps 14 to 20 connect the pieces into one flow and finish the MVP.

14. **LangGraph workflow** (`workflow/graph.py`). Writes only; reads stay on a fast path outside the graph. The state holds the action ID, SQL, intent, classification, diff, knowledge, impact, judge result, decision and reviewer note. Nodes and edges follow the diagram in section 7. The human review node calls LangGraph's `interrupt()`, which pauses and saves the state. Use `AsyncPostgresSaver` as the checkpointer (call `setup()` at startup) with `thread_id = action_id`. Each node updates `actions.status` and writes an audit event. Any exception sets the action to failed and blocks it (fail closed).
    *Done when:* a test runs one write through every branch: allow, review then approve, review then reject, block.
15. **Workflow runner** (`workflow/runner.py`). `start(action)` launches the graph with `asyncio.create_task`. `wait_for_decision(action_id, 20s)` checks the `actions` row every half second. `resume(action_id, approved, note)` continues the paused graph with `Command(resume=...)`. A small background loop every 10 minutes auto-rejects reviews older than 24 hours and fails actions stuck in `previewing` for over 5 minutes.
    *Done when:* restarting the server while an action awaits review, then approving it, still commits correctly.
16. **Commit** (`services/commit.py`). Using the `blastproof_writer` role on the real main branch, in one transaction: lock the affected rows (`SELECT ... FOR UPDATE` by primary key) and compare them with the shadow run's before rows. If anything differs, roll back, mark the action drifted and send it back through preview. Otherwise save before-images to Blastproof's database, run the SQL, check the affected row count matches the shadow run (roll back if not), commit, and write the audit event. For the MVP, before-images cover the target table only; cascaded rows are recorded as counts.
    *Done when:* an approved write commits with matching counts, and a test that changes a row between preview and commit triggers the drift path.
17. **Wire up `query_database`.** Reads run directly. Writes create an action, start the workflow, wait up to 20 seconds, then reply in words written for the agent: committed (rows changed), blocked (reason and a suggestion, such as "exclude rows where plan is pro or enterprise"), or pending (action ID and "a human is reviewing; check back with get\_action\_status").
    *Done when:* in Claude Code, "clean up inactive users" ends with the agent receiving a pending result.
18. **Claude Code hook.** `claude_hook/pre_tool_use.py` reads the tool call JSON from stdin, posts it to `/v1/hook/pre-tool-use` with `BLASTPROOF_AGENT_KEY` (a Blastproof key, not a database credential), and on deny prints the reason to stderr and exits with code 2. If Blastproof is unreachable, it also exits 2 (fail closed). Register it in `.claude/settings.json` under `PreToolUse` with the matcher `Bash|Edit|Write`. Server-side `hook_rules.py`: deny database clients (`psql`, `pg_dump`), reading `.env` or credential files, `terraform apply` or `destroy`, `git push --force`, and `rm -rf` outside the workspace; deny edits to `.env` and to `.claude/settings*.json` so the agent cannot switch off its own hook; allow and log everything else. Check the Claude Code hooks docs for the current input format.
    *Done when:* Claude trying `psql` or editing its settings file is blocked with a clear reason.
19. **Dashboard API.** The routes in section 6, with pagination, approve and reject allowed only for the action's owner or an admin, every actions query going through the central visibility rule (someone else's action returns 404), a required reason on reject, `decided_by` recorded, a 409 response naming who already decided, admin-only routes for users, systems and knowledge changes, and no CORS in production, because the dashboard reaches the API server to server through its own proxy (see the frontend plan).
    *Done when:* you can review and approve an action using only the `/docs` page, before any dashboard exists.
20. **Bootstrap and dev commands.** `bootstrap` creates an admin user, an agent and the `systems` row, runs the schema scan and ingests `schema_docs.md`. `seed-demo-actions` creates two demo users, each with an agent, and sample actions for both in every status (pending, approved, rejected, blocked, committed) without calling Neon or an LLM, for dashboard development and the Playwright test. `export-openapi` writes the OpenAPI schema to `backend/openapi.json` for the frontend's contract check.
    *Done when:* a fresh clone plus the setup steps gives a working demo.

## 11. Configuration

All settings come from environment variables, read once by `config.py`. Locally they live in `backend/.env` (git-ignored, with a committed `.env.example`); in production they are Fly.io secrets.

| Variable | Example | Used for |
| --- | --- | --- |
| `APP_ENV` | `local` | local, test or production behavior |
| `BLASTPROOF_DATABASE_URL` | `postgresql+asyncpg://...` | Blastproof's own database |
| `COMPANY_DB_READER_URL` | `postgresql://blastproof_reader:...@.../neondb` | Reads on the company database |
| `COMPANY_DB_WRITER_URL` | `postgresql://blastproof_writer:...@.../neondb` | Commits on the company database |
| `COMPANY_DB_WRITER_PASSWORD` | secret | Connecting to shadow branches as `blastproof_writer` |
| `NEON_API_KEY` | secret | Creating and deleting branches |
| `NEON_PROJECT_ID` | `proud-sky-123` | The `blastproof-company` project |
| `NEON_MAIN_BRANCH_ID` | `br-...` | Parent of every shadow branch |
| `ANTHROPIC_API_KEY` | secret | Claude calls (add the matching key, e.g. GOOGLE\_API\_KEY, for another provider) |
| `SUMMARY_MODEL` / `JUDGE_MODEL` | `anthropic:\<haiku model>` / `anthropic:\<sonnet model>` | Any provider:model string, e.g. google\_genai:\<gemini model> |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Embeddings |
| `LANGSMITH_TRACING` | `true` | Turns tracing on |
| `LANGSMITH_API_KEY` | secret | Sends traces |
| `LANGSMITH_PROJECT` | `blastproof-dev` | Groups traces |
| `JWT_SECRET` | long random string | Signing dashboard tokens |
| `CORS_ORIGINS` | `http://localhost:3000` | Only for a browser calling the API directly in local experiments; empty in production, since the dashboard uses its own proxy |
| `REVIEW_WAIT_SECONDS` | `20` | How long `query_database` waits |
| `REVIEW_TIMEOUT_HOURS` | `24` | Auto-reject unanswered reviews |
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

- **Fail closed:** any error, timeout or unknown SQL blocks the write. A global exception handler never lets a write through by accident.
- **No credentials near the agent:** company database credentials exist only in Blastproof's environment; the agent gets a Blastproof key only.
- **Least privilege:** reads use `blastproof_reader` (SELECT only), commits use `blastproof_writer` (no DDL, no role changes).
- **Read limits:** 5-second statement timeout and a 500-row cap on results returned to agents.
- **Keys and passwords:** agent keys stored as SHA-256 hashes; passwords hashed with argon2; JWTs expire after 8 hours.
- **Untrusted text:** SQL, intents, uploaded documents and sample rows are wrapped as data in every prompt and never followed as instructions.
- **Hook self-protection:** the agent cannot edit `.claude/settings*.json` or `.env`.
- **Append-only audit:** the app role cannot update or delete audit events. Ownership: every actions query goes through one function, visible\_actions(user), which filters by user\_id unless the user is an admin; a request for someone else's action returns 404, so its existence is never revealed.
- **No secrets in logs or traces:** never log connection strings or keys; mask columns tagged sensitive (email, phone) in sample rows before they reach logs, LLM prompts or LangSmith.
- **Abuse limits:** cap each agent at a sensible number of writes per minute (a simple counter in Postgres is enough).
- **Dependencies:** pin versions in `uv.lock` and enable GitHub Dependabot alerts.

## 13. Observability

Two tools are enough for the MVP: LangSmith for everything AI, and structured logs for everything else. Grafana and OpenTelemetry come in phase 2.

- **LangSmith:** set the three `LANGSMITH_*` variables and every LangChain call and LangGraph run is traced automatically. Tag each run with `action_id`, `agent_id` and `env` in its metadata, so one search shows an action's full AI trail, tokens and cost.
- **Structured logs:** structlog JSON with `request_id` and `action_id` bound on every line. Log each stage with its duration: classify, shadow, retrieve, impact, judge, decide, commit.
- **Request IDs:** middleware adds an `X-Request-ID` header to every response.
- **A stats endpoint:** `GET /v1/stats` returns counts by status and decision, average shadow-run time and LLM cost today, computed from the `actions` table. This gives the dashboard a metrics page with no extra infrastructure.
- **Health:** `/health` checks Blastproof's database; Fly.io uses it to restart the app if it fails.

## 14. Testing and evals

Tests prove the code works; evals prove the safety decisions are good. Both run in CI.

**Tests:**

- **Unit:** classifier, policy rules, hook rules, number-check guardrail, auth helpers, and an authorization matrix test that calls every endpoint as the owner, as another user and as an admin, and checks the expected result. No network; LLM calls replaced with fakes.
- **Integration:** API routes and the workflow against a real Postgres in Docker; Neon calls replaced with a local fake that copies a test database.
- **End-to-end (manual, weekly):** the demo script in Claude Code against real Neon.

**Evals** (`evals/`)

- **Incident replays:** the Replit-style mass delete, a DROP TABLE, a delete without WHERE, a `psql` bypass attempt, a hook-settings edit. Every one must be caught.
- **Labeled actions:** start with 50 SQL statements labeled allow, review or block (aim for 200 after the MVP), including subtle cases such as a WHERE clause that matches far more rows than the intent suggests.
- **Metrics:** catch rate (dangerous actions blocked or reviewed), false block rate (safe actions held), explanation accuracy (numbers correct, checked by code, plus an LLM-as-judge score in LangSmith), and added latency.
- **Running them:** `uv run python -m evals.run` prints a results table and saves it as JSON. Upload the labeled set as a LangSmith dataset to compare variants as experiments: rules only vs rules plus judge, Claude judge vs Jev judge (catch rate, false blocks, latency, cost and calibration: when a judge says 90% sure, is it right about 90% of the time?), with vs without RAG context, and pgvector vs Qdrant (retrieval accuracy and latency).

- **In CI:** unit and integration tests on every pull request. Incident replays and rule-only evals also run on every PR (no LLM cost). The full LLM eval runs nightly, by manual trigger, and before any change to SUMMARY\_MODEL or JUDGE\_MODEL, and fails if catch rate drops below 100% on incident replays.

## 15. Local infra, CI and deployment

Locally you run one Postgres container and the FastAPI app; in production the app runs on Fly.io and both databases live on Neon.

**Local:**

```yaml
# docker-compose.yml
services:
  blastproof-db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: blastproof
      POSTGRES_PASSWORD: blastproof
      POSTGRES_DB: blastproof
    ports: ["5432:5432"]
    volumes: [blastproof-data:/var/lib/postgresql/data]
volumes:
  blastproof-data:
```

Run the app with `uv run uvicorn app.main:app --reload`; the company database stays on Neon even locally, because shadow runs need Neon branching.

- **CI** (`.github/workflows/ci.yml`): on every pull request, install with uv, run Ruff, run Alembic migrations against a Postgres service container, run tests and rule-only evals. A second workflow runs the LLM evals nightly using the `ANTHROPIC_API_KEY` repository secret.

**Deployment** (do this once Gate 1 passes)

1. Write a multi-stage `Dockerfile`: install dependencies with uv in a build stage, copy into a slim Python image, run as a non-root user, start Uvicorn on port 8080. Download the embedding model at build time so startup is fast.
2. In the Neon `blastproof-app` project, run `CREATE EXTENSION vector`. This becomes `BLASTPROOF_DATABASE_URL` in production.
3. `fly launch --no-deploy` to create the app, choosing a region close to your Neon region. In `fly.toml`, set `release_command = "alembic upgrade head"` and an HTTP health check on `/health`.
4. `fly secrets set` for every secret in section 11.
5. `fly deploy`, then run the bootstrap command once with `fly ssh console`.
6. Add a deploy job to GitHub Actions that runs `flyctl deploy` on every merge to `main`, using a `FLY_API_TOKEN` repository secret.
7. Point Claude Code at the deployed MCP URL (`https://<your-app>.fly.dev/mcp`) and update `BLASTPROOF_AGENT_KEY` for the hook.

Then deploy the dashboard to Vercel by following the frontend plan. Terraform, separate staging and production environments, and Grafana come in phase 2.

## 16. Schedule

Four weeks gets you to the MVP gate if nothing slips; if something does, keep the order and let the dates move.

&#91;embedded content: MVP schedule · 4 weeks, one check per week\]

Each week ends with a check you can demo. If a week runs long, cut scope inside the step (fewer seed tables, fewer eval cases) rather than skipping a step.

## 17. MVP done checklist and what is deferred

The backend MVP is done when every box below is ticked, on the deployed version, using Claude Code.

- [ ] Claude Code reads the company database only through Blastproof; no credentials exist in its workspace.
- [ ] "Clean up inactive users" is held for review with an impact report naming the paying and enterprise customers affected.
- [ ] Rejecting it sends the reason to Claude, which writes a safer query; the safer query is approved and committed with matching counts.
- [ ] DROP TABLE and delete-without-WHERE are blocked instantly with clear reasons.
- [ ] `psql` in the terminal and edits to `.claude/settings.json` are blocked by the hook.
- [ ] A change made between preview and commit triggers the drift path.
- [ ] A pending review survives a server restart.
- [ ] Every action has a complete audit timeline and a LangSmith trace. Two users each see only their own agents' actions; an admin sees all.
- [ ] Incident replays score 100% caught; catch rate and false block rate on the labeled set are recorded.
- [ ] CI is green, and merges to `main` deploy to Fly.io automatically.
- [ ] A fresh clone reaches a working demo by following the README.

**Deferred to later phases:** the Jev judge and shadow judging (the pluggable interface is already in the MVP), the Qdrant vector store (its interface is also in the MVP), the undo button (before-images are already saved), cascaded-row before-images, Docker sandboxes for shell commands, memory of past decisions, email or Slack actions, session pattern detection, a policy editor in the dashboard, observe mode, Terraform, Grafana and OpenTelemetry, a secrets vault, Redis or a job queue.
