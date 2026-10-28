# Blastproof

Blastproof is a safety gateway for AI agents. Every tool call an agent makes passes
through it: reads run directly; writes are previewed on a Neon database branch,
explained in plain language, judged by rules and an LLM, then auto-approved, blocked,
or held for the owner's approval before being committed with an audit trail.

The plans are the source of truth. Read the relevant one before any task:

- `docs/spec.md` - what we are building and why
- `docs/backend-plan.md` - backend MVP, built in numbered steps 1-20
- `docs/frontend-plan.md` - dashboard MVP, built in steps F1-F13
- `docs/decisions.md` - decisions made during the build (append-only)

Historical, not for implementation: `docs/plan-audit.md` and `docs/audit-response.md` record the
pre-build audit. Where they differ from the plans or `decisions.md`, the plans win.
- `PROGRESS.md` - which steps are done, what is next, known issues

## Repo layout

- `backend/` - FastAPI app: MCP server, hook API, REST API, LangGraph workflow
- `frontend/` - Next.js dashboard and public landing page
- `demo_company/` - fake customer database (schema, seed, schema docs)
- `claude_hook/` - PreToolUse hook script for Claude Code
- `docs/` - plans and decision log

## Commands

Backend (run from `backend/`):
- `docker compose up -d` (repo root) - start Blastproof's local Postgres
- `uv sync` - install dependencies
- `uv run uvicorn app.main:app --reload --env-file .env --log-config log_config.json` - run the API on :8000 (`--env-file` puts the model key where LangChain reads it; `--log-config` makes uvicorn's own startup lines JSON too)
- `uv run pytest` - fast unit tests only, no Docker (the local default, D-179); `uv run pytest --integration --skip-slow` - the local integration run, 3 minutes, without the seed-heavy files (needs Docker up; D-190); the full suite (`--integration` alone) runs only in CI and is never run locally (D-191)
- `uv run ruff check . ../demo_company ../claude_hook && uv run ruff format --check . ../demo_company ../claude_hook` - lint (as CI does)
- `uv run python -m app.cli bootstrap --admin-email ... --admin-name ...` - first admin, agent, main system and knowledge base (safe to re-run)
- `uv run alembic revision --autogenerate -m "..."` then `uv run alembic upgrade head`
- `uv run python -m evals.run` - eval suite
- `docker build -f backend/Dockerfile -t blastproof-backend:local .` (repo root), then `bash docker/image-smoke.sh blastproof-backend:local` - build and smoke-test the deploy image (CI: `image.yml`)
- `uv run python -m app.cli deploy-check --url https://blastproof.fly.dev` - read-only probe of the deployed backend; the owner's deploy checklist is `docs/deploy.md`

Frontend (run from `frontend/`):
- `npm run dev` - dashboard on :3000
- `npm run lint && npm run format:check && npm run typecheck && npm test` - checks
- `npm run gen:api` - regenerate API types from `backend/openapi.json` (no server needed); after a route change run `uv run python -m app.cli export-openapi` first

## How we work

1. Start every session by reading `PROGRESS.md` and the plan step you are asked to do.
2. Work on ONE plan step at a time. Do not start the next step unless asked.
3. Plan before code: list files, functions, tests, and anything in the step that looks
   unclear or wrong. Wait for approval before writing code.
4. If the plan seems wrong, say so and propose a change. Never silently deviate.
5. Every change comes with tests. Run tests and lint before saying a step is done.
6. Finish by running the step's "Done when" check and reporting the result honestly,
   including anything that does not work yet.
7. Update `PROGRESS.md`; add any real decision (with the reason) to `docs/decisions.md`.
8. Keep changes small and focused. Explain non-obvious code in plain language when asked.

## Architecture rules (do not break these)

- Fail closed: any error, timeout or unparseable SQL blocks the write. The one exception: if only
  the AI judge fails, the write goes to human review. A failed check never lets a write through.
- Commit exactly what was previewed: `pinned_sql` is previewed, approved and committed; the commit
  must reproduce the approved diff or roll back.
- The security boundary is credential isolation, not the hook. Hook rules are guardrails.
- Code counts, the LLM explains: numbers come from the database, never from a model.
  Every number in an LLM summary must match the computed facts.
- Rules run before the judge; the judge can only make a decision stricter, never looser.
- Reads take a fast path outside the LangGraph workflow. Only writes enter the graph.
- One SQL statement per write; reject BEGIN/COMMIT/ROLLBACK from agents.
- Ownership: every action stores the agent owner's `user_id`. All action queries go
  through `visible_actions(user)`. Another user's action returns 404, never 403.
- Company database credentials live only in the backend's environment. Never in agent
  workspaces, logs, traces, prompts or API responses.
- Mask sensitive columns (email, phone, etc.) before data reaches logs, prompts or the UI.
- Pluggable by design: `Judge`, `VectorStore` and `Embedder` are interfaces; LLMs are
  created from `provider:model` config strings. Never hard-code a provider.
- Knowledge text lives in Postgres (`knowledge_chunks`); vector stores hold only vectors.
- The backend never imports from `demo_company/`.
- The dashboard holds no business logic; the backend enforces every permission.

## Conventions

Backend:
- Python 3.13, type hints everywhere, async I/O.
- Thin routes; logic in `app/services/`; database code in `app/db/`.
- Every route declares a Pydantic response model (the frontend generates types from it).
- Errors: `{"error": {"code": "...", "message": "..."}}`, including validation errors.
- Lists: `{"items": [...], "total": n, "limit": n, "offset": n}`.
- IDs are UUID strings; timestamps are ISO 8601 UTC.
- Log with structlog; bind `request_id` and `action_id`.
- Always read and fix autogenerated Alembic migrations before applying them.

Frontend:
- TypeScript strict mode. Server data only in TanStack Query; filters in the URL.
- `src/lib/api/schema.d.ts` is generated: never edit it by hand.
- The browser calls only `/api/blastproof/*`; the proxy is the only code that knows
  `BLASTPROOF_API_URL`. Never store tokens in localStorage.
- Render agent-provided text as plain text. Never use `dangerouslySetInnerHTML`.
- Use shadcn/ui components before writing custom ones.

## Libraries and docs

- Before using these, check their CURRENT documentation, because APIs change often:
  MCP Python SDK, LangGraph (interrupts, Postgres checkpointer), Neon API (branches),
  Claude Code hooks, Vercel AI Gateway, LangChain `init_chat_model`.
- Pin exact versions. Do not add a new dependency without asking first.
- If installing on a company machine, use the approved package registry
  (e.g. Artifactory) instead of public PyPI and npm.

## Testing

- Unit tests: no network. Use fakes for LLM calls and the Neon client.
- Integration tests: real Postgres from Docker Compose.
- Authorization matrix test: every endpoint as owner, other user and admin.
- Integration tests and CI use the local `company-db` and `FakeNeonClient`; they never call Neon.
- Evals live in `backend/evals/`; the MVP incident replays (`evals/scenarios/incidents.yaml`) must
  stay at 100% caught.

## Safety while building

- Never print, copy or commit values from `.env` files or secrets.
- Only connect to the development Neon projects (`blastproof-company-dev`); never to
  `blastproof-company`, which only the deployed backend uses. Ask before any command that could
  delete or overwrite data on a Neon main branch, even the fake company database.
- Never weaken a security rule to make a test pass; report the problem instead.
