# Development Guide

This guide covers running SkyLab locally. For production see the [Deployment Guide](/guides/deployment); for the backend layout see [Backend](/guides/backend).

## Prerequisites

- Docker Engine with Compose ≥ 2.20 (the root compose file uses `include`)
- [uv](https://docs.astral.sh/uv/) for Python, [Bun](https://bun.sh/) for the frontend
- Python **3.11 or newer** for the backend (`requires-python = ">=3.11"`; the code uses `datetime.UTC`, which 3.10 lacks). CI tests on 3.11 and the production image runs 3.15; `uv sync` downloads a suitable interpreter if the system one is too old
- A reachable Proxmox VE node or cluster if you want to provision anything; the UI, auth, docs and most tests work without one

## Docker Compose

```bash
cp -n .env.example .env         # edit SECRET_KEY, FIRST_SUPERUSER*, POSTGRES_PASSWORD at least
cp -n vllm-service/litellm/.env.example vllm-service/litellm/.env   # Compose reads it even without the AI stack
docker compose up -d --build --scale litellm=0    # build the images and start the stack
```

`--scale litellm=0` leaves out LiteLLM, which cannot start until the AI stack has generated its `config.yaml`; see [AI API user manual](/guides/ai-api-user-manual) to bring it up.

The containers run the built images: no source directory is mounted and nothing reloads on change. After editing code, rebuild the affected services (`docker compose up -d --build backend worker`) or run that service on the host as described below.

Services started by the root `docker-compose.yml`:

| Service | Role | Host port |
| --- | --- | --- |
| `nginx` | single entry point: `/api`, `/ws` → backend, `/grafana/` → Grafana, everything else → frontend | `NGINX_HOST_PORT` (8082) |
| `backend` | FastAPI app (`fastapi run`, one worker process) | `BACKEND_HOST_PORT` (8000) |
| `worker` | arq background worker (clone, batch provisioning, deletion …) | – |
| `prestart` | waits for the DB, runs Alembic, creates the first superuser, then exits | – |
| `frontend` | the built bundle, served by nginx inside the container on port 8080 | 5173 |
| `db` / `pgbouncer` | PostgreSQL and the transaction-pooling proxy the app connects through | `POSTGRES_HOST_PORT` (5433) |
| `redis` | rate limiting, token revocation, arq queue, caches | `REDIS_HOST_PORT` (6379) |
| `mailcatcher` | catches all outgoing mail in development | 1080 (web), 1025 (SMTP) |
| `litellm` | AI API gateway, included from `vllm-service/litellm/docker-compose.yml` | 4000 (localhost) |
| monitoring profile | Prometheus, Grafana, Loki, Alloy, InfluxDB, exporters, cAdvisor | see [Monitoring](/guides/monitoring) |

Useful commands:

```bash
docker compose logs -f backend
docker compose exec backend bash
docker compose --profile monitoring up -d       # add the monitoring stack
docker compose down -v --remove-orphans         # wipe volumes (database included)
```

The first start takes a while: `prestart` waits for PostgreSQL and applies 200+ migrations. When the stack is up, open http://localhost:8082. On a fresh database the setup wizard (`/setup`) runs first; it creates (or takes over) the administrator, tests the Proxmox connection and configures the IP subnet. The gateway and platform-entry steps can be skipped in development.

### MailCatcher

With Compose the backend is pointed at MailCatcher (`SMTP_HOST=mailcatcher`, port 1025) unless you set `SMTP_HOST` in `.env`. Every email the backend sends (password reset, new accounts, alerts, governance notices) shows up at http://localhost:1080. Note that `SMTP_PORT=587` / `SMTP_TLS=True` from a copied production `.env` will make MailCatcher miss the mail.

## Running services on the host

The backend and frontend containers publish the same host ports the local dev servers use (8000 and 5173), so you can stop one container and run that service on the host while the rest stays in Docker. The nginx entry point (8082) only reaches the containers: while the backend runs on the host, open the Vite dev server at http://localhost:5173, which proxies `/api` and `/ws` to `localhost:8000`.

### Backend

```bash
docker compose stop backend worker
cd backend
uv sync
uv run fastapi dev app/main.py   # http://localhost:8000, Swagger at /docs
```

The repository root is a uv workspace with `backend` as its member, so `uv sync` creates the virtual environment at the repository root (`.venv`), not in `backend/`; `uv run` uses it from any directory. Do not start a globally installed `fastapi` instead: it starts fine but lacks project dependencies such as `paramiko`, so provisioning fails at runtime with "SSH backend is unavailable".

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
- Every page reads server data through TanStack Query hooks in `src/queries/*.js` (one file per domain, each hook wraps a service function and owns its query key and polling interval). The client defaults live in `src/queries/queryClient.js`: no retries, 5 s stale time, polling paused while the tab is hidden. Writes still call the service directly, then patch the cache with the `use…CacheUpdater` helpers or invalidate the key. When an event handler needs the current server state, call `queryClient.fetchQuery({ ...xxxQueryOptions(), staleTime: 0 })` so the result also lands in the shared cache.
- Loading and error screens use `useQueryView`, `useLoadError` and `useQueryErrorEffect` from `src/hooks/useQueryLoadState.js`. Do not derive loading or error state from `isPending`, `isError` or `data === undefined && isError`: with no data, TanStack Query v5 resets the status to pending and clears the error on every refetch, so the screen flashes back to the spinner on every background retry (polling or window focus) and a toast keyed on it fires again each time.
- Failure toasts depend on the message string from `useLoadFailureMessage(query, fallback)`, never on the error object (every failure is a new object). It reports a run of failures once and again only after a success.
- The client refetches on window focus. A query the page used to load only on entry and after its own writes (settings forms, SSH-backed Gateway status, per-machine lists) sets `refetchOnWindowFocus: false`. A form initialised once from a query waits for the entry refetch (`isFetchedAfterMount`) so it never starts from stale cached values.
- Data split into several queries does not stay in step on its own: if one query's polling depends on another query's data, refetch the dependent one when the state changes. Patch the cache with `setQueryData` only after `await cancelQueries(...)`, or an in-flight background refetch overwrites the write. Lists whose actions address items by position (firewall rules) disable those actions while the list is refetching and re-check the item after a confirmation dialog.
- Page tests wrap the component with `withQueryClient` from `src/queries/testing.jsx`. Query results are access-tracked, so read the fields you assert on during render, and let one timer tick pass before asserting because TanStack Query notifies on a macrotask.
- The React Compiler runs at build time (`@rolldown/plugin-babel` with `reactCompilerPreset` in `vite.config.js`), so new components do not need hand-written `useMemo`/`useCallback`. Components that break the rules of hooks are skipped silently, not broken. Code that reads a mutable instance in render (TanStack Virtual, for example) must opt out with `"use no memo"`. Keep `@babel/core` on 7: with Babel 8 the compiler skips components whose destructured parameters have defaults. A hook parameter default written as an expression or arrow function (`{ enabled = id != null }`) also makes the compiler skip the whole hook; assign the default in the body instead (`enabled: enabled ?? (id != null)`).
- Every `<table>` is built with TanStack Table v9 through `src/components/DataTable` (`DataTable`, `DataTableHead`, `DataTableCells`, and the `baseFeatures`/`sortableFeatures` sets in `features.js`). Define columns at module level: v9's `FlexRender` mounts any function `cell` as a component, so columns created during render remount every cell on every render. Cells are components and may call hooks; page-level handlers reach them through `useTable({ meta })`. A row with its own interactive state keeps its row component, renders its `<tr>` itself and passes that state to the cells with `<DataTableCells row={row} rowState={…} />`. The markup is the same as a hand-written table, so the table mixins in the style guide still apply.
- Long tables use `useVirtualRows` (`src/hooks/useVirtualRows.js`, TanStack Virtual with the window as the scroll container). Below 60 rows it renders every row; above that only the rows near the viewport exist in the DOM.
- Styles follow the [Frontend Style Guide](/guides/frontend-style-guide): SCSS Modules, `_variables` / `_mixins` injected by Vite, theme colours via `--color-*` custom properties.
- Text goes through react-i18next; keys live in `src/locales/{zh-TW,en,ja}/`. Add every key to all three locales.
- A new page also needs its AI assistant entry: a route in `backend/app/ai/navigation/catalog.py`, a screen in `backend/app/ai/contextual_help/surfaces.py` and a guide with the same id in `surface_guides.py` (the writing rules are in that file's docstring). `backend/tests/test_ai_contextual_help.py` fails when a catalog page has no explanation, when an element label, dialog title or opener no longer matches the zh-TW locale, or when the text describes a screen position.

## Environment variables

The single source of truth is `.env` at the repository root, read by Compose and by `backend/app/core/config.py`. `.env.example` documents every variable in sections (rendered as the [environment variable reference](/operations/environment)); the ones you must change before any non-local deployment are `SECRET_KEY`, `FIRST_SUPERUSER_PASSWORD` and `POSTGRES_PASSWORD` (the backend refuses to start with `changethis` when `ENVIRONMENT` is not `local`).

Proxmox credentials are **not** environment variables: they are entered in the setup wizard or on the "PVE Management" page and stored encrypted in the database.

LiteLLM has its own `vllm-service/litellm/.env` (master key, upstream keys, its database URL). Never copy the master key into the root `.env`; the backend only gets a restricted service key (`AI_API_API_KEY`). `bash scripts/prepare-ai-stack.sh --init-env` fills both files; see the [AI API User Manual](/guides/ai-api-user-manual).

Never commit `.env`.

## Database and migrations

The app connects through PgBouncer in transaction-pooling mode, so code must not rely on session state across transactions (see the [backend rules](/guides/backend#layout)).

Schema changes are generated on the host, because the backend container has no source mount and a file written inside it never reaches the repository:

```bash
cd backend
uv run alembic revision --autogenerate -m "Describe the change"
uv run alembic upgrade head
```

Alembic reads the root `.env`; point `POSTGRES_SERVER` / `POSTGRES_PORT` at the database (`localhost` and `POSTGRES_HOST_PORT`, 5433, when it runs in Compose). Migrations also run automatically on container start (`scripts/prestart.sh`). Keep revision ids at 32 characters or fewer and review autogenerated output: adding a value to a column backed by a PostgreSQL enum (`sa.Enum(...)` in `models/`, for example `TaskRecordStatus`) needs an explicit `ALTER TYPE … ADD VALUE`. The `migration-check` workflow in CI runs `backend/scripts/check-migrations.sh` (it also runs locally with Docker): it applies every migration to a throw-away PostgreSQL and fails when the models and migrations disagree.

## Tests and linting

Backend:

```bash
cd backend
uv run bash ./scripts/test.sh                # pytest + coverage (htmlcov/)
uv run pytest tests/api/routes/test_login.py  # a single file
uv run ruff check . && uv run ruff format --check .   # --fix / drop --check to apply
uv run mypy .                                # report-only in CI; check the files you changed
```

DB-backed tests use the `db` fixture, which refuses databases whose name does not look like a test database (`PYTEST_ALLOW_NON_TEST_DB=1` overrides). For a disposable local database run `postgres` and `redis` containers on spare ports and point `POSTGRES_*` / `REDIS_URL` at them. The backend image does not contain `tests/`, so the suite runs on the host, not inside the stack. Tests always disable Sentry.

Frontend:

```bash
cd frontend
bun run test          # Vitest: services, queries, hooks, components and pages
bun run build         # catches import and syntax errors
```

Pre-commit hooks (ruff, ruff-format, YAML/TOML checks, large-file check; defined in the root `.pre-commit-config.yaml`) are managed with [prek](https://prek.j178.dev/):

```bash
cd backend
uv run prek install -f        # once
uv run prek run --all-files   # on demand
```

## Local URLs

| URL | What |
| --- | --- |
| http://localhost:8082 | SkyLab through nginx (what users see) |
| http://localhost:5173 | Vite dev server when it runs on the host, otherwise the frontend container |
| http://localhost:8000/docs | Swagger UI, http://localhost:8000/redoc for ReDoc (not served when `ENVIRONMENT=production`) |
| http://localhost:1080 | MailCatcher |
| http://localhost:8082/grafana/ | Grafana (monitoring profile only) |
| http://127.0.0.1:9090 | Prometheus (monitoring profile, localhost only) |

## Common pitfalls

- **Port 80 / privileged ports:** the entry point defaults to 8082 because rootless Docker cannot bind ports below 1024. On Windows, port 80 is often held by HTTP.sys.
- **Docker subnets vs. PVE networks:** the default `docker0` 172.17/16 and Compose pools can overlap with campus PVE networks; set `bip` and `default-address-pools` in `daemon.json` if containers cannot reach a PVE host.
- **Scheduler errors without Proxmox:** until a PVE connection exists, scheduler tasks log errors every cycle. That is expected on an empty database.
- **Logging format:** use `%s`, not `%d`, for vmids, which are often strings by the time they reach the logger.
- **No silent fallbacks:** catch an exception only to re-raise or log at ERROR; never swallow it and continue with a default (this once turned a static IP into DHCP without any trace).
