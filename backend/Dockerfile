# Blastproof backend image (backend plan §15). Build from the REPO ROOT, as fly.toml does:
#   docker build -f backend/Dockerfile .
# The context is an allowlist (.dockerignore): no .env, tests, evals or frontend can enter it.
# Base images are pinned by digest (D-060); Dependabot's docker entry proposes the bumps (D-263).

# The uv binary only. Its tag must equal [tool.uv] required-version in pyproject.toml.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM python:3.15.0-slim-trixie@sha256:f5b92f2a669a4c347044f29367f31983280b648250ca94dea66f98c53bc87e58 AS build
COPY --from=uv /uv /usr/local/bin/uv
# uv's Docker guide: bytecode for faster starts, copied (not linked) packages, the image's own
# Python, and no dev group.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_NO_DEV=1 \
    PATH=/app/backend/.venv/bin:$PATH
WORKDIR /app/backend

# Dependencies first, so a code change does not reinstall them.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/policies ./policies
COPY backend/alembic.ini backend/log_config.json ./

# Download the embedding model now, so the running container needs no network. The model name and
# the folder come from Settings, not a second copy here.
RUN python -c "from app.config import Settings; from app.services.embeddings import build_embedder; build_embedder(Settings(_env_file=None)).load()"

FROM python:3.15.0-slim-trixie@sha256:f5b92f2a669a4c347044f29367f31983280b648250ca94dea66f98c53bc87e58 AS runtime
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app/backend
COPY --from=build --chown=app:app /app/backend/.venv ./.venv
COPY --from=build --chown=app:app /app/backend/app ./app
COPY --from=build --chown=app:app /app/backend/migrations ./migrations
COPY --from=build --chown=app:app /app/backend/policies ./policies
COPY --from=build --chown=app:app /app/backend/alembic.ini /app/backend/log_config.json ./
COPY --from=build --chown=app:app /app/backend/.cache/fastembed ./.cache/fastembed
# bootstrap's default --schema-docs path (a Markdown file, never imported).
COPY --chown=app:app demo_company/schema_docs.md /app/demo_company/schema_docs.md

# HF_HUB_OFFLINE: the model is already in the cache; never call Hugging Face at runtime.
ENV PATH=/app/backend/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1
USER app
EXPOSE 8080
# --timeout-graceful-shutdown 10 plus the runner's 45 s drain stays under fly.toml's kill_timeout.
# No --reload and no --env-file: production settings come from Fly secrets only.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--log-config", "log_config.json", "--timeout-graceful-shutdown", "10"]
