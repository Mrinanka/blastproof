# Setup guide

How to run Blastproof on one machine, from a fresh clone. The **local path** (sections 1 to 8) needs
no Neon account and no model key. Section 9 adds Neon. Section 10 lists known snags.

All commands run from the repo root unless a `cd` says otherwise.

## 1. Prerequisites

- Docker (Compose v2), [uv](https://docs.astral.sh/uv/) (the repo pins its version) and Python 3.13
  (uv fetches it).
- Node, the version in `frontend/.nvmrc`, for the dashboard.
- Claude Code **v2.1.295 or newer** for the agent part. Older versions let a failing hook through
  (`onFailure: "block"` is not honoured), so `workspace.py check` stays the thing you trust.
- On a company machine, install from the approved package registry, not public PyPI and npm.

## 2. The three databases

`docker compose up -d` starts two Postgres servers:

| Service | Port | Holds |
|---|---|---|
| `blastproof-db` | 5432 | Blastproof's own database: users, agents, actions, audit trail, knowledge |
| `company-db` | 5433 | The fake customer database: `company_template` (locked) and `company_main` |

The third database is Neon's company project, used only on the Neon path (section 9).

## 3. Backend `.env`

```text
cd backend
uv sync
cp .env.example .env
```

`.env` is git-ignored: never commit it or paste its values anywhere. For the local path set these
(the database values are the throwaway Compose ones shown in `.env.example`):

- `JWT_SECRET`: at least 32 bytes, from `uv run python -c "import secrets; print(secrets.token_urlsafe(48))"`.
- `COMPANY_CONNECTION_PROFILE=local` and `NEON_CLIENT=fake`.
- `LOCAL_COMPANY_WRITER_URL` and `FAKE_NEON_ADMIN_URL`: the two commented Compose values in `.env.example`.
- `BLASTPROOF_MIGRATOR_URL`, `BLASTPROOF_DATABASE_URL`, `CHECKPOINTER_DATABASE_URL`,
  `LOCAL_COMPANY_READER_URL` and `PUBLIC_BASE_URL` already hold local values.
- Leave `GOOGLE_API_KEY` empty to run without a model: the summary falls back to a template and the
  judge sends writes to review (`ai_unavailable`).

## 4. Migrations and the company data

```text
uv run alembic upgrade head
uv run python ../demo_company/seed.py --target local
```

The seed builds about 273,000 rows. It anchors dates to a fixed day; if the demo's counts look off
after some weeks, run `uv run python ../demo_company/rebase_dates.py --target local --database company_main`.

## 5. Bootstrap

```text
uv run python -m app.cli bootstrap --admin-email you@example.com --admin-name "You"
```

It asks for a password (or reads one stdin line with `--password-stdin`), then creates the first
admin, an agent named `claude-code`, the `company` system and the knowledge base (the schema scan and
`demo_company/schema_docs.md`). The agent's key and the connection lines are printed **once**; copy
them now. The command is safe to re-run: it reuses what exists and never shows a key again. If the
company database was unreachable, the rest is kept and a re-run finishes the scan. The first run
downloads a 67 MB embedding model (`EMBEDDING_CACHE_DIR` chooses where).

A system stored with another profile than `COMPANY_CONNECTION_PROFILE` is refused, not rewritten.

## 6. Demo actions

```text
uv run python -m app.cli seed-demo-actions
```

Creates two demo members and actions in every status, two of them waiting for review. It takes about
70 seconds and asks for a password for the demo users. Local profile only.

## 7. Run the backend and the dashboard

```text
uv run uvicorn app.main:app --reload --env-file .env --log-config log_config.json
```

Use `--reload` on Windows even if you do not edit code: it is what gives the server the event loop
the checkpointer needs. Open <http://localhost:8000/docs>, call `POST /v1/auth/login` with the admin,
and use `GET /v1/actions`, then approve or reject a held action.

```text
cd ../frontend
cp .env.example .env.local
npm ci
npm run dev
```

The dashboard is on <http://localhost:3000>. Its pages after login are placeholders for now.

## 8. Connect Claude Code

From the repo root. The key goes through stdin so it never reaches shell history:

```text
uv run --project backend python claude_hook/workspace.py create ../blastproof-demo --key-stdin
uv run --project backend python claude_hook/workspace.py check ../blastproof-demo
```

`create` writes the MCP server and the PreToolUse hook into the workspace. Start `claude` in that
folder once by hand, trust it and approve the `blastproof` server. Then ask it to clean up inactive
users: the write is held until you approve it. Ask it to run `psql`: the hook blocks it. Details are
in [`claude_hook/README.md`](../claude_hook/README.md).

To use your own project instead, run `claude mcp add --transport http blastproof <url>/mcp --header
"Authorization: Bearer <key>"` (`bootstrap` prints the exact line) and install the hook from
`claude_hook/settings.example.json`. Keep the company database credentials out of that folder: the
boundary is credential isolation, not the hook.

## 9. The Neon path

Use only the development project (`blastproof-company-dev`), never `blastproof-company`.
Set `SEED_NEON_OWNER_URL`, `COMPANY_DB_READER_URL`, `COMPANY_DB_WRITER_URL`, `NEON_PROJECT_ID`,
`NEON_PARENT_BRANCH_ID` and `NEON_API_KEY`, then `COMPANY_CONNECTION_PROFILE=neon` and
`NEON_CLIENT=api`. Load the data with `seed.py --target neon` (it only prints the target until you
add `--confirm <host>`), then run `bootstrap` as above. A write preview on a real branch takes
about 45 seconds for a mass delete. Add `GOOGLE_API_KEY` (and start with `--env-file .env`) to get
real summaries and judging.

## 10. Troubleshooting

- **Ports 5432 or 5433 are taken:** another Postgres is running; stop it or change the Compose ports.
- **`bootstrap` says the company database could not be scanned:** check `LOCAL_COMPANY_READER_URL`
  and that `seed.py --target local` ran. Your admin and agent are kept; run it again.
- **`bootstrap` says the main system is stored with another profile:** `COMPANY_CONNECTION_PROFILE`
  differs from the first run. Set it back, or start from a fresh Blastproof database.
- **Counts look old after a few weeks:** run `rebase_dates.py` (section 4).
- **Windows, uvicorn errors about the event loop:** run it with `--reload` (section 7).
- **The hook does not block anything:** check your Claude Code version (section 1) and run
  `workspace.py check`.
