# SkyLab

> **English** | [繁體中文](./README.zh-TW.md)

SkyLab is a full-stack management platform for Proxmox VE (PVE) built for campus use. It covers VM/LXC lifecycle management, request and approval workflows, class-based batch provisioning, firewall and gateway control, AI-assisted operations, and an OpenAI-compatible AI API backed by self-hosted vLLM models.

## Components

| Component | Path | Role |
| --- | --- | --- |
| Backend | `backend/` | FastAPI + SQLModel + PostgreSQL + Redis; talks to the Proxmox API and serves REST + WebSocket |
| Frontend | `frontend/` | React 19 (JSX) + Vite + react-router-dom 7 + SCSS Modules; hand-written API services |
| nginx | `nginx/` | Single entry point (`:8082` by default): `/api`, `/ws` → backend, everything else → frontend |
| Gateway installer | `gateway/` | `install.sh` for the campus egress gateway VM: nginx (port forwarding, domain reverse proxy), WireGuard, nftables ACL |
| Desktop client | `desktop-client/` | SkyLab Connect (Electron): device login and WireGuard tunnel to the gateway |
| vLLM service | `vllm-service/` | Single-model service, multi-model vLLM cluster and the LiteLLM gateway that exposes them |
| AI PVE Placement Advisor | `ai-pve-placement-advisor/` | Rule + weighted-score placement suggestion service (port 8011) |
| PVE Resource Simulator | `pve_ resource_simulator/` | Offline placement-strategy simulator (port 8012) |
| Monitoring stack | `monitoring/` | Optional Prometheus / Grafana / Loki / InfluxDB profile |
| Legacy vLLM | `vllm-inference/`, `vllm-API/` | Old single-model deployment and multi-model gateway, kept for migration reference only |
| Docs | `docs/` | Guides and design notes, see [`docs/README.md`](docs/README.md) |

## Tech stack

- **Backend:** FastAPI, SQLModel, Alembic, PostgreSQL (via PgBouncer), Redis, arq worker, proxmoxer, Paramiko, PyJWT, cryptography, httpx, websockets, Sentry
- **Frontend:** React 19, Vite, react-router-dom 7, SCSS Modules, react-i18next (en / zh-TW / ja), react-vnc, xterm.js, @xyflow/react, Recharts, Monaco Editor, sonner, Vitest
- **Infrastructure:** Docker Compose, nginx, PgBouncer, PostgreSQL, Redis, MailCatcher (dev), optional Prometheus / Grafana / Loki / Alloy / InfluxDB
- **AI:** vLLM (OpenAI-compatible API) behind LiteLLM, plus the in-house PVE Advisor and template recommendation services
- **Package managers:** uv (Python), Bun (frontend)

## Main features

- VM / LXC lifecycle: create, inspect, resize, snapshot, backup/restore, delete
- VNC and LXC terminal in the browser through WebSocket proxies (noVNC + xterm.js)
- VM request workflow: student submits → review → capacity check for the requested time window → scheduled provisioning
- Templates, teaching classes, timetables, student rosters, multi-machine teaching environments and whole-class batch provisioning
- Classroom monitoring, teacher broadcast and AI-assisted grading inside a class
- Firewall topology, NAT, reverse proxy (domain) rules, IP management, and a gateway VM managed over SSH (nginx port forwarding / domain reverse proxy / administrator-supplied HTTPS certificate / WireGuard)
- Multiple Proxmox connections (standalone nodes or clusters) with HA failover inside a cluster
- Governance: TTL-based reclamation, idle detection, resource alerts, mining detection, quotas
- AI API: `ccai_*` credentials, request approval, Redis sliding-window rate limiting, and an OpenAI-compatible proxy at `/api/v1/ai-proxy/{models,chat/completions,completions,responses}` that forwards to a restricted LiteLLM service key
- AI assistants for administrators (PVE operations) and for teaching workflows (navigation, contextual help, template recommendation)
- Authentication: password, Google, LDAP/AD, optional per-account TOTP, Cloudflare Turnstile, login pre-flight service check
- Platform health monitoring, system alerts, Web Push notifications, Prometheus `/metrics`
- Full audit log; roles: superuser / admin / instructor / student; trilingual UI (en / zh-TW / ja)

## Quick start (Docker Compose)

