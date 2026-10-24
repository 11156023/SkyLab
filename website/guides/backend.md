# SkyLab — Backend

The SkyLab backend is a FastAPI + SQLModel + PostgreSQL service that manages Proxmox VE: VM/LXC lifecycle, request and approval workflows, teaching classes and batch provisioning, firewall and gateway control, governance, monitoring, and the AI API gateway.

## Tech stack

- **Web framework:** FastAPI (`fastapi[standard]`) + Pydantic v2
- **ORM / migrations:** SQLModel + Alembic
- **Database:** PostgreSQL via PgBouncer (transaction pooling) + Redis (rate limiting, token revocation, arq queue, caches)
- **Background jobs:** arq worker (`worker` container); in-process fallback when `REDIS_ENABLED=false`
- **Proxmox:** proxmoxer with per-connection pools, HA failover (TCP ping) and CA handling
- **Security:** PyJWT, pwdlib (Argon2 + Bcrypt), cryptography (Fernet for stored credentials), TOTP (standard library)
- **Remote access:** websockets (VNC and LXC terminal proxies to the PVE websocket endpoints, pure RFB handling in `infrastructure/vnc/`), Paramiko (gateway SSH, PVE node commands such as `pct`), ldap3
- **Observability:** structured JSON logging, Prometheus metrics, Sentry
- **Tooling:** uv, pytest, ruff, mypy, prek

## Layout

```
backend/
├── app/
│   ├── main.py                 # FastAPI app, lifespan, middleware, WebSocket endpoints
│   ├── api/
│   │   ├── main.py             # router aggregation
│   │   ├── routes/             # REST endpoints (thin controllers, ~50 modules)
│   │   ├── websocket/          # VNC, terminal, jobs, classroom, course progress
│   │   ├── deps/               # dependency injection (auth, db, proxmox, rate limit, Turnstile, AI API key)
│   │   └── prometheus_sd.py    # http_sd targets for the monitoring stack
│   ├── schemas/                # Pydantic request/response schemas
│   ├── models/                 # SQLModel tables and enums only
│   ├── services/               # business logic, grouped by domain
│   │   ├── classroom/          #   VNC fan-out sessions, signalling hub
│   │   ├── course/, course_environment/, teaching/, template/
│   │   ├── governance/         #   TTL reclamation, idle detection
│   │   ├── jobs/               #   task records shown on the Jobs page
│   │   ├── llm_gateway/        #   AI API gateway / proxy (upstream is always LiteLLM)
│   │   ├── monitoring/         #   health policy, heartbeats, preflight, alerts
│   │   ├── network/            #   firewall, NAT, gateway, reverse proxy, IP management, WireGuard, platform entry, certificates
│   │   ├── notification/       #   Web Push
│   │   ├── proxmox/            #   VM/LXC provisioning, connection sync
│   │   ├── resource/           #   resource CRUD, backups, snapshots
│   │   ├── scheduling/         #   VM request scheduler (coordinator, policy, provision pool, leader lock)
│   │   ├── security/           #   mining detection
│   │   ├── system/             #   setup wizard
│   │   ├── user/               #   auth, users, TOTP, LDAP, password policy, audit log
│   │   └── vm/                 #   batch provisioning, placement, spec changes, VM requests
│   ├── infrastructure/         # external systems only
│   │   ├── proxmox/            #   API client (per-connection pools), operations, routing, TLS
│   │   ├── ssh/, ldap/, google/, cloudflare/, redis/, vnc/, ai/
│   │   ├── queue/              #   arq task modules
│   │   └── worker/             #   in-process background runner
│   ├── core/                   # config, db, security, permissions, i18n, logging, metrics, sentry, request context
│   ├── ai/                     # built-in AI assistants (PVE log, navigation, contextual help, template recommendation, teacher judge)
│   ├── features/ai/            # AI API / LiteLLM settings (AI_API_*, LITELLM_RUNTIME_*)
│   ├── domain/                 # placement, scheduling and username-policy rules (pure functions)
│   ├── repositories/           # data access helpers
│   ├── locales/                # backend message translations
│   ├── alembic/versions/       # 200+ migrations
│   ├── email-templates/        # MJML sources and built HTML
│   ├── utils/                  # email, tokens, TOTP, login passwords, time helpers
│   ├── exceptions.py           # AppError and the global handler
│   ├── backend_pre_start.py    # wait for the database
│   ├── initial_data.py         # create the first superuser
│   └── reset_totp.py           # CLI rescue for a locked-out administrator
├── config/                     # system-ai.json (AI assistants), placement.json, llm-model-profiles.yaml, username_policy.yaml
├── tests/                      # pytest (api/routes, services, performance)
├── scripts/                    # prestart, test, migration check, rotate_secret_key, export_openapi
├── alembic.ini
├── pyproject.toml
└── Dockerfile
```

