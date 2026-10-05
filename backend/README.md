# SkyLab — Backend

> **English** | [繁體中文](./README.zh-TW.md)

The SkyLab backend is a FastAPI + SQLModel + PostgreSQL service that manages Proxmox VE: VM/LXC lifecycle, request and approval workflows, teaching classes and batch provisioning, firewall and gateway control, governance, monitoring, and the AI API gateway.

## Tech stack

- **Web framework:** FastAPI (standard ≥ 0.135) + Pydantic v2
- **ORM / migrations:** SQLModel + Alembic
- **Database:** PostgreSQL via PgBouncer (transaction pooling) + Redis (rate limiting, token revocation, arq queue, caches)
- **Background jobs:** arq worker (`worker` container); in-process fallback when `REDIS_ENABLED=false`
- **Proxmox:** proxmoxer with per-connection pools, HA failover (TCP ping) and CA handling
- **Security:** PyJWT, pwdlib (Argon2 + Bcrypt), cryptography (Fernet for stored credentials), TOTP (standard library)
- **Remote access:** websockets (VNC proxy, pure RFB handling in `infrastructure/vnc/`), Paramiko (gateway SSH, LXC terminal, PVE node commands), ldap3
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
│   │   ├── deps/               # dependency injection (auth, db, proxmox)
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
│   ├── domain/                 # placement and scheduling rules (pure functions)
│   ├── repositories/           # data access helpers
│   ├── locales/                # backend message translations
│   ├── alembic/versions/       # 200+ migrations
│   ├── email-templates/        # MJML sources and built HTML
│   ├── utils/                  # email, tokens, TOTP, login passwords, time helpers
│   ├── exceptions.py           # AppError and the global handler
│   ├── backend_pre_start.py    # wait for the database
│   ├── initial_data.py         # create the first superuser
│   └── reset_totp.py           # CLI rescue for a locked-out administrator
├── tests/                      # pytest (api/routes, services, performance)
├── scripts/                    # prestart, test, migration check, rotate_secret_key
├── alembic.ini
├── pyproject.toml
└── Dockerfile
```

Rules that keep the layout honest:

- **Routes → Services → Infrastructure**, never the other way around. Routes validate and delegate; services hold business rules; only `infrastructure/` talks to Proxmox, SSH, Redis, LDAP or LLMs.
- **Models vs schemas:** tables in `models/`, API I/O in `schemas/`.
- **Errors:** raise `AppError(status_code, message)`; the global handler turns it into the HTTP response.
- **PgBouncer transaction pooling:** never rely on session state across transactions. Advisory locks are always `pg_advisory_xact_lock` / `pg_try_advisory_xact_lock`; no `SET`, `LISTEN/NOTIFY` or temp tables.
- **Proxmox settings are per connection.** Code that works on a node must use `get_proxmox_settings_for_node(node)` or `get_proxmox_settings(connection_id)`; the parameterless form raises when no connection exists.

## API overview

All REST routes are mounted under `/api/v1` from `app/api/main.py`. Grouped by area:

| Area | Modules | Notes |
| --- | --- | --- |
| Auth and users | `login`, `users`, `setup`, `ldap_config`, `private`, `utils` | password / Google / LDAP login, TOTP challenge, refresh, first-install wizard, health checks |
| Resources | `resources`, `resource_details`, `resource_settings`, `vm`, `lxc`, `templates`, `gpu`, `quotas`, `jobs` | list, specs, RRD, snapshots, backups, create (202, clone runs in the worker), GPU mappings, quota usage, task records |
| Requests | `vm_requests`, `spec_change_requests`, `batch_provision` | request workflow, availability and placement advice, spec change approval, whole-class provisioning |
| Teaching | `teaching_classes`, `classroom`, `courses`, `course_admin`, `course_environments`, `quick_practice`, `rubric`, `teacher_judge_*` | classes, timetables, rosters, classroom monitoring and broadcast, course paths, teaching environments, quick practice, AI grading |
| Network | `firewall`, `gateway`, `reverse_proxy`, `ip_management`, `cloudflare`, `desktop_client` | firewall topology and rules, gateway (nginx, WireGuard, platform entry, certificate), domains, subnets, Cloudflare DNS, SkyLab Connect device login |
| Platform | `proxmox_config`, `monitoring`, `governance`, `mining_incidents`, `audit_logs`, `push` | PVE connections (CRUD, test, sync), overview and RRD, system health, alerts, governance config, mining incidents, audit log, Web Push |
| AI | `ai`, `ai_api`, `ai_proxy`, `ai_contextual_help`, `ai_monitoring`, `ai_navigation`, `ai_pve_log`, `ai_template_recommendation` | AI API credentials and approval, OpenAI-compatible proxy, assistants, usage monitoring |

WebSocket endpoints are registered directly on the app in `app/main.py`:

| Path | Purpose |
| --- | --- |
| `/ws/vnc/{vmid}` | Proxmox VNC proxy (JWT in query) |
| `/ws/terminal/{vmid}` | LXC terminal (Paramiko + xterm.js) |
| `/ws/jobs` | live task updates for the Jobs page |
| `/ws/classroom`, `/ws/classroom/{session_id}/watch` | classroom signalling and VNC fan-out |
| `/ws/courses/paths/{path_id}/progress` | course progress updates |

Bidirectional forwarding uses an `asyncio.Event` to signal disconnects between the two pump tasks; always check it before sending or receiving.

## Runtime

- **Lifespan** initialises Redis and starts three background loops: the VM request scheduler, the Web Push notifier and the WireGuard reconciler. Each holds a PostgreSQL transaction-scoped advisory lock, so with several replicas only one process runs a given loop.
- **Scheduler tasks** (60 s cycle) include request start/stop, auto-stop, deletion queue, resource alerts, TTL lifecycle, idle detection, mining detection and system health alerts. Behaviour is controlled by the `GovernanceConfig` singleton (`GET/PUT /governance/config`).
- **arq worker:** template conversion and cloning, VM request provisioning, one-click reset, class batch provisioning and resource deletion are queued as task records. Provisioning is deduplicated with the job id `vm_request:<id>` and limited by `provision_max_concurrency`.
- **Health:** `GET /api/v1/utils/health-check/` (liveness), `GET /api/v1/utils/health-check/ready` (DB + Redis, 503 on failure), `GET /api/v1/monitoring/system-health` (admin), `GET /metrics` (internal network only). See [`../docs/monitoring.md`](../docs/monitoring.md).
- **Middleware:** security headers (CSP, HSTS, X-Frame-Options), CORS, request context with `X-Request-ID`, Prometheus instrumentation.

## Configuration

Settings are loaded by `app/core/config.py` from the project root `.env` (`env_file="../.env"`). The commented template is `.env.example`; the main groups:

```env
# identity and secrets (must change in production)
SECRET_KEY=...                  # signs JWTs and derives the Fernet key for stored credentials
FIRST_SUPERUSER=admin@example.com
FIRST_SUPERUSER_PASSWORD=...
POSTGRES_PASSWORD=...

