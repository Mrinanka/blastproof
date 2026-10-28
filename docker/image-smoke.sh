#!/usr/bin/env bash
# Smoke test for the backend image (backend plan §15). Used by CI (image.yml) and locally:
#   docker build -f backend/Dockerfile -t blastproof-backend:local .
#   bash docker/image-smoke.sh blastproof-backend:local
# Needs Docker only. Holds no secret: the containers get no credentials and, mostly, no network.
set -uo pipefail

IMAGE="${1:-blastproof-backend:local}"
NAME="bp-smoke-$$"
FAILED=0

pass() { echo "ok   $1"; }
fail() { echo "FAIL $1"; FAILED=1; }
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# (a) Not root.
uid="$(docker run --rm --entrypoint id "$IMAGE" -u)"
if [ "$uid" != "0" ] && [ -n "$uid" ]; then pass "runs as uid $uid, not root"; else fail "runs as root"; fi

# (b) With no network and no environment, the app starts and answers /health.
docker run -d --name "$NAME" --network none "$IMAGE" >/dev/null
healthy=0
for _ in $(seq 1 30); do
  if docker exec "$NAME" python -c \
    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)" \
    >/dev/null 2>&1; then healthy=1; break; fi
  sleep 2
done
if [ "$healthy" = 1 ]; then pass "GET /health answers 200 with no network and no env"; else
  fail "GET /health did not answer within 60 s"; docker logs "$NAME" 2>&1 | tail -n 20; fi
cleanup

# (c) The embedding model loads and embeds offline, as the app user.
if docker run --rm --network none "$IMAGE" python -c \
  "import asyncio; from app.config import Settings; from app.services.embeddings import build_embedder; v = asyncio.run(build_embedder(Settings(_env_file=None)).embed(['hello'])); assert len(v) == 1 and len(v[0]) > 0" \
  >/dev/null 2>&1; then pass "embedder works offline"; else fail "embedder failed offline"; fi

# (d) Production with no secrets refuses to start, and says so without leaking a value.
out="$(docker run --rm --network none -e APP_ENV=production "$IMAGE" 2>&1)"
code=$?
if [ "$code" -ne 0 ] && echo "$out" | grep -q "refusing to start"; then pass "production without secrets refuses to start"; else
  fail "production without secrets did not refuse to start (exit $code)"; fi
if echo "$out" | grep -Eq "postgres(ql)?://|bp_[A-Za-z0-9_-]{43}"; then fail "startup error shows a credential"; else pass "startup error shows no credential"; fi

# (e) No secrets files or test code in the image.
leaks="$(docker run --rm --entrypoint sh "$IMAGE" -c 'find /app -path /app/backend/.venv -prune -o \( -name ".env*" -o -name tests -o -name evals \) -print | head')"
if [ -z "$leaks" ]; then pass "no .env, tests or evals in the image"; else fail "found in image: $leaks"; fi

# (f) The migrations and their config are in the image: offline SQL generation works.
if docker run --rm --network none -e BLASTPROOF_MIGRATOR_URL=postgresql://blastproof_owner:x@localhost:1/x \
  "$IMAGE" alembic upgrade head --sql >/dev/null 2>&1; then pass "alembic upgrade head --sql runs"; else fail "alembic --sql failed"; fi

exit "$FAILED"