Rules that keep the layout honest:

- **Routes → Services → Infrastructure**, never the other way around. Routes validate and delegate; services hold business rules; only `infrastructure/` talks to Proxmox, SSH, Redis, LDAP or LLMs.
- **Models vs schemas:** tables in `models/`, API I/O in `schemas/`.
- **Errors:** raise `AppError(status_code, message)`; the global handler turns it into the HTTP response.
- **PgBouncer transaction pooling:** never rely on session state across transactions. Advisory locks are always `pg_advisory_xact_lock` / `pg_try_advisory_xact_lock`; no `SET`, `LISTEN/NOTIFY` or temp tables.
- **Do not hold a database transaction across slow I/O.** The `get_db` session opens a transaction on its first query and keeps the connection until the response ends; `get_db` never commits. Before awaiting Proxmox, SSH or an LLM, release the connection with `end_read_transaction(session)` (`app/core/db.py`). It sends a COMMIT, so async code calls it through the threadpool, and it does nothing while the session holds unflushed writes. Synchronous DB work from async code runs through `run_db_in_threadpool` (same file) with its own short `Session(engine)`. Never call `end_read_transaction` inside a transaction holding `pg_advisory_xact_lock`: the commit releases the lock too.
- **Proxmox settings are per connection.** Code that works on a node must use `get_proxmox_settings_for_node(node)` or `get_proxmox_settings(connection_id)`; the parameterless form raises when no connection exists.

## API overview

All REST routes are mounted under `/api/v1` from `app/api/main.py`. Grouped by area:

| Area | Modules | Notes |
| --- | --- | --- |
| Auth and users | `login`, `users`, `setup`, `ldap_config`, `private`, `utils` | password / Google / LDAP login, TOTP challenge, refresh, first-install wizard, health checks |
| Resources | `resources`, `resource_details`, `resource_settings`, `vm`, `lxc`, `templates`, `gpu`, `quotas`, `jobs`, `policies` | list, specs, RRD, snapshots, backups, create (202, clone runs in the worker), GPU mappings, quota usage, task records, login username policy |
| Requests | `vm_requests`, `spec_change_requests`, `batch_provision` | request workflow, availability and placement advice, spec change approval, whole-class provisioning |
| Teaching | `teaching_classes`, `classroom`, `courses`, `course_admin`, `course_environments`, `quick_practice`, `rubric`, `teacher_judge_*` | classes, timetables, rosters, classroom monitoring and broadcast, course paths, teaching environments, quick practice, AI grading |
| Network | `firewall`, `gateway`, `reverse_proxy`, `ip_management`, `cloudflare`, `desktop_client` | firewall topology and rules, gateway (nginx, WireGuard, platform entry, certificate), domains, subnets, Cloudflare DNS, SkyLab Connect device login |
| Platform | `proxmox_config`, `monitoring`, `governance`, `mining_incidents`, `audit_logs`, `push` | PVE connections (CRUD, test, sync), overview and RRD, system health, alerts, governance config, mining incidents, audit log, Web Push |
| AI | `ai`, `ai_api`, `ai_proxy`, `ai_contextual_help`, `ai_monitoring`, `ai_navigation`, `ai_pve_log`, `ai_template_recommendation` | AI API credentials and approval, OpenAI-compatible proxy, assistants, usage monitoring |

WebSocket endpoints (VNC, LXC terminal, jobs, classroom, course progress) are registered directly on the app in `app/main.py`, not through the API router; the handlers live in `api/websocket/`. The paths are listed in [WebSocket endpoints](/reference/websocket). Bidirectional forwarding uses an `asyncio.Event` to signal disconnects between the two pump tasks; always check it before sending or receiving, or the proxy fails with "receive after disconnect".

