# Development Guide

> **English** | [繁體中文](./development.zh-TW.md)

This guide covers running SkyLab locally. For production see [`deployment.md`](deployment.md); for the backend layout see [`../backend/README.md`](../backend/README.md).

## Prerequisites

- Docker Engine with Compose ≥ 2.20 (the root compose file uses `include`)
- [uv](https://docs.astral.sh/uv/) for Python, [Bun](https://bun.sh/) for the frontend
- A reachable Proxmox VE node or cluster if you want to provision anything; the UI, auth, docs and most tests work without one

## Docker Compose

```bash
cp -n .env.example .env         # edit SECRET_KEY, FIRST_SUPERUSER*, POSTGRES_PASSWORD at least
docker compose watch            # build, start, and sync source changes into the containers
```

Services started by the root `docker-compose.yml`:

| Service | Role | Host port |
| --- | --- | --- |
| `nginx` | single entry point: `/api`, `/ws` → backend, `/grafana/` → Grafana, everything else → frontend | `NGINX_HOST_PORT` (8082) |
| `backend` | FastAPI app | `BACKEND_HOST_PORT` (8000) |
| `worker` | arq background worker (clone, batch provisioning, deletion …) | – |
| `prestart` | waits for the DB, runs Alembic, creates the first superuser, then exits | – |
| `frontend` | Vite dev server (production image serves the built bundle) | 5173 |
| `db` / `pgbouncer` | PostgreSQL and the transaction-pooling proxy the app connects through | `POSTGRES_HOST_PORT` (5433) |
| `redis` | rate limiting, token revocation, arq queue, caches | `REDIS_HOST_PORT` (6379) |
| `mailcatcher` | catches all outgoing mail in development | 1080 (web), 1025 (SMTP) |
| `litellm` | AI API gateway, included from `vllm-service/litellm/docker-compose.yml` | 4000 (localhost) |
| monitoring profile | Prometheus, Grafana, Loki, Alloy, InfluxDB, exporters, cAdvisor | see [`monitoring.md`](monitoring.md) |

Useful commands:

```bash
docker compose logs -f backend
docker compose exec backend bash
docker compose --profile monitoring up -d       # add the monitoring stack
docker compose down -v --remove-orphans         # wipe volumes (database included)
```

The first start takes a while: `prestart` waits for PostgreSQL and applies 200+ migrations. When the stack is up, open http://localhost:8082. On a fresh database the setup wizard (`/setup`) runs first; it creates (or takes over) the administrator, tests the Proxmox connection and configures the IP subnet. The gateway and platform-entry steps can be skipped in development.

### MailCatcher

With Compose the backend is pointed at MailCatcher (`SMTP_HOST=mailcatcher`, port 1025) unless you set `SMTP_HOST` in `.env`. Every email the backend sends (password reset, approvals, alerts) shows up at http://localhost:1080. Note that `SMTP_PORT=587` / `SMTP_TLS=True` from a copied production `.env` will make MailCatcher miss the mail.

## Running services on the host

Each service uses the same port inside Compose and on the host, so you can stop one container and run that service locally while the rest stays in Docker.

### Backend

```bash
docker compose stop backend worker
cd backend
uv sync
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
fastapi dev app/main.py          # http://localhost:8000, Swagger at /docs
```

Activate the virtual environment first. A globally installed `fastapi` starts fine but lacks project dependencies such as `paramiko`, so provisioning fails at runtime with "SSH backend is unavailable".

The host process reads the root `.env` (`env_file="../.env"`). Point `POSTGRES_SERVER` at `localhost` and `POSTGRES_PORT` at `POSTGRES_HOST_PORT` (5433), `REDIS_URL` at `redis://localhost:6379/0`, and `AI_API_BASE_URL` / `LITELLM_RUNTIME_BASE_URL` at `http://127.0.0.1:4000` when the rest of the stack is in Docker. The worker is a separate process: `uv run arq app.infrastructure.queue.worker.WorkerSettings`, or set `REDIS_ENABLED=false` to run background tasks in-process.

### Frontend

```bash
docker compose stop frontend
cd frontend
bun install
bun run dev                      # http://localhost:5173
```

The Vite dev server proxies `/api` and `/ws` to `http://localhost:8000`. Set `VITE_API_URL` to target a different backend. Other scripts: `bun run build` (production bundle), `bun run preview`, `bun run test` (Vitest).

Frontend conventions:

- Routing is centralised in `src/App.jsx`; sidebar entries in `src/components/Sidebar/Sidebar.jsx`.
- Pages live under `src/pages/<area>/<Feature>/XxxPage.jsx` with a sibling `.module.scss`.
- There is no generated API client. Every endpoint has a hand-written function in `src/services/*.js` built on `apiGet/apiPost/...` from `src/services/api.js` (token, 401 refresh), with a Vitest mock test next to it. Pages never call `fetch` directly.
- Styles follow [`frontend-style-guide.md`](frontend-style-guide.md): SCSS Modules, `_variables` / `_mixins` injected by Vite, theme colours via `--color-*` custom properties.
- Text goes through react-i18next; keys live in `src/locales/{zh-TW,en,ja}/`. Add every key to all three locales.

## Environment variables

The single source of truth is `.env` at the repository root, read by Compose and by `backend/app/core/config.py`. `.env.example` documents every variable in sections; the ones you must change before any non-local deployment are `SECRET_KEY`, `FIRST_SUPERUSER_PASSWORD` and `POSTGRES_PASSWORD` (the backend refuses to start with `changethis` when `ENVIRONMENT` is not `local`).

Proxmox credentials are **not** environment variables: they are entered in the setup wizard or on the "PVE Connections" page and stored encrypted in the database.

LiteLLM has its own `vllm-service/litellm/.env` (master key, upstream keys, its database URL). Never copy the master key into the root `.env`; the backend only gets a restricted service key (`AI_API_API_KEY`). `bash scripts/prepare-ai-stack.sh --init-env` fills both files; see the [AI API User Manual](ai-api-user-manual.md).

Never commit `.env`.

## Database and migrations

The app connects through PgBouncer in transaction-pooling mode. Consequences for code:

- Advisory locks must be transaction scoped (`pg_advisory_xact_lock`), never session scoped.
- No `SET`, `LISTEN/NOTIFY` or temp tables that assume the same server connection across transactions.

Schema changes:

```bash
docker compose exec backend bash
alembic revision --autogenerate -m "Describe the change"
alembic upgrade head
```

Migrations run automatically on container start (`scripts/prestart.sh`). Keep revision ids under 32 characters and review autogenerated output: enum changes (for example `AuditAction`) need explicit `ALTER TYPE … ADD VALUE` statements. The `migration-check` workflow in CI verifies that models and migrations agree.

## Tests and linting

Backend:

```bash
cd backend
bash ./scripts/test.sh                       # pytest + coverage (htmlcov/)
uv run pytest tests/api/routes/test_login.py  # a single file
uv run ruff check . && uv run ruff format --check .
uv run mypy .
```

DB-backed tests use the `db` fixture, which refuses databases whose name does not look like a test database (`PYTEST_ALLOW_NON_TEST_DB=1` overrides). For a disposable local database run `postgres` and `redis` containers on spare ports and point `POSTGRES_*` / `REDIS_URL` at them, or run the suite inside the stack with `docker compose exec backend bash scripts/tests-start.sh -x`. Tests always disable Sentry.

Frontend:

```bash
cd frontend
bun run test          # Vitest, services layer
bun run build         # catches import and syntax errors
```

Pre-commit hooks (ruff, ruff-format, trailing whitespace, YAML/TOML checks) are managed with [prek](https://prek.j178.dev/):

```bash
cd backend
uv run prek install -f        # once
uv run prek run --all-files   # on demand
```

## Local URLs

| URL | What |
| --- | --- |
| http://localhost:8082 | SkyLab through nginx (what users see) |
| http://localhost:5173 | Vite dev server |
| http://localhost:8000/docs | Swagger UI, http://localhost:8000/redoc for ReDoc |
| http://localhost:1080 | MailCatcher |
| http://localhost:8082/grafana/ | Grafana (monitoring profile only) |
| http://127.0.0.1:9090 | Prometheus (monitoring profile, localhost only) |

## Common pitfalls

- **Port 80 / privileged ports:** the entry point defaults to 8082 because rootless Docker cannot bind ports below 1024. On Windows, port 80 is often held by HTTP.sys.
- **Docker subnets vs. PVE networks:** the default `docker0` 172.17/16 and Compose pools can overlap with campus PVE networks; set `bip` and `default-address-pools` in `daemon.json` if containers cannot reach a PVE host.
- **Scheduler errors without Proxmox:** until a PVE connection exists, scheduler tasks log errors every cycle. That is expected on an empty database.
- **WebSocket proxies:** the VNC/terminal pumps signal disconnects with an `asyncio.Event`; always check it before sending or receiving to avoid "receive after disconnect".
- **Logging format:** use `%s`, not `%d`, for vmids, which are often strings by the time they reach the logger.
- **No silent fallbacks:** catch an exception only to re-raise or log at ERROR; never swallow it and continue with a default (this once turned a static IP into DHCP without any trace).
