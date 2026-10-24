# Documentation index

Every document of the SkyLab repository lives on this site, under `website/` in the repository. English pages are the primary version; each has a 繁體中文 counterpart under `website/zh-TW/` unless marked otherwise. The sidebar groups the same pages by purpose; this page lists them by file.

## Getting started

| Page | What it covers |
| --- | --- |
| [README](https://github.com/ntubclass/SkyLab#readme) (繁體中文: [Project overview](/zh-TW/overview)) | Project overview, components, quick start |
| [Getting started](/getting-started) | From `docker compose up` to the setup wizard and the first login |
| [Concepts and architecture](/concepts) | Components, roles, vocabulary, background work, where data lives |
| [User manual](/manual/) | One page per feature, grouped by role |
| [Troubleshooting](/troubleshooting) | Known failures, causes and fixes |

## Operations

| Page | What it covers |
| --- | --- |
| [Deployment](/guides/deployment) | Server deployment, environment variables, platform entry through the gateway, HTTPS certificate, LDAP over TLS, CI deployment |
| [Upgrading and migrations](/operations/upgrade) | Standard upgrade, migration state, rollback, breaking changes |
| [Backup and restore](/operations/backup) | What to back up, database dump and restore, disaster recovery |
| [Environment variables](/operations/environment) | Every variable of `.env.example`, generated at build time |
| [Public site](/operations/site) | The introduction site at skylab-tw.com: prerendering, SEO, contact details, Cloudflare Pages deployment |
| [Monitoring](/guides/monitoring) | Built-in health checks, heartbeats and system alerts; the optional Prometheus / Grafana / Loki / InfluxDB stack; Proxmox Metric Server, gateway and AI monitoring |
| [Registration and Google login](/guides/registration-and-google-login) | Self-registration and administrator-approval switches, Google login and the Google Cloud "Audience" setting |
| [AI API user manual](/guides/ai-api-user-manual) | LiteLLM deployment, model routing, keys, taking over a standalone gateway, calling the user-facing AI API |
| [WireGuard and SkyLab Connect](/guides/wireguard-desktop-architecture) | WireGuard tunnel from the desktop client to the gateway VM, ACLs, gateway installation |
| [vLLM service](/guides/vllm-service), [ShareGPT benchmark](/guides/vllm-sharegpt-quickstart), [LiteLLM gateway](/guides/litellm) | The self-hosted model services and the gateway that exposes them |

## Reference

| Page | What it covers |
| --- | --- |
| [REST API](/reference/api) | Generated from the backend's OpenAPI schema |
| [WebSocket endpoints](/reference/websocket) | Consoles and live updates |
| [Governance configuration](/reference/governance-config) | Every field of the governance settings |
| [Username policy](/reference/username-policy) | The rules for Linux login users |

## Development

| Page | What it covers |
| --- | --- |
| [Development environment](/guides/development) | Running the stack locally, services and ports, tests, linting, common pitfalls |
| [Backend architecture](/guides/backend) | Backend layout, API and WebSocket overview, runtime loops, migrations |
| [Database design](/guides/database-design) | Normalisation (1NF–3NF), redundancy kept on purpose, checklist for schema changes |
| [Frontend style guide](/guides/frontend-style-guide) | SCSS architecture, tokens, components, forms, tables, dark mode |
| [Internationalisation](/development/i18n) | Frontend, backend and desktop catalogs; translation rules |
| [Contributing](https://github.com/ntubclass/SkyLab/blob/main/CONTRIBUTING.md), [Security policy](https://github.com/ntubclass/SkyLab/blob/main/SECURITY.md), [Code of conduct](https://github.com/ntubclass/SkyLab/blob/main/CODE_OF_CONDUCT.md) | Kept at the repository root so GitHub shows them |
| [License](/project/license), [`LICENSE`](https://github.com/ntubclass/SkyLab/blob/main/LICENSE), [`NOTICE`](https://github.com/ntubclass/SkyLab/blob/main/NOTICE), [`THIRD_PARTY_NOTICES.md`](https://github.com/ntubclass/SkyLab/blob/main/THIRD_PARTY_NOTICES.md) | AGPL-3.0, the upstream template's MIT notice, the list of open-source components |

## Product and design

| Page | What it covers |
| --- | --- |
| [Multi-machine environment SOP](/guides/multi-machine-environment-sop) | Formal SOP for building, publishing and using multi-machine teaching environments |
| [AI navigation in teaching workflows](/guides/ai-navigation-teaching-workflows) | Design notes for the AI navigation assistant in the class-creation and environment-editing wizards |
| [AI Teacher Judge workflow and contracts](/zh-TW/guides/ai-teacher-judge-contract) (繁體中文 only) | Canonical workflow for proposals, AI edits, full-table review, persistence and script compilation |
| [Page guide content audit](/zh-TW/guides/user-guide-content-audit) (繁體中文 only) | Proposed rewrites of the in-app page guide texts (not applied yet) |

## Conventions for writing docs

- English page first (`website/<section>/<name>.md`), then the 繁體中文 page at the same path under `website/zh-TW/`, translated sentence by sentence; a page that exists in one language only is marked in the sidebar.
- Plain descriptive file names without date prefixes. A one-off report or audit leaves the site once it has been acted on; git history keeps the original.
- Code comments and configuration files (`.env.example`, `docker-compose.yml`, Prometheus configs) link to the English file by repository path, for example `website/guides/monitoring.md`.
- Commands, paths, variable names and tables stay identical in both languages.
- A new page needs an entry in the sidebar in `website/.vitepress/config.mts`, in both languages.
