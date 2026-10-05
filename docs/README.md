# SkyLab documentation

> **English** | [繁體中文](./README.zh-TW.md)

English is the primary language of the documentation. Every guide has a Traditional Chinese counterpart with the `.zh-TW.md` suffix, linked from the top of each file. Files under `archive/` are point-in-time reports and are kept in their original language only.

## Getting started

| Document | What it covers |
| --- | --- |
| [`../README.md`](../README.md) | Project overview, components, quick start |
| [`development.md`](development.md) | Running the stack locally, services and ports, tests, linting, common pitfalls |
| [`deployment.md`](deployment.md) | Server deployment, environment variables, platform entry through the gateway, HTTPS certificate, LDAP over TLS, CI deployment |
| [`../backend/README.md`](../backend/README.md) | Backend layout, API and WebSocket overview, runtime loops, migrations |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Contribution workflow, licensing of contributions, commit rules |
| [`../SECURITY.md`](../SECURITY.md) | Reporting vulnerabilities |
| [`../LICENSE`](../LICENSE), [`../NOTICE`](../NOTICE), [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) | AGPL-3.0 license text, the upstream template's MIT notice, and the list of open-source components with their licenses |

## Operations

| Document | What it covers |
| --- | --- |
| [`monitoring.md`](monitoring.md) | Built-in health checks, heartbeats and system alerts; the optional Prometheus / Grafana / Loki / InfluxDB stack; Proxmox Metric Server, gateway and AI monitoring |
| [`ai-api-user-manual.md`](ai-api-user-manual.md) | LiteLLM deployment, model routing, keys, taking over a standalone gateway, and calling the user-facing AI API |
| [`wireguard-desktop-architecture.md`](wireguard-desktop-architecture.md) | SkyLab Connect: WireGuard tunnel from the desktop client to the gateway VM, ACLs, gateway installation |
| [`../vllm-service/README.md`](../vllm-service/README.md) | vLLM single-model service and multi-model cluster; see also [`PROJECT_OVERVIEW.md`](../vllm-service/docs/PROJECT_OVERVIEW.md), [`SHAREGPT_QUICKSTART.md`](../vllm-service/docs/SHAREGPT_QUICKSTART.md) and the [LiteLLM README](../vllm-service/litellm/README.md) |
| [`../monitoring/prometheus/targets/README.md`](../monitoring/prometheus/targets/README.md) | How the vLLM scrape targets file is generated |

## Product and design

| Document | What it covers |
| --- | --- |
| [`multi-machine-environment-sop.md`](multi-machine-environment-sop.md) | Formal SOP (v1.0, 2026-08-31) for building, publishing and using multi-machine teaching environments; roles, data model, end-to-end flow |
| [`ai-navigation-teaching-workflows.md`](ai-navigation-teaching-workflows.md) | Design notes for the AI navigation assistant in the class-creation and environment-editing wizards |
| [`frontend-style-guide.md`](frontend-style-guide.md) | Frontend styling rules: SCSS architecture, tokens, components, forms, tables, dark mode |
| [`database-design.md`](database-design.md) | Database normalization (1NF–3NF), redundancy kept on purpose and how it stays consistent, checklist for schema changes |

## Archive

Reports written for a specific date. Useful as history, not as current specification.

| Document | What it covers |
| --- | --- |
| [`archive/2026-09-09-ui-consistency-audit.md`](archive/2026-09-09-ui-consistency-audit.md) | Audit of 28 UI consistency and usability findings, with root causes and estimates |
| [`archive/2026-10-03-ai-api-concurrency-analysis.md`](archive/2026-10-03-ai-api-concurrency-analysis.md) | Root-cause analysis and fix plan for the AI API concurrency incident (DB connections held across upstream calls) |

## Conventions for writing docs

- Add new guides as English `name.md` with a `name.zh-TW.md` counterpart and the language switch line under the H1.
- Use plain descriptive file names without date prefixes; dated reports go to `archive/`.
- Link to the English file from code comments and configuration (`.env.example`, `docker-compose.yml`, Prometheus configs), so references survive translation updates.
- Keep commands, paths, variable names and tables identical in both languages.
