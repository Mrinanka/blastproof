# Deploying the backend to Fly.io

Your checklist for putting the backend live at `https://blastproof.fly.dev` (backend plan §15,
D-261 onward). The machine runs one container: the image from `backend/Dockerfile`, configured by
`fly.toml` at the repo root. Its own data is in Neon `blastproof-app`; the fake company database is in
Neon `blastproof-company`. Every production credential lives only in **Fly secrets** (D-058).

Rules for this whole guide:

- Run the production commands yourself, in a terminal where **no Claude Code session is open**.
- Never write a production value into `backend/.env`, a file in the repo or a command-line argument.
  Secrets go in on stdin (step 5), so they never reach shell history.
- Passwords and keys appear nowhere in this file. Where a value is needed, it says where to get it.

## 0. Before you start

1. Both Neon projects must be Postgres 18 in `aws-us-east-2`. Check each project's settings page.
2. Install flyctl **0.4.123** and sign in: `fly auth login`.
3. Create the `release` branch from `develop` and protect it: pull requests only, with the checks
   `unit`, `integration`, `frontend` and `image` required. A push to `release` is what deploys.

## 1. Set up `blastproof-app` (Neon SQL Editor)

1. Make two passwords: `python -c "import secrets; print(secrets.token_urlsafe(32))"`, one for the
   owner role and one for the app role.
2. Open `deploy/neon/blastproof-app.sql`, copy it into the Neon SQL Editor on `blastproof-app`
   (database `neondb`), replace `<OWNER_PASSWORD>` and `<APP_PASSWORD>`, and run it.
3. Build the three app URLs from the **direct** endpoint (not the pooled one), with
   `sslmode=require` and **no** `channel_binding` (D-134):
   - `BLASTPROOF_MIGRATOR_URL`: `postgresql://blastproof_owner:...@<direct host>/neondb?sslmode=require`
   - `BLASTPROOF_DATABASE_URL`: `postgresql+asyncpg://blastproof_app:...@<direct host>/neondb?ssl=require`
   - `CHECKPOINTER_DATABASE_URL`: `postgresql://blastproof_app:...@<direct host>/neondb?sslmode=require`

## 2. Load `blastproof-company`

In a **separate terminal** that holds `SEED_NEON_OWNER_URL`, `COMPANY_DB_READER_URL` and
`COMPANY_DB_WRITER_URL` only in that process's environment (SEC-19):

```text
uv run --project backend python demo_company/seed.py --target neon
uv run --project backend python demo_company/seed.py --target neon --confirm <host>
```

The first command only prints the target. Check it names `blastproof-company`, then run the second.
Close the terminal afterwards. Note the project's `production` branch ID: it is
`NEON_PARENT_BRANCH_ID`.

## 3. Create the app

```text
fly apps create blastproof
```

## 4. The secrets

Every name below is a Fly secret. The first table is required (the backend refuses to start
without it); the second is optional. Nothing about models is in `fly.toml` (D-255).

| Secret | Required | What it is |
|---|---|---|
| `BLASTPROOF_DATABASE_URL` | yes | The running app, as `blastproof_app` (step 1) |
| `CHECKPOINTER_DATABASE_URL` | yes | LangGraph's checkpointer, as `blastproof_app` (step 1) |
| `BLASTPROOF_MIGRATOR_URL` | yes | `blastproof_owner`, for the release command only; the running app ignores it |
| `JWT_SECRET` | yes | At least 32 bytes: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `COMPANY_DB_READER_URL` | yes | `blastproof_reader` on `blastproof-company`, direct endpoint |
| `COMPANY_DB_WRITER_URL` | yes | `blastproof_writer` on `blastproof-company`, direct endpoint |
| `NEON_API_KEY` | yes | Project-scoped key for `blastproof-company` only |
| `NEON_PROJECT_ID` | yes | The `blastproof-company` project ID |
| `NEON_PARENT_BRANCH_ID` | yes | That project's `production` branch ID |
| `SUMMARY_MODEL` | yes | `provider:model` for the plain-language summary |
| `JUDGE_MODEL` | yes | `provider:model` for the risk judge |
| `LLM_PRICES` | yes | JSON price table covering both models |
| `SUMMARY_MODEL_OPTIONS` | no | JSON constructor options for the summary model |
| `JUDGE_MODEL_OPTIONS` | no | JSON constructor options for the judge model |
| `GOOGLE_API_KEY` | no | The model provider's key, as the models you chose need |
| `GROQ_API_KEY` | no | Same, for a Groq model |
| `LANGSMITH_TRACING` | no | `true` to send anonymised traces |
| `LANGSMITH_API_KEY` | no | LangSmith key |
| `LANGSMITH_PROJECT` | no | LangSmith project name |