# environment
ENVIRONMENT=local               # local | staging | production
ENABLE_SIGNUP=true
FRONTEND_HOST=http://127.0.0.1:5173
NGINX_HOST_PORT=8082
# SKYLAB_TRUSTED_PROXY=...      # only when the platform entry on the gateway is used

# database / redis
POSTGRES_SERVER=db
POSTGRES_DB=app
REDIS_ENABLED=true
REDIS_URL=redis://redis:6379/0

# optional: System AI, AI API via LiteLLM, SMTP, Google login, Turnstile, LDAP CA, Sentry, WireGuard, logging, monitoring stack
```

Proxmox connections are **not** in `.env`: they are entered in the setup wizard or the "PVE Connections" page and stored encrypted. The legacy `PROXMOX_*` variables are ignored.

`SECRET_KEY` must be a fixed value of at least 32 characters shared by backend and worker. Rotate it with `python -m scripts.rotate_secret_key --apply` (see [`../docs/deployment.md`](../docs/deployment.md)); changing `.env` alone makes every stored credential undecryptable.

## Development

```bash
cd backend
uv sync
source .venv/bin/activate            # Windows: .venv\Scripts\activate
fastapi dev app/main.py              # http://localhost:8000/docs
```

Or run the whole stack with Docker Compose from the repository root:

```bash
docker compose watch
docker compose exec backend bash
docker compose logs backend
```

On container start `scripts/prestart.sh` waits for the database, runs `alembic upgrade head` and creates the first superuser.

## Migrations

```bash
docker compose exec backend bash
alembic revision --autogenerate -m "Add column foo to bar"
alembic upgrade head
```

Every change under `app/models/` needs a migration. Revision ids are limited to 32 characters. `audit_logs.action` is a PostgreSQL enum: adding an `AuditAction` value requires an `ALTER TYPE … ADD VALUE` migration, and removing one breaks the audit log listing.

## Tests

```bash
bash ./scripts/test.sh                                   # pytest with coverage → htmlcov/
docker compose exec backend bash scripts/tests-start.sh -x   # inside the running stack
```

The `db` fixture in `tests/conftest.py` refuses to run against a database whose name does not look like a test database; set `PYTEST_ALLOW_NON_TEST_DB=1` to override and `PYTEST_ENABLE_DB_CLEANUP=1` to enable cleanup. Tests disable Sentry. Pure-function layers (`domain/`, `services/*/policy.py`, `tests/performance/` tier 2) run without any external dependency.

CI runs the suite on Python 3.11 (`.github/workflows/backend-tests.yml`); the production image is `python:3.14-slim`.

## Code quality

```bash
uv run ruff check .
uv run ruff check --fix .
uv run ruff format .
uv run mypy .
uv run prek install -f           # pre-commit hooks
uv run prek run --all-files
```

## Email templates

`app/email-templates/src/` holds MJML sources and `build/` the compiled HTML. Edit the MJML and export with the VS Code MJML extension ("MJML: Export to HTML").

## See also

- Project overview: [`../README.md`](../README.md)
- Development guide: [`../docs/development.md`](../docs/development.md)
- Deployment guide: [`../docs/deployment.md`](../docs/deployment.md)
- Monitoring: [`../docs/monitoring.md`](../docs/monitoring.md)
