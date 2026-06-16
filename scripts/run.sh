#!/usr/bin/env bash
# Run the backend locally: start Postgres, install deps, apply migrations,
# then launch the FastAPI dev server.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
    echo "No .env found - copying .env.example. Edit .env with real credentials, then re-run." >&2
    cp .env.example .env
fi

echo "==> Checking Postgres"
if command -v pg_isready > /dev/null 2>&1 && pg_isready -h localhost -p 5432 > /dev/null 2>&1; then
    echo "    Postgres already running on localhost:5432"
else
    echo "    Starting Postgres via docker compose"
    docker compose up -d postgres
    until docker compose exec -T postgres pg_isready -U postgres > /dev/null 2>&1; do
        sleep 1
    done
fi

echo "==> Installing dependencies"
uv sync --all-extras

echo "==> Applying database migrations"
uv run alembic upgrade head

echo "==> Starting FastAPI dev server"
exec uv run fastapi dev app/main.py
