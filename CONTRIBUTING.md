# Contributing to Blastproof

Thank you for your interest in contributing to Blastproof! We welcome contributions, bug reports, and suggestions.

This document provides guidelines and steps for contributing to the repository.

---

## Code of Conduct

All contributors and participants are expected to adhere to the [Code of Conduct](CODE_OF_CONDUCT.md). Please report unacceptable behavior following the guidelines described therein.

---

## Architecture Principles

Before making changes, please keep Blastproof's core principles in mind:

1. **Fail closed**: Any error, timeout, or unparseable SQL statement blocks or holds the write. A failed check never lets a dangerous write through.
2. **The security boundary is credential isolation**: Company database credentials live only in the backend's environment, never in agent workspaces, logs, traces, prompts, or API responses.
3. **Commit exactly what was previewed**: Write queries are previewed on a branch, judged, and only committed if the commit reproduces the approved preview.
4. **Code counts, the LLM explains**: Every count and number comes directly from the database, never hallucinated by a model.
5. **Rules run before the judge**: Rules can auto-approve or block; the LLM judge can only make decisions stricter, never looser.

---

## Development Setup

### Prerequisites

- Python 3.13
- [uv](https://docs.astral.sh/uv/) (v0.13.5)
- Node.js (v24 or matching `frontend/.nvmrc`) and `npm`
- Docker and Docker Compose (for local PostgreSQL database)
- Git

### Backend Setup

1. Start the local database:
   ```bash
   docker compose up -d
   ```

2. Install dependencies:
   ```bash
   cd backend
   uv sync
   ```

3. Configure environment:
   ```bash
   cp .env.example .env
   ```
   Refer to `docs/setup.md` for recommended local development values.

4. Apply migrations and bootstrap:
   ```bash
   uv run alembic upgrade head
   uv run python ../demo_company/seed.py --target local
   uv run python -m app.cli bootstrap --admin-email dev@example.com --admin-name "Dev Admin"
   uv run python -m app.cli seed-demo-actions
   ```

5. Run the development server:
   ```bash
   uv run uvicorn app.main:app --reload --env-file .env --log-config log_config.json
   ```

### Frontend Setup

1. Install dependencies:
   ```bash
   cd frontend
   npm ci
   ```

2. Start the Next.js development server:
   ```bash
   npm run dev
   ```
   Open [http://localhost:3000](http://localhost:3000) to view the dashboard.

---

## Testing & Quality Checks

Run the verification suites before submitting a pull request:

### Backend Checks

```bash
cd backend

# Run fast unit tests
uv run pytest

# Run linting and code formatting checks
uv run ruff check . ../demo_company ../claude_hook
uv run ruff format --check . ../demo_company ../claude_hook
```

### Frontend Checks

```bash
cd frontend

# Linting, formatting, type checking, and tests
npm run lint
npm run format:check
npm run typecheck
npm test
```

### Route / API Synchronization

If you modify or add any backend API routes:
1. Export the OpenAPI specification:
   ```bash
   cd backend
   uv run python -m app.cli export-openapi
   ```
2. Regenerate the TypeScript schema for the frontend:
   ```bash
   cd frontend
   npm run gen:api
   ```
3. Commit both generated files (`backend/openapi.json` and `frontend/src/lib/api/schema.d.ts`). CI will fail if generated files are stale.

---

## Pull Request Guidelines

1. **Branch off `develop`**: The primary development branch is `develop`. Branch off `develop` rather than `main`.
2. **Branch naming**:
   - `feat/description` for new features
   - `fix/description` for bug fixes
   - `docs/description` for documentation improvements
   - `chore/description` for maintenance or tool updates
3. **Keep changes focused**: Small, focused pull requests are reviewed and merged much faster.
4. **Include tests**: Every change should be covered by unit or integration tests where appropriate.
5. **Update documentation**: If your change affects APIs, CLI commands, or documentation, update the corresponding docs and check `docs/setup.md` or `README.md`.
6. **No secrets**: Never commit `.env` files, API keys, or database credentials.

---

## Reporting Issues & Vulnerabilities

- **Bug Reports & Feature Requests**: Please use the [GitHub Issue Templates](.github/ISSUE_TEMPLATE/).
- **Security Vulnerabilities**: Do **not** open public issues for security vulnerabilities. Please follow our [Security Policy](SECURITY.md).
