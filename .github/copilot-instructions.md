# SkyLab — Copilot Instructions

> Working rules for GitHub Copilot CLI / Copilot Chat and similar agents. This is the short version; the full guide for AI agents lives in the repository root `CLAUDE.md` (local, not committed) and the human-facing docs in [`docs/`](../docs/README.md). When they disagree, `CLAUDE.md` wins.

## Project at a glance

SkyLab is a Proxmox VE management platform for campuses: VM/LXC lifecycle, VNC/terminal console, request workflow and batch provisioning, firewall/NAT/gateway networking, AI-assisted operations, teaching classes and multi-tenancy by group.

- **Backend:** FastAPI + SQLModel + PostgreSQL (via PgBouncer) + Redis + arq worker + Proxmox API + SSH (paramiko), managed with `uv`
- **Frontend:** React 19 (JSX) + Vite + react-router-dom 7 + SCSS Modules + react-i18next, managed with `bun`
- **Infra:** Docker Compose, nginx entry point on `:8082`, optional monitoring profile

## Essential commands

### Backend
```bash
cd backend
uv sync                                 # install
fastapi dev app/main.py                 # local dev server (:8000)
uv run ruff check --fix . && uv run ruff format .
uv run mypy .
bash ./scripts/test.sh                  # pytest + coverage
# Alembic (inside the backend container)
alembic revision --autogenerate -m "..." && alembic upgrade head
```

### Frontend
```bash
cd frontend
bun install
bun run dev                             # http://localhost:5173 (/api proxied to :8000)
bun run build                           # vite build
bun run test                            # vitest (services layer)
```

### Full stack
```bash
docker compose watch                    # everything with hot reload
docker compose exec backend bash
```

## Backend layering

```
api/routes/       ← thin REST controllers: validate + delegate
api/websocket/    ← VNC, terminal, jobs, classroom, course progress proxies
schemas/          ← Pydantic request/response schemas
models/           ← SQLModel tables + enums only
services/         ← business logic (classroom/, course*/, governance/, llm_gateway/, monitoring/, network/, notification/, proxmox/, resource/, scheduling/, security/, system/, teaching/, template/, user/, vm/)
infrastructure/   ← external systems (proxmox/, ssh/, ldap/, google/, cloudflare/, redis/, vnc/, ai/, queue/, worker/)
core/             ← config, db, security, permissions, i18n, logging, metrics, request_context
exceptions.py     ← AppError (raise AppError(status, msg))
main.py           ← FastAPI app, lifespan (Redis init + scheduler / push / WireGuard loops)
```

**Core rule:** `Routes → Services → Infrastructure`, never the reverse. Models hold DB tables, schemas hold API I/O; do not mix them.

## Maintainer rules

1. **No external-connection details in services** (Proxmox/SSH/LLM calls belong in `infrastructure/`).
2. **No business rules in routes** (routes are controllers only).
3. **No duplicated permission checks** (use the dependencies in `core/security.py` / `core/permissions.py`).
4. **No god-object services** (split by sub-domain).
5. **When adding async, start with the high-I/O hot spots**, not everything at once.
6. **Refactor by keeping the facade first**, remove compatibility layers last.
7. **No silent fallbacks:** on an exception either re-raise or log at ERROR; never swallow it and continue with a default value (this once turned a static IP silently into DHCP and cost four hours of debugging).

## Common pitfalls

- **No generated frontend client.** Every endpoint is wrapped by hand in `frontend/src/services/*.js` (built on `services/api.js`) with a Vitest mock test. A backend API change means updating the matching service file; pages never call `fetch` directly.
- **Models changed → Alembic migration**, always (startup runs `scripts/prestart.sh`). Revision ids ≤ 32 chars. `audit_logs.action` is a PostgreSQL enum: adding an `AuditAction` value needs an `ADD VALUE` migration.
- **PgBouncer transaction pooling:** use `pg_advisory_xact_lock` / `pg_try_advisory_xact_lock`, never session-level locks, `SET`, `LISTEN/NOTIFY` or temp tables.
- **Proxmox settings are per connection:** use `get_proxmox_settings_for_node(node)` or `get_proxmox_settings(connection_id)`; the parameterless form raises when no connection exists. Clones cannot cross connections.
- **WebSocket forwarding:** VNC/terminal proxies must use an `asyncio.Event` to synchronise disconnects, or you get "receive after disconnect".
- **SQLModel scalar select:** `session.exec(select(Model.column)).all()` returns a **scalar list**, not rows.
- **Never commit `.env`;** `.env.example` is the template. Settings load from the root `.env` via `core/config.py` (`env_file="../.env"`). Proxmox credentials are not in `.env` at all.
- **Logging format:** use `%s`, not `%d`, because vmids are often serialised as strings.
- **UI text** goes through react-i18next (`frontend/src/locales/{zh-TW,en,ja}`); add every key to all three locales.
- **Documentation** is English-first with `.zh-TW.md` counterparts (see `docs/README.md`); update both when you change behaviour.

## Provisioning flow (key points)

- VM request → scheduler (`services/scheduling/coordinator.py`) → provisioning fan-out through `provision_pool` (dedup: runner task id + DB `SKIP LOCKED` + status re-check); clones run in the arq worker.
- Service-template deployment goes through `services/network/script_deploy_service.py` (SSH to the Proxmox node, community-scripts); cancellation sets a cancel event → the SSH streaming loop breaks → rollback (destroy container + release IP).
- IP allocation **must** come from `services/network/ip_management_service` (`cidr/gateway/dns`); never fall back to DHCP.

## Commit messages

- Conventional prefixes (`feat(scope):`, `fix(scope):`, `docs:` …), imperative subject, sign-off with `git commit -s`.
- **Never add AI co-author trailers or "generated with" markers.**

## Style

- **Backend:** Ruff + Mypy strict + prek pre-commit.
- **Frontend:** SCSS Modules per [`docs/frontend-style-guide.md`](../docs/frontend-style-guide.md); no linter configured yet.
- **Comments:** only to explain intent, never to restate what the code literally does.
- Environment is Windows for the main maintainer; use `\` paths in shell suggestions where it matters.
