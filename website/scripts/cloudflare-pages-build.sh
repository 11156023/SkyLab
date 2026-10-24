#!/usr/bin/env bash
# Build command of the Cloudflare Pages project skylab-docs (Git integration).
#
# Pages settings (see website/README.md, "Deployment"):
#   Root directory: (empty, repository root)
#   Build command: bash website/scripts/cloudflare-pages-build.sh
#   Build output directory: website/.vitepress/dist
#   Environment variables: BUN_VERSION=1.4.4, SKIP_DEPENDENCY_INSTALL=1
set -euo pipefail

cd "$(dirname "$0")/../.."

# Pages clones the repository shallowly; the changelog page is generated from the full history.
if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
  git fetch --unshallow --quiet
fi

# The REST API page renders the backend's OpenAPI schema, exported with the backend's locked
# dependencies. The build image has no uv; install a pinned one.
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/0.9.28/install.sh | UV_NO_MODIFY_PATH=1 sh
  export PATH="$HOME/.local/bin:$PATH"
fi

(
  cd backend
  uv sync --frozen --python 3.11
  # The app is imported, not started; these values only satisfy the settings loader
  # (see backend/scripts/export_openapi.py).
  SECRET_KEY=docs-build-secret-key-not-for-production-32 \
  FIRST_SUPERUSER=admin@example.com \
  FIRST_SUPERUSER_PASSWORD=docs-build-password-not-for-production \
  EMAILS_FROM_EMAIL=noreply@example.com \
  ENVIRONMENT=local \
  PROJECT_NAME=SkyLab \
  POSTGRES_SERVER=localhost \
  POSTGRES_USER=postgres \
  POSTGRES_PASSWORD=unused \
  POSTGRES_DB=unused \
  REDIS_ENABLED=false \
  LOG_LEVEL=WARNING \
  LOG_JSON=false \
    uv run --no-sync python scripts/export_openapi.py ../website/public/openapi.json
)

cd website
bun install --frozen-lockfile
bun run build