Put the `NAME=VALUE` lines in a file **outside the repo**, load them from stdin, then delete the file:

```text
fly secrets import --stage < ../blastproof-secrets.env
```

`--stage` stores them without restarting anything; the deploy picks them up. Do not set
`FAKE_NEON_ADMIN_URL`, any `LOCAL_*` or any `SEED_*` name: production refuses the first and has no use
for the others.

## 5. First deploy

```text
fly deploy --ha=false
fly scale count 1
fly status
```

`--ha=false` matters: the default adds a second machine, and a second machine would run a second
runner, recovery and sweep (D-025). `fly status` must show **one** machine, in `iad`. The release
command (`alembic upgrade head`) runs first in a temporary machine; its log is JSON and holds no URL.

## 6. Probe it

```text
cd backend
uv run python -m app.cli deploy-check --url https://blastproof.fly.dev
```

It needs no key. Every line must say `ok`.

## 7. Bootstrap the first admin

```text
fly ssh console -C "python -m app.cli bootstrap --admin-email you@example.com --admin-name \"You\""
```

It prints the agent's key and setup lines **once**. Copy them now (and never paste them anywhere shared).

`fly ssh console` cannot hide what you type and the prompt may store a different password than you meant
(first deploy: login failed with the typed password). Safer: run the pipe on the machine, so the
password goes in as one clean line (letters and digits only; it lands in your shell history, so make it
a new one):

```text
fly ssh console -C "sh -c 'echo NEWPASSWORD123abc | python -m app.cli bootstrap --admin-email you@example.com --admin-name \"You\" --password-stdin'"
```

Do not run bootstrap on a 512 MB machine: it loads the embedding model beside the server and the kernel
kills the server (D-267). Keep `memory = "1gb"`. A Fly account still on the free trial stops machines
after 5 minutes: add a credit card first.

## 8. The `expires_at` check (once)

```text
fly ssh console -C "python -m app.cli preview-sql --sql \"SELECT 1\" --confirm-project blastproof-company"
```

If Neon refuses `expires_at` on the branch, tell the build session: the fix is a setting, not a
redeploy of this guide.

## 9. Point Claude Code at it

```text
uv run --project backend python claude_hook/workspace.py create <workspace> --base-url https://blastproof.fly.dev --force --key-stdin
uv run --project backend python claude_hook/workspace.py check <workspace>
```

`--key-stdin` reads until the input ends, so a bare terminal paste hangs. Put the agent key alone in a
text file outside the repo (Notepad may save it as `name.txt.txt` and may add a byte-order mark), pipe it
in with `Get-Content -Raw | ... --key-stdin`, then delete the file. The key must be exactly 46 characters
(`bp_` plus 43).

`check` must be clean: the workspace holds no company credential (SEC-19, OPS-13). Then, from Claude
Code in that workspace: a read works, `psql` is blocked, the Replit delete is held for review, and the
agent's 50 s `get_action_status` long poll survives Fly's proxy (D-101). Write down the real idle cut.

## 10. Automatic deploys

1. `fly tokens create deploy` and add the result as the secret `FLY_API_TOKEN` on the GitHub
   environment `production`, limited to the `release` branch.
2. Merge `develop` into `release` through a pull request. The **Deploy** workflow redeploys; when
   it goes green, OPS-12 is done.
3. The dashboard is a separate deploy on Vercel: see `docs/deploy-dashboard.md`.

## Tag each production deploy

After a Deploy run goes green and `deploy-check` passes, tag the commit that is live, so a version name
always points at the exact code in production (and you can roll back to it):

```text
git fetch origin
git tag -a v0.2.0 origin/release -m "v0.2.0: what changed in this deploy"
git push origin v0.2.0
```

- Use the next number each time: `v0.1.0` was the first deploy (commit `f488f9d`). A small fix is a
  patch (`v0.1.1`), new features are a minor (`v0.2.0`).
- Tag `origin/release`, never a local branch, and tag only after the checks pass.
- A tag deploys nothing: only a push to `release` runs the Deploy workflow.
- List them with `git tag -n1`. To go back, check out the tag, or open a pull request into `release`
  that reverts the change.

## Before a demo

Run `rebase-dates` against `blastproof-company` with an empty review queue (it moves every date to
today): `uv run --project backend python demo_company/rebase_dates.py --target neon --confirm <host>`.

## Known limits

- The login rate limit sees Fly's proxy address, so all users share one bucket. Safe (stricter); fixed
  when the dashboard forwards the browser's IP.
- `/docs` stays on in production (BE-119).
