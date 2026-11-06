# Blastproof

**A safety gateway for AI agents.** Every tool call an agent makes passes through Blastproof.
Reads run directly. Writes are previewed on a database branch, explained in plain language, judged
by rules and an LLM, then auto-approved, blocked, or held for the owner's approval before they are
committed with an audit trail.

The agent never holds the company database's credentials, so a bad `DELETE FROM users` becomes a
held request that says "deletes 48,213 users, including 12 paying customers", not an incident.

<p align="center">
  <img src="docs/media/blastproof-demo.gif" width="800"
       alt="An agent asks to run DELETE FROM users WHERE last_login_at &lt; now() - interval '6 months'. Blastproof runs it on a copy first: it would delete 48,213 users, including 12 paying customers, so the change request is held for review.">
</p>

▶ **Watch the full 24-second film:**

https://github.com/user-attachments/assets/5eed0547-6eca-40e8-969c-cca156d4cd89

## How it works

<p align="center">
  <img src="docs/media/blastproof-architecture.gif" width="800"
       alt="Architecture: an AI agent sends a tool call to the Blastproof backend, which previews a write on a database branch, runs rules then an LLM judge, and either blocks it, holds it for the owner's approval in the dashboard, or commits what was previewed to the company database. Only the backend holds the database keys.">
</p>

- **Code counts, the LLM explains.** Every number comes from the database, never from a model.
- **Fail closed.** An error, timeout or unparseable statement blocks the write.
- **The boundary is credential isolation.** Company credentials live only in the backend.

More: [`docs/spec.md`](docs/spec.md) (what and why), [`docs/backend-plan.md`](docs/backend-plan.md),
[`docs/frontend-plan.md`](docs/frontend-plan.md), [`docs/decisions.md`](docs/decisions.md).

## Eval results

63 labelled scenarios (`backend/evals/`): 51 dangerous actions, 12 of them replays of real
incidents, and 12 safe ones. Dangerous actions must be blocked or held; safe ones must go through.

| | Rules only (every pull request) | Live, real models (2026-11-04) |
|---|---|---|
| Real-incident replays caught | 12 of 12 | 12 of 12 |
| Dangerous actions blocked or held | 51 of 51 (100%) | 51 of 51 (100%) |
| Safe actions held (false blocks) | 1 of 12 (8.3%) | 1 of 12 (8.3%) |
| Previewed impacts with the exact row counts | n/a | 37 of 37 |
| Time added to a read, p95 | n/a | 9.4 ms (100 reads) |
| Write preview time, p50 / p95 | n/a | 8.6 s / 18.2 s |
| Cost of the whole run | $0 | $0.27 |

The live run used `gpt-oss-120b` for summaries and Claude Sonnet 5.5 as the judge, through
OpenRouter, on GitHub Actions. The one false block is a ticket update that no rule allows on its
own, so it goes to review. How to run them: [`docs/setup.md`](docs/setup.md) section 10.

## Quickstart (fully local: no Neon, no model key)

You need Docker, [uv](https://docs.astral.sh/uv/), Node (see `frontend/.nvmrc`) and, for the agent
part, Claude Code **v2.1.295 or newer** (older versions let a failing hook through). The full guide,
with the Neon path and troubleshooting, is [`docs/setup.md`](docs/setup.md).

```text
docker compose up -d
cd backend
uv sync
cp .env.example .env        # then fill the values listed in docs/setup.md section 3
uv run alembic upgrade head
uv run python ../demo_company/seed.py --target local
uv run python -m app.cli bootstrap --admin-email you@example.com --admin-name "You"
uv run python -m app.cli seed-demo-actions
uv run uvicorn app.main:app --reload --env-file .env --log-config log_config.json
```

The first `bootstrap` downloads a 67 MB embedding model (set `EMBEDDING_CACHE_DIR` to choose where).
Open <http://localhost:8000/docs>, log in with `POST /v1/auth/login`, and approve or reject the seeded
actions. Without a model key the summary uses a template and the judge sends writes to human review.

The dashboard (`cd frontend && npm ci && npm run dev`, <http://localhost:3000>) has login,
navigation, shared components and every page up to Settings (Agents, Users, Rules); the polish pass and landing page come next.

## Connect Claude Code

`bootstrap` prints the agent's key once. Create a workspace that holds only that key, with the hook
installed, and start Claude Code in it (see [`claude_hook/README.md`](claude_hook/README.md)):

```text
uv run --project backend python claude_hook/workspace.py create ../blastproof-demo --key-stdin
uv run --project backend python claude_hook/workspace.py check ../blastproof-demo
```

## Development

Checks, from `backend/` and `frontend/`:

```text
uv run pytest
uv run ruff check . ../demo_company ../claude_hook
cd ../frontend && npm run lint && npm run typecheck && npm test
```

After changing a route: `uv run python -m app.cli export-openapi`, then `npm run gen:api` in
`frontend/`. CI fails if either generated file is stale. Progress and known issues:
[`PROGRESS.md`](PROGRESS.md).

> Status: the backend (steps 1 to 20) is live on Fly.io (checklist: [`docs/deploy.md`](docs/deploy.md))
> and the dashboard and landing page (F1 to F13) on Vercel at https://blastproof.in
> ([`docs/deploy-dashboard.md`](docs/deploy-dashboard.md)). The evals are green with real models
> (above). Still to come: some by-hand checks, the final audit and the two-to-three-minute demo
> video.