The root `docker-compose.yml` already includes LiteLLM. Fill in the two `.env` files first (see the [AI API User Manual](docs/ai-api-user-manual.md) for the model routing and keys), then start the whole stack:

```bash
cp -n .env.example .env
cp -n vllm-service/litellm/.env.example vllm-service/litellm/.env
# edit both .env files; local models must be started first with vllm-service/start_multi_model_cluster.sh
bash scripts/prepare-ai-stack.sh --start
```

If you do not need the AI stack yet, `docker compose up -d --build` is enough.

Default URLs:

| Service | URL |
| --- | --- |
| SkyLab entry point (nginx) | http://localhost:8082 |
| Frontend dev server | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| MailCatcher (dev inbox; set `SMTP_HOST` in production) | http://localhost:1080 |
| Grafana (only with `--profile monitoring`) | http://localhost:8082/grafana/ |

On first launch open the entry point: the setup wizard (`/setup`) creates the administrator, tests the Proxmox connection, and configures the IP subnet, gateway and platform entry.

## Local development

Backend:

```bash
cd backend
uv sync
source .venv/bin/activate          # Windows: .venv\Scripts\activate
fastapi dev app/main.py
```

Frontend:

```bash
cd frontend
bun install
bun run dev                        # http://localhost:5173, /api proxied to :8000
```

The backend has no generated client. Every endpoint is wrapped by hand in `frontend/src/services/*.js` and covered by a Vitest mock test; update the matching service file whenever a route changes.

See [`docs/development.md`](docs/development.md) for the full guide and [`backend/README.md`](backend/README.md) for the backend layout.

## Database migrations

Schema changes are managed with Alembic. Every change to `backend/app/models/` needs a migration:

```bash
docker compose exec backend bash
alembic revision --autogenerate -m "Describe the change"
alembic upgrade head
```

`scripts/prestart.sh` runs `alembic upgrade head` automatically when the container starts.

## Tests

- **Backend:** `bash ./scripts/test.sh` from `backend/` (pytest; coverage report in `backend/htmlcov/`), or `docker compose exec backend bash scripts/tests-start.sh -x` inside the running stack
- **Frontend:** `bun run test` from `frontend/` (Vitest, services layer)
- **CI:** GitHub Actions runs backend tests, frontend tests, desktop client tests, AI service tests, a migration check and CodeQL

## Proxmox configuration

Proxmox connections are **not** configured in `.env`. Enter them in the setup wizard on first install, then add, edit or sync them on the "PVE Connections" page; credentials are encrypted and stored in the database. Several PVE entry points (standalone nodes or clusters) can be attached at once, and hosts inside a cluster fail over automatically (TCP ping).

The legacy `PROXMOX_HOST`, `PROXMOX_USER`, `PROXMOX_PASSWORD` and `PROXMOX_VERIFY_SSL` variables are no longer read by the backend.

## Documentation

The index is in [`docs/README.md`](docs/README.md). Most-used entries:

- [`docs/development.md`](docs/development.md) — development environment
- [`docs/deployment.md`](docs/deployment.md) — production deployment, platform entry, HTTPS certificate, LDAP
- [`docs/monitoring.md`](docs/monitoring.md) — built-in health monitoring and the optional monitoring stack
- [`docs/ai-api-user-manual.md`](docs/ai-api-user-manual.md) — LiteLLM deployment and the user-facing AI API
- [`docs/multi-machine-environment-sop.md`](docs/multi-machine-environment-sop.md) — SOP for multi-machine teaching environments
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — contribution guide
- [`SECURITY.md`](SECURITY.md) — security policy

## License

SkyLab is released under the **GNU Affero General Public License v3.0** (see [`LICENSE`](LICENSE)).

- Running SkyLab inside your school or organisation, with or without local changes, carries no obligation beyond the license itself.
- If you modify SkyLab and let third parties use it over a network (for example as a hosted or managed service), the AGPL requires you to make your modified source available to those users.
- **Commercial licensing** is available for organisations that want to offer SkyLab as a service without the AGPL's source-sharing terms, or need other terms. Open an issue or contact the maintainers to discuss.

The project started from the MIT-licensed Full Stack FastAPI Template; its notice is kept in [`NOTICE`](NOTICE), and the open-source components SkyLab is built on are listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). The same information is shown in the application under Account Settings → About. "SkyLab" and the SkyLab logo are names of this project; the license covers the code, not the name.
