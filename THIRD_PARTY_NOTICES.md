# Third-party notices

SkyLab is licensed under the GNU Affero General Public License v3.0 (see [`LICENSE`](LICENSE)). It is built on the open-source components below. This file lists the **direct** dependencies of each part of the project with their licenses; transitive dependencies keep their own licenses and are recorded in the lock files (`backend/uv.lock`, `frontend/bun.lock`, `desktop-client/package-lock.json`).

Generated with `pip-licenses` / `importlib.metadata` (backend) and `license-report` (frontend, desktop client) on 2026-10-04, then curated by hand. When you add or upgrade a direct dependency, update the matching table.

## Origin

| Component | License | Notes |
| --- | --- | --- |
| [Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template) | MIT | The project scaffold SkyLab started from; its notice is kept in [`NOTICE`](NOTICE). |

## Backend (`backend/`, Python)

| Package | License | Link |
| --- | --- | --- |
| FastAPI | MIT | https://github.com/fastapi/fastapi |
| Starlette | BSD-3-Clause | https://github.com/Kludex/starlette |
| Uvicorn | BSD-3-Clause | https://uvicorn.dev/ |
| Pydantic, pydantic-settings | MIT | https://github.com/pydantic/pydantic |
| SQLModel | MIT | https://github.com/fastapi/sqlmodel |
| SQLAlchemy | MIT | https://www.sqlalchemy.org |
| Alembic | MIT | https://alembic.sqlalchemy.org |
| psycopg 3 (incl. psycopg-binary) | LGPL-3.0-only | https://psycopg.org/ |
| redis-py, hiredis-py | MIT | https://github.com/redis/redis-py |
| arq | MIT | https://github.com/python-arq/arq |
| httpx | BSD-3-Clause | https://github.com/encode/httpx |
| websockets | BSD-3-Clause | https://github.com/python-websockets/websockets |
| proxmoxer | MIT | https://proxmoxer.github.io/docs/ |
| Paramiko | LGPL-2.1 | https://github.com/paramiko/paramiko |
| ldap3 | LGPL-3.0 | https://github.com/cannatag/ldap3 |
| cryptography | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| PyJWT | MIT | https://github.com/jpadilla/pyjwt |
| pwdlib (argon2-cffi, bcrypt) | MIT (MIT, Apache-2.0) | https://github.com/frankie567/pwdlib |
| pywebpush, py-vapid | MPL-2.0 | https://github.com/web-push-libs/pywebpush |
| python-multipart | Apache-2.0 | https://github.com/Kludex/python-multipart |
| email-validator | Unlicense | https://github.com/JoshData/python-email-validator |
| emails | BSD | https://github.com/lavr/python-emails |
| Jinja2 | BSD-3-Clause | https://github.com/pallets/jinja/ |
| tenacity | Apache-2.0 | https://github.com/jd/tenacity |
| PyYAML | MIT | https://pyyaml.org/ |
| pdfplumber | MIT | https://github.com/jsvine/pdfplumber |
| python-docx | MIT | https://github.com/python-openxml/python-docx |
| openpyxl | MIT | https://openpyxl.readthedocs.io |
| prometheus_client | Apache-2.0 AND BSD-2-Clause | https://github.com/prometheus/client_python |
| sentry-sdk | MIT | https://github.com/getsentry/sentry-python |

Development tooling (uv, pytest, ruff, mypy, prek, coverage) is not distributed with the application.

## Frontend (`frontend/`, JavaScript)

| Package | License | Link |
| --- | --- | --- |
| React, React DOM | MIT | https://github.com/facebook/react |
| react-router-dom | MIT | https://github.com/remix-run/react-router |
| i18next, react-i18next | MIT | https://github.com/i18next/i18next |
| react-vnc (noVNC) | MIT | https://github.com/roerohan/react-vnc |
| @xterm/xterm, addon-fit, addon-web-links | MIT | https://github.com/xtermjs/xterm.js |
| @xyflow/react | MIT | https://github.com/xyflow/xyflow |
| Recharts | MIT | https://github.com/recharts/recharts |
| Monaco Editor, @monaco-editor/react | MIT | https://github.com/microsoft/monaco-editor |
| react-markdown, remark-gfm, rehype-sanitize | MIT | https://github.com/remarkjs/react-markdown |
| sonner | MIT | https://github.com/emilkowalski/sonner |
| qrcode | MIT | https://github.com/soldair/node-qrcode |
| GSAP | GSAP Standard License (free for commercial and non-commercial use; not OSI open source) | https://gsap.com/standard-license |
| @material-design-icons/font | Apache-2.0 | https://github.com/marella/material-design-icons |
| @sentry/react | MIT | https://github.com/getsentry/sentry-javascript |

Build tooling (Vite, Sass, Vitest) is not distributed with the application.

## Desktop client (`desktop-client/`, SkyLab Connect)

| Package | License | Link |
| --- | --- | --- |
| Electron | MIT | https://github.com/electron/electron |
| Vue 3, Vue Router, Pinia, vue-i18n | MIT | https://github.com/vuejs/core |
| Element Plus | MIT | https://github.com/element-plus/element-plus |
| @vueuse/core | MIT | https://github.com/vueuse/vueuse |
| @seald-io/nedb | MIT | https://github.com/seald/nedb |
| electron-log | MIT | https://github.com/megahertz/electron-log |
| tree-kill | MIT | https://github.com/pkrumins/node-tree-kill |
| animate.css | MIT | https://github.com/animate-css/animate.css |
| WireGuard for Windows (bundled MSI) | MIT | https://git.zx2c4.com/wireguard-windows/ — see `desktop-client/vendor/wireguard/THIRD_PARTY_NOTICES.txt`, shipped in the app resources |

The desktop client ships its own copy of these notices as `THIRD_PARTY_NOTICES.txt`, reachable from its About page.

## Infrastructure and services

These run alongside SkyLab as separate containers or hosts and are not linked into the SkyLab code.

| Component | License | Link |
| --- | --- | --- |
| PostgreSQL | PostgreSQL License | https://www.postgresql.org/ |
| PgBouncer | ISC | https://www.pgbouncer.org/ |
| Redis | RSALv2 / SSPLv1 (Redis ≥ 7.4) or BSD-3-Clause (earlier) | https://redis.io/ |
| nginx | BSD-2-Clause | https://nginx.org/ |
| MailCatcher (development only) | MIT | https://mailcatcher.me/ |
| LiteLLM | MIT | https://github.com/BerriAI/litellm |
| vLLM | Apache-2.0 | https://github.com/vllm-project/vllm |
| Prometheus, Grafana (AGPL-3.0), Loki (AGPL-3.0), Alloy (Apache-2.0), InfluxDB 2 (MIT), cAdvisor, node-exporter, postgres-exporter, redis-exporter (Apache-2.0 / MIT) | see links | optional monitoring profile |
| Proxmox VE | AGPL-3.0 | https://www.proxmox.com/ (managed over its API; not distributed) |
| WireGuard (gateway) | GPL-2.0 (Linux kernel module) | https://www.wireguard.com/ |

## Notes

- LGPL (psycopg, Paramiko, ldap3) and MPL (pywebpush) components are used unmodified as Python packages; SkyLab's AGPL-3.0 license is compatible with them.
- GSAP is used under its free Standard License; it is not an open-source license, so forks that must stay 100 % OSI-licensed should replace it.
- Trademarks and logos of the listed projects belong to their respective owners.
