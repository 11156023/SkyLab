#!/usr/bin/env bash
# Migration sanity check.
#
# Spins up a throw-away Postgres in a container, runs `alembic upgrade head`,
# then runs `alembic check` to ensure that the live SQLModel metadata exactly
# matches the migration history. Intended for CI to catch:
#
#   1. Migrations that fail to apply on a clean DB.
#   2. Schema drift — model changes that weren't accompanied by a new revision.
#
# Usage:
#   bash backend/scripts/check-migrations.sh

set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-cc-migration-check}"
PG_PORT="${PG_PORT:-55432}"
DB_NAME="${POSTGRES_DB:-app}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_PASSWORD="${POSTGRES_PASSWORD:-postgres}"

cleanup() {
  echo "[cleanup] removing $CONTAINER_NAME"
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[1/4] starting throw-away postgres on port $PG_PORT"
docker run --rm -d \
  --name "$CONTAINER_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASSWORD" \
  -e POSTGRES_DB="$DB_NAME" \
  -p "$PG_PORT:5432" \
  postgres:18-alpine >/dev/null

echo "[2/4] waiting for postgres to accept connections"
# Probe over TCP: during first-run init the image's temporary server listens on
# the unix socket only, so a socket probe reports ready before the real server
# (the one alembic reaches over TCP) is up.
for _ in {1..30}; do
  if docker exec "$CONTAINER_NAME" pg_isready -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

export POSTGRES_SERVER="127.0.0.1"
export POSTGRES_PORT="$PG_PORT"
export POSTGRES_USER="$DB_USER"
export POSTGRES_PASSWORD="$DB_PASSWORD"
export POSTGRES_DB="$DB_NAME"

cd "$(dirname "$0")/.."

echo "[3/4] alembic upgrade head"
uv run alembic upgrade head

echo "[4/4] alembic check (autogenerate diff must be empty)"
uv run alembic check

echo "OK — migrations apply cleanly and schema is in sync."