## Runtime

- **Lifespan** initialises Redis and starts three background loops: the VM request scheduler, the Web Push notifier and the WireGuard reconciler. Each holds a PostgreSQL transaction-scoped advisory lock, so with several replicas only one process runs a given loop.
- **Scheduler tasks** (60 s cycle) include request start/stop, auto-stop, deletion queue, resource alerts, TTL lifecycle, idle detection, mining detection and system health alerts. Behaviour is controlled by the `GovernanceConfig` singleton (`GET/PUT /governance/config`).
- **arq worker:** template conversion and cloning, VM request provisioning, one-click reset, class batch provisioning, backup and restore, and resource deletion are queued as task records. Provisioning is deduplicated with the job id `vm_request:<id>` and limited by `provision_max_concurrency`.
- **Health:** `GET /api/v1/utils/health-check/` (liveness), `GET /api/v1/utils/health-check/ready` (DB + Redis, 503 on failure), `GET /api/v1/monitoring/system-health` (admin), `GET /metrics` (internal network only). See [Monitoring](/guides/monitoring).
- **Middleware:** security headers (CSP, HSTS, X-Frame-Options), CORS, request context with `X-Request-ID`, Prometheus instrumentation.

## Configuration

Settings are loaded by `app/core/config.py` from the project root `.env` (`env_file="../.env"`; unknown variables are ignored). The commented template is `.env.example`; every variable is listed in the [environment variable reference](/operations/environment).

Proxmox connections are **not** in `.env`: they are entered in the setup wizard or the "PVE Management" page and stored encrypted. The legacy `PROXMOX_*` variables are ignored.

`SECRET_KEY` must be a fixed value of at least 32 characters shared by backend and worker. Rotate it with `python -m scripts.rotate_secret_key --apply` (see the [Deployment Guide](/guides/deployment)); changing `.env` alone makes every stored credential undecryptable.

## Development

Running the backend on the host or in Docker Compose is described in the [Development Guide](/guides/development#backend). The image runs `fastapi run --workers 1`: classroom VNC sessions, presence and the VNC ticket cookies are in-process state, so scale out with more containers only after that state is moved out of the process. On container start `scripts/prestart.sh` waits for the database, runs `alembic upgrade head` and creates the first superuser.

## Migrations

Every change under `app/models/` needs a migration, generated on the host as described in the [Development Guide](/guides/development#database-and-migrations); the normalization rules for schema changes are in [Database design](/guides/database-design). Revision ids are limited to 32 characters. Columns backed by a PostgreSQL enum (`sa.Enum(...)` in `models/`) need an explicit `ALTER TYPE … ADD VALUE` migration for a new value. `audit_logs.action` is a plain string column validated against `AuditAction` on write, so new audit actions need no migration and old values stay readable.

## Tests

```bash
uv run bash ./scripts/test.sh        # pytest with coverage → htmlcov/
```

The suite runs on the host (the image does not contain `tests/`). The `db` fixture in `tests/conftest.py` refuses to run against a database whose name does not look like a test database; set `PYTEST_ALLOW_NON_TEST_DB=1` to override and `PYTEST_ENABLE_DB_CLEANUP=1` to enable cleanup. Tests disable Sentry. Pure-function layers (`domain/`, the `services/*/*policy.py` modules, `tests/performance/` tier 2) run without any external dependency.

CI runs the suite on Python 3.11 (`.github/workflows/backend-tests.yml`); the production image is `python:3.15-slim`.

## Code quality

Ruff, mypy and the prek pre-commit hooks are run as described in the [Development Guide](/guides/development#tests-and-linting). CI runs `ruff check` as a gate and mypy as a report only.

## Email templates

`app/email-templates/src/` holds MJML sources and `build/` the compiled HTML. Edit the MJML and export with the VS Code MJML extension ("MJML: Export to HTML").

## See also

- [Project overview (README)](https://github.com/SkyLab-tw/SkyLab/blob/main/README.md)
- [Development Guide](/guides/development)
- [Deployment Guide](/guides/deployment)
- [Monitoring](/guides/monitoring)
- [WebSocket endpoints](/reference/websocket)
