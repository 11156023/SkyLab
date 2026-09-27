#!/usr/bin/env bash
# Create the isolated LiteLLM PostgreSQL role/database without touching the
# Campus application database, tables, or Alembic history.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: export LITELLM_DB_PASSWORD=<secret> && ./scripts/init-litellm-db.sh [options]

Options:
  --env-file PATH   Compose environment file (default: .env)
  --database NAME   LiteLLM database name (default: $LITELLM_DB_NAME or litellm)
  --role NAME       LiteLLM login role (default: $LITELLM_DB_USER or litellm)
  --verify-only     Verify an existing isolated database/role; do not create it
  -h, --help        Show this help

The role password must be supplied through LITELLM_DB_PASSWORD. It is never
printed; an existing role is realigned to it so DATABASE_URL stays the source
of truth. prepare-ai-stack.sh --start runs this automatically for db:5432. The script connects as the Compose PostgreSQL administrator inside
the db container; it never invokes Campus Alembic or connects to the Campus
application database for writes.
EOF
}

env_file=".env"
database="${LITELLM_DB_NAME:-litellm}"
role="${LITELLM_DB_USER:-litellm}"
verify_only=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      env_file="$2"
      shift 2
      ;;
    --database)
      database="$2"
      shift 2
      ;;
    --role)
      role="$2"
      shift 2
      ;;
    --verify-only)
      verify_only=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$env_file" ]]; then
  printf 'Compose env file does not exist: %s\n' "$env_file" >&2
  exit 2
fi
if [[ ! "$database" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]]; then
  printf 'Database name must be a PostgreSQL identifier.\n' >&2
  exit 2
fi
if [[ ! "$role" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]]; then
  printf 'Role name must be a PostgreSQL identifier.\n' >&2
  exit 2
fi
if [[ -z "${LITELLM_DB_PASSWORD:-}" ]]; then
  printf 'LITELLM_DB_PASSWORD must be supplied by the operator or secret manager.\n' >&2
  exit 2
fi

compose=(docker compose --env-file "$env_file")

# psql interpolates :'var' only in statements read from stdin/files, never in -c.
db_owner="$("${compose[@]}" exec -T -e LITELLM_TARGET_DB="$database" db sh -ec '
  printf "%s\n" "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = :'\''db_name'\'';" |
    psql -X --set=ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
      -At -v db_name="$LITELLM_TARGET_DB"
')"
if [[ -n "$db_owner" && "$db_owner" != "$role" ]]; then
  printf 'Refusing to use existing database %s: owner is %s, expected %s.\n' \
    "$database" "$db_owner" "$role" >&2
  exit 1
fi

if [[ "$verify_only" == false ]]; then
  printf '%s\n' "$LITELLM_DB_PASSWORD" | "${compose[@]}" exec -T \
    -e LITELLM_TARGET_DB="$database" \
    -e LITELLM_TARGET_ROLE="$role" \
    db sh -ec '
      IFS= read -r LITELLM_TARGET_PASSWORD
      psql -X --set=ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
        -v db_name="$LITELLM_TARGET_DB" \
        -v role_name="$LITELLM_TARGET_ROLE" \
        -v role_password="$LITELLM_TARGET_PASSWORD" <<'\''SQL'\''
SELECT CASE
  WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'\''role_name'\'') THEN format(
    '\''ALTER ROLE %I WITH LOGIN PASSWORD %L'\'',
    :'\''role_name'\'',
    :'\''role_password'\''
  )
  ELSE format(
    '\''CREATE ROLE %I LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L'\'',
    :'\''role_name'\'',
    :'\''role_password'\''
  )
END
\gexec

SELECT format('\''CREATE DATABASE %I OWNER %I'\'', :'\''db_name'\'', :'\''role_name'\'')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'\''db_name'\'')
\gexec

SELECT format('\''REVOKE ALL ON DATABASE %I FROM PUBLIC'\'', :'\''db_name'\'')
\gexec
SELECT format('\''GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I'\'', :'\''db_name'\'', :'\''role_name'\'')
\gexec
SQL
    '
fi

if ! printf '%s\n' "$LITELLM_DB_PASSWORD" | "${compose[@]}" exec -T \
  -e LITELLM_TARGET_DB="$database" \
  -e LITELLM_TARGET_ROLE="$role" \
  db sh -ec '
    IFS= read -r PGPASSWORD
    export PGPASSWORD
    # TCP, not the trust-authenticated socket, so the password is really checked.
    psql -X --set=ON_ERROR_STOP=1 -h 127.0.0.1 -U "$LITELLM_TARGET_ROLE" -d "$LITELLM_TARGET_DB" \
      -At -c "SELECT current_database() || '\'':'\'' || current_user;"
  ' | grep -Fxq "${database}:${role}"; then
  printf 'LiteLLM role %s cannot log in to %s over TCP with the supplied password.\n' "$role" "$database" >&2
  exit 1
fi

campus_schema_create="$("${compose[@]}" exec -T -e LITELLM_TARGET_ROLE="$role" db sh -ec '
  printf "%s\n" "SELECT has_schema_privilege(:'\''role_name'\'', '\''public'\'', '\''CREATE'\'')::text;" |
    psql -X --set=ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      -At -v role_name="$LITELLM_TARGET_ROLE"
')"
if [[ "$campus_schema_create" != "false" ]]; then
  printf 'LiteLLM role unexpectedly has CREATE on the Campus public schema.\n' >&2
  exit 1
fi

printf 'LiteLLM database isolation verified: database=%s role=%s\n' "$database" "$role"
printf 'LiteLLM runs its own migrations on start; prepare-ai-stack.sh --start then provisions the Campus service Virtual Key.\n'
