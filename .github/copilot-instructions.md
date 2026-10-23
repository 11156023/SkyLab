# SkyLab — Copilot Instructions

> Working rules for GitHub Copilot and similar AI coding agents. This is the short version; the full documentation lives in [`website/`](../website/guides/index.md) and [`CONTRIBUTING.md`](../CONTRIBUTING.md). When anything here disagrees with the code, the code wins.

## Project at a glance

SkyLab is a Proxmox VE management platform for campuses: VM/LXC lifecycle, VNC/terminal consoles, request review and scheduled provisioning, teaching classes and multi-machine environments with batch provisioning, firewall/NAT/gateway networking, governance (TTL, idle detection, quotas, mining detection), platform health monitoring, AI-assisted operations and an OpenAI-compatible AI API through LiteLLM.

- **Backend:** FastAPI + SQLModel + PostgreSQL (via PgBouncer) + Redis + arq worker + Proxmox API + SSH (paramiko), managed with `uv`
- **Frontend:** React 19 (JSX, React Compiler) + Vite + react-router-dom 7 + TanStack Query / Table + SCSS Modules + react-i18next, managed with `bun`
- **Desktop client:** `desktop-client/`, SkyLab Connect (Electron + Vue 3 + Element Plus), managed with `npm`
- **Infra:** Docker Compose, nginx entry point on `:8082`, optional monitoring profile

## Essential commands

### Backend
```bash
cd backend
uv sync                                 # install (the venv lives in the repository root)
uv run fastapi dev app/main.py          # local dev server (:8000)
uv run ruff check --fix . && uv run ruff format .
uv run mypy <changed files>             # the code base still has many existing mypy errors
uv run bash ./scripts/test.sh           # pytest + coverage
# Alembic runs on the host: the backend container has no source mount
POSTGRES_SERVER=localhost POSTGRES_PORT=5433 uv run alembic revision --autogenerate -m "..."
```

### Frontend
```bash
cd frontend
bun install
bun run dev                             # http://localhost:5173 (/api and /ws proxied to :8000)
bun run build                           # vite build
bun run test                            # Vitest
```

### Desktop client
```bash
cd desktop-client
npm run lint:fix                        # ESLint + Prettier, run after every change
npm run build                           # vue-tsc --noEmit + vite build
```

### Full stack
```bash
docker compose up -d --build --scale litellm=0   # LiteLLM starts once the AI stack is prepared (scripts/prepare-ai-stack.sh)
docker compose exec backend bash                 # inside the container call python / alembic directly, never `uv run`
```

## Backend layering

```
api/routes/       ← thin REST controllers: validate + delegate
api/websocket/    ← VNC, terminal, jobs, classroom, course progress proxies
schemas/          ← Pydantic request/response schemas
models/           ← SQLModel tables + enums only
services/         ← business logic (classroom/, course*/, governance/, llm_gateway/, monitoring/, network/, notification/, proxmox/, resource/, scheduling/, security/, system/, teaching/, template/, user/, vm/)
infrastructure/   ← external systems (proxmox/, ssh/, ldap/, google/, cloudflare/, redis/, vnc/, ai/, queue/, worker/)
core/             ← config, db, security, permissions, i18n, logging, metrics, sentry, request_context
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
7. **No silent fallbacks:** on an exception either re-raise or log at ERROR; never swallow it and continue with a default value (this once turned a static IP silently into DHCP).

## Common pitfalls

- **No generated frontend client.** Every endpoint is wrapped by hand in `frontend/src/services/*.js` (built on `services/api.js`) with a Vitest mock test. A backend API change means updating the matching service file; pages never call `fetch` directly.
- **Models changed → Alembic migration**, always (the `prestart` container runs `scripts/prestart.sh`). Revision ids ≤ 32 chars. `audit_logs.action` is a plain string column, so a new `AuditAction` value needs no enum migration.
- **PgBouncer transaction pooling:** use `pg_advisory_xact_lock` / `pg_try_advisory_xact_lock`, never session-level locks, `SET`, `LISTEN/NOTIFY` or temp tables. `get_db` does not commit.
- **Proxmox settings are per connection:** use `get_proxmox_settings_for_node(node)` or `get_proxmox_settings(connection_id)`; the parameterless form raises when no connection exists. Route clients with `get_proxmox_api(connection_id)` / `get_proxmox_api_for_node(node)`, and SSH to a node with `get_node_host(node)`. Clones cannot cross connections.
- **WebSocket forwarding:** VNC/terminal proxies must use an `asyncio.Event` to synchronise disconnects, or you get "receive after disconnect".
- **SQLModel scalar select:** `session.exec(select(Model.column)).all()` returns a **scalar list**, not rows.
- **Never commit `.env`;** `.env.example` is the template. Settings load from the root `.env` via `core/config.py` (`env_file="../.env"`). Proxmox credentials are not in `.env` at all.
- **Logging format:** use `%s`, not `%d`, because vmids are often serialised as strings.
- **Machine login passwords:** user-chosen passwords are stored only as SHA-512 crypt hashes (`utils/sha512_crypt.py`); system-generated ones come only from `utils/login_password`.
- **Templates are platform templates:** the source machine is protected and full-cloned; nothing is converted to a PVE template.
- **UI text** goes through react-i18next (`frontend/src/locales/{zh-TW,en,ja}`); add every key to all three locales.
- **Documentation:** English pages are `website/<section>/<name>.md`, Traditional Chinese pages `website/zh-TW/<section>/<name>.md`; update both when you change behaviour, translating by hand.

## Frontend rules

- Server data is read through TanStack Query hooks in `frontend/src/queries/<domain>.js`; render loading and error states with `useQueryView` / `useLoadError` from `hooks/useQueryLoadState`. Writes call the service, then update or invalidate the cache.
- The React Compiler is on: do not add `useMemo` / `useCallback` by hand, and do not use expressions or arrow functions as default values of hook parameters.
- Tables use `components/DataTable` (TanStack Table v9, pinned to 9.3.1); define column definitions at module level.
- Dialogs use the shared `components/Modal`; VNC screens use `components/NoVncScreen`.

## Provisioning flow (key points)

- VM request → scheduler (`services/scheduling/coordinator.py`) → provisioning fan-out through `provision_pool` (dedup: runner task id + DB `SKIP LOCKED` + status re-check); clones run in the arq worker.
- IP allocation **must** come from `services/network/ip_management_service` (`cidr/gateway/dns`); never fall back to DHCP.

## Commit messages

- Conventional prefixes (`feat(scope):`, `fix(scope):`, `docs:` …), imperative subject, sign-off with `git commit -s`.
- **Never add AI co-author trailers or "generated with" markers.**

## Style

- **Backend:** Ruff + Mypy strict + prek pre-commit (root `.pre-commit-config.yaml`).
- **Frontend:** SCSS Modules per [`website/guides/frontend-style-guide.md`](../website/guides/frontend-style-guide.md) (`--color-*` tokens, `_mixins.scss`); no linter is configured.
- **Desktop client:** ESLint + Prettier (`npm run lint:fix`).
- **Comments:** only to explain intent, never to restate what the code literally does.
