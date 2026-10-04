# SkyLab System Monitoring

> **English** | [繁體中文](./monitoring.zh-TW.md)

SkyLab monitoring has two layers:

| Layer | What it covers | Where to look | Extra containers? |
|---|---|---|---|
| **Built-in** | Platform health (DB, Redis, worker, PVE API connectivity, Gateway, AI Gateway and each model, scheduler task heartbeats), system alerts + email | "System health" card and active alerts on the admin "Resource Monitoring" page | No |
| **Monitoring stack** (optional) | API traffic / latency / error rate, scheduler and queue metrics, container and host resources, PostgreSQL / Redis, centralized logs, Proxmox node / VM usage, Gateway host and nginx, AI requests and the LiteLLM / vLLM inference engines | Grafana, Prometheus | `docker compose --profile monitoring` |

Proxmox node / VM resource usage **does not pass through the SkyLab backend**: the PVE built-in Metric Server pushes it straight to the InfluxDB in the monitoring stack. The backend only checks whether it can reach the PVE API itself.

---

## 1. Built-in (nothing extra to install)

### Health-check endpoints

| Endpoint | Purpose | Access |
|---|---|---|
| `GET /api/v1/utils/health-check/` | Liveness: returns `true` as long as the process is alive (used by the compose healthcheck) | No login |
| `GET /api/v1/utils/health-check/ready` | Readiness: 200 only when both DB and Redis are reachable, otherwise **503**. Returns `{"status":"ok","checks":{"database":true,"redis":true}}` with no error details | No login |
| `GET /api/v1/monitoring/system-health` | Full report: status and latency of each component, background loops and the heartbeat of every scheduler task | Admin |
| `GET /metrics` | Prometheus-format metrics (internal network only, `backend:8000`; nginx does not forward it) | Internal; `METRICS_TOKEN` optional |
| `GET /metrics/gateway-targets?exporter=node\|nginx` | Prometheus `http_sd`: returns the addresses of the Gateway exporters (taken from the connection settings on the "Gateway VM" page; returns `[]` when not configured) | Same as `/metrics` |

### Scheduler task heartbeats

The main scheduler (`scheduler`, one round every 60 seconds, 17 tasks), the Web Push notifier (`web_push`) and the WireGuard reconciler (`wireguard`) record on every run: last run, last success, duration, consecutive failure count and the most recent error. The data lives in Redis (`skylab:hb:*`, 7-day expiry) and falls back to process memory when Redis is unavailable.

Status rules (`services/monitoring/health_policy.py`):

- **Failing**: ≥ 3 consecutive failures
- **Occasional failure**: 1–2 failures (the card turns red but the overall status is not lowered)
- **Stale**: no run for more than `max(5 × interval, 10 minutes)`
- **Not yet run**: has not had its turn since this startup

Note: some tasks swallow exceptions internally and return 0 (for example `process_pending_deletions`); when such a task fails its heartbeat still shows success, so check the backend logs.

### System alerts

The scheduler task `process_system_health_alerts` evaluates once a minute (per "Governance settings → alert check interval", minimum 60 seconds). When it finds any of the following it writes an alert with `scope=system`, which appears under "Resource Monitoring → Active alerts" and is emailed to all administrators according to the "Alert email" switch:

- A scheduler task has failed ≥ 3 times in a row, or is stale
- A background loop is stale (no process holds the leader lock)
- The worker has no heartbeat, Redis is unreachable, or one of the PVE connections is unreachable
- Gateway: SSH unreachable, nginx or WireGuard not running, `nginx -t` failing (status "Unreachable"); HTTPS certificate expiring within 14 days or already expired (status "Needs attention" — the certificate is supplied and renewed by the administrator, see `deployment.md` under "Gateway HTTPS certificate")

- AI: LiteLLM unreachable or its database disconnected (`component:ai_gateway`; both the user API and the built-in AI features fail); the upstream inference service of some model (for example vLLM on a DGX) failing its health check (`component:ai_model:<alias>`; "Unreachable" when every deployment is down, "Needs attention" when only some are)

The Gateway check is one command the backend runs on the Gateway over SSH (`systemctl is-active`, `nginx -t`, reading the expiry date of the certificate referenced by `http.conf`; the self-signed fallback certificate does not count), with the result cached for 60 seconds. When no Gateway is configured the card shows "Disabled".

The AI check asks LiteLLM using the restricted runtime key (`LITELLM_RUNTIME_API_KEY`; no master key required): `/health/liveliness`, `/health/readiness` (whether the DB is connected), `/model/info` (public model names) and `/health` (the result of LiteLLM's own background health check that runs every 60 seconds; it reads the cache and never hits an inference service just to probe), with the result cached for 60 seconds. When no runtime key is set the card shows "Disabled"; models whose background check has not completed yet right after LiteLLM starts show "Not yet run", which neither affects the overall status nor raises an alert.

The same problem has to show up in **two consecutive rounds** before an alert is opened (this absorbs a worker starting late during deployment or a brief PVE blip); it resolves automatically once the problem disappears, and the cooldown reuses the resource-alert setting.

**When the database is down, or the whole backend is down, the alert itself can neither be written nor emailed**; both situations are visible on the "System health" card and on the Grafana "SkyLab Platform" dashboard, but no notification is sent. If you need one, point any external probing service on another machine at `/api/v1/utils/health-check/ready` (anything other than 200 means unhealthy).

### Request ID

nginx generates a `$request_id` for every request, forwards it to the backend as `X-Request-ID` and writes it into the nginx access log (`rid=...`); the backend puts it in the `request_id` field of its JSON logs and in the Sentry tag, and returns it in the `X-Request-ID` response header. When a user reports an error, the id they see in the browser DevTools can be looked up directly in the Request ID field of the Grafana "SkyLab Logs" dashboard to follow the whole path.

### Sentry

- Backend and worker: set `SENTRY_DSN` in `.env` (optionally `SENTRY_RELEASE` and `SENTRY_TRACES_SAMPLE_RATE`).
- Frontend (browser): set `VITE_SENTRY_DSN` in `.env`, then **rebuild the frontend image** (`docker compose build frontend`). When it is unset the SDK is stripped at build time and adds nothing to the bundle. We recommend a separate Browser project in Sentry so it can be viewed apart from the backend.
- Neither side sends personal data (`send_default_pii=False`). Tests (pytest) always disable Sentry, so exceptions raised by tests never reach the production project.

### Container log rotation

Every service in `docker-compose.yml` uses `x-logging`: json-file, 10 MB per file, 5 files kept (adjustable with `DOCKER_LOG_MAX_SIZE` / `DOCKER_LOG_MAX_FILE`). The application's own `logs/app.log` (daily rotation, 30 days) and `logs/error.log` are unchanged.

### Container healthchecks

db, pgbouncer, redis and backend already had one; newly added:

- **worker**: checks the `skylab:tasks:health-check` key that arq writes into Redis every 60 seconds (TTL 61 seconds)
- **nginx**: `/nginx-health`
- **frontend**: home page returns 200

`docker compose ps` shows `(healthy)` / `(unhealthy)`. Note that Docker Compose itself **does not** restart unhealthy containers automatically (the `restart` policy only kicks in when the process exits).

---

## 2. Monitoring stack

### Starting it

```bash
# First set (at least) these in .env:
#   GRAFANA_ADMIN_PASSWORD, INFLUXDB_ADMIN_PASSWORD, INFLUXDB_ADMIN_TOKEN
docker compose --profile monitoring up -d
```

From then on every `docker compose up -d` must include `--profile monitoring` (or set `COMPOSE_PROFILES=monitoring` in `.env`), otherwise the monitoring containers do not come up with the rest.

**When deploying through CI (the `Deploy to PVE Test` workflow)**: the workflow only runs `docker compose up -d` and copies in the deployment host's `/opt/skylab/.env`, so add `COMPOSE_PROFILES=monitoring` (together with the passwords / token above) to that `.env` and run the deployment again before the monitoring stack starts.

### Rootless Docker

Check first with `docker info --format '{{.SecurityOptions}}'`: if the output contains `name=rootless` it is rootless (the self-hosted runner's deployment host is). Under rootless Docker the socket and data directory live under the user's own paths, so add three lines to `.env`, otherwise Alloy receives no container logs at all and cAdvisor sees no containers:

```bash
# Find <uid> with `id -u`; find Docker Root Dir with `docker info --format '{{.DockerRootDir}}'`
DOCKER_SOCKET=/run/user/<uid>/docker.sock
CONTAINERD_SOCKET=/run/user/<uid>/docker/containerd/containerd.sock
DOCKER_DATA_ROOT=/home/<user>/.local/share/docker
```

For cAdvisor to see per-container CPU / memory it also needs cgroup v2 delegation: `docker info --format '{{.CgroupDriver}} {{.CgroupVersion}}'` should print `systemd 2`. If it prints `none` / `cgroupfs`, everything else keeps working and only the container panels of "SkyLab Infrastructure" stay empty; to enable it, see "Limiting resources" in Docker's official rootless documentation (set `Delegate=cpu cpuset io memory pids` in `/etc/systemd/system/user@.service.d/delegate.conf`, then log in again).

node-exporter still reads the host's CPU, memory and disks under rootless (rootlesskit does not create a pid namespace by default); the network traffic it sees is the container's own network. If the deployment host is a VM on PVE, the host NIC traffic can be read from that VM's network panel on the Proxmox dashboard (reported by the PVE Metric Server).

| Service | Purpose | Entry point |
|---|---|---|
| Grafana | Dashboards | `http://<SkyLab>/grafana/` (through nginx); locally also `http://127.0.0.1:3000/grafana/` |
| Prometheus | Metric collection (no alert rules) | `http://127.0.0.1:9090` (bound to localhost only; use an SSH tunnel) |
| Loki + Alloy | Logs of all containers, kept 14 days | Query in Grafana |
| InfluxDB 2 | Destination for the Proxmox Metric Server push | `:8086` (see setup below) |
| postgres-exporter / redis-exporter / cAdvisor / node-exporter | Database, cache, container and host metrics | Scraped internally by Prometheus |

The "View details in Grafana" button in the top-right corner of the SkyLab "Resource Monitoring" page only appears when the monitoring stack is enabled: the backend (`POST /api/v1/monitoring/grafana/session`) probes `/api/health` on `GRAFANA_INTERNAL_URL` (default `http://grafana:3000/grafana`) and shows the button only when reachable, caching the result for one minute; the button links to `GRAFANA_ROOT_URL` from `.env`, or to `/grafana/` on the same domain when unset.

The monitoring stack is only responsible for **collecting and displaying**; it sends no alert notifications (no Prometheus alert rules, Alertmanager or Grafana alerting). Problems with the platform itself are handled by the built-in "System alerts" (see above; they appear under "Active alerts" and are emailed according to the "Alert email" switch).

### Grafana dashboards (imported automatically, folder "SkyLab")

- **SkyLab Platform** (home): backend status, request volume, 5xx ratio, p95 latency, WebSocket connections, queue backlog, slowest / most error-prone routes, scheduler task status table, task failures and duration, background task records, dependency status and latency
- **SkyLab Infrastructure**: per-container CPU / memory / network, PostgreSQL (connections, transactions, cache hit ratio, deadlocks, size), Redis, SkyLab host CPU / memory / disk (only `job="node"`, excluding the Gateway)
- **SkyLab Logs**: filter by service and keyword, error logs, tracing by Request ID
- **Proxmox VE (Metric Server)**: node CPU / memory / IO wait / load, the VMs / LXCs with the highest CPU and memory, usage of each storage; the bottom row "Gateway VM (reported by PVE)" shows the CPU, memory, network and disk IO of the Gateway VM itself (choose it in the "Gateway VM" dropdown at the top; names containing gateway are sorted first automatically)
- **SkyLab Gateway**: data from the exporters on the Gateway host — SkyLab health probe / exporter / nginx status, nginx active connections, uptime, CPU / memory / disk, traffic per NIC (`wg0` is WireGuard), TCP connection count, nginx connection states and request rate (see "Gateway monitoring" below)
- **SkyLab AI**: AI Gateway / LiteLLM / inference engine status, number of available models, requests per minute and platform error rate; on the Campus side (user API keys and built-in AI features) request volume per model, outcome categories, end-to-end and time-to-first-token latency, token throughput, source; for vLLM, running / queued requests, KV cache usage, prefill / decode throughput, TTFT, inter-token latency, end-to-end latency, finish reasons, preemption count; for LiteLLM, status codes, upstream deployment successes / failures, upstream and gateway-side latency, deployment status (see "AI module monitoring" below). The "View details in Grafana" button in the top-right corner of the "AI Usage Monitoring" page opens this dashboard directly (shown only to admins and only when the monitoring stack is enabled)

When the public URL is not `http://localhost`, set `GRAFANA_ROOT_URL=https://your-domain/grafana/`.

### Logging in to Grafana

**Password-free login for SkyLab administrators**: after opening the "Resource Monitoring" page once, clicking "View details in Grafana" (or opening `/grafana/` directly) logs you in with your own SkyLab account; the role inside Grafana is Admin and the account is created automatically on first entry (login name = SkyLab email).

1. The Resource Monitoring page calls `POST /monitoring/grafana/session`, and the backend issues the `skylab_grafana` cookie: httponly, sent only under the `/grafana/` path, lifetime `GRAFANA_SESSION_EXPIRE_MINUTES` (default 480 minutes), renewed every 30 minutes while the page stays open; `Secure` is added under HTTPS.
2. For every request to `/grafana/`, nginx first does an `auth_request` to the backend's `/monitoring/grafana/auth` (reserved for nginx's internal calls; the public entry point returns 404). The backend validates the cookie and re-checks the account: being disabled, a password change / forced logout (`token_version`), loss of admin rights, or being required to enrol in two-factor authentication without having done so all invalidate it within 10 seconds. On success it returns `X-WEBAUTH-USER` / `EMAIL` / `NAME` / `ROLE` (quoted-printable, so Chinese names fit into HTTP headers).
3. nginx always overwrites these four headers with the backend's reply (whatever the browser sends is discarded), and Grafana `auth.proxy` trusts only nginx's fixed IP on the `grafana-authproxy` network (`GF_AUTH_PROXY_WHITELIST`). Grafana issues no session of its own; every request is re-validated.
4. Logging out of SkyLab deletes this cookie as well.

**Fallback entry**: with no cookie (for example the Resource Monitoring page was never opened, or the cookie expired) or when the backend is down, `/grafana/` shows Grafana's own login page; log in with `admin` / `GRAFANA_ADMIN_PASSWORD`. Note that Grafana writes this password only on its very first start; changing `.env` afterwards has no effect, and you have to reset it with `docker exec $(docker ps -qf name=grafana) grafana cli admin reset-admin-password '<new password>'`.

**Network settings**: `grafana-authproxy` is a docker network containing only nginx and Grafana, default `172.30.253.0/28`, with nginx pinned to `172.30.253.2` (outside the dynamic allocation range `172.30.253.8/29`). If it collides with the host or another network, change `GRAFANA_AUTHPROXY_SUBNET`, `GRAFANA_AUTHPROXY_IP_RANGE` and `GRAFANA_AUTHPROXY_NGINX_IP` together in `.env`. To disable password-free login, set `GRAFANA_AUTH_PROXY_ENABLED=false`.

### Proxmox VE Metric Server setup

1. Set in `.env`:
   - `INFLUXDB_ADMIN_TOKEN`: replace with a long random string (`openssl rand -hex 32`)
   - `INFLUXDB_BIND_ADDRESS`: the SkyLab host IP reachable from the PVE nodes (or `0.0.0.0`); the default binds only 127.0.0.1
2. `docker compose --profile monitoring up -d influxdb`
3. **Create a PVE-only, write-only token** (never put the admin token on PVE):
   ```bash
   docker compose exec influxdb influx bucket list --org skylab   # note the bucket ID of proxmox
   docker compose exec influxdb influx auth create --org skylab \
     --write-bucket <bucket-id> --description "proxmox metric server"
   ```
4. In the PVE web UI: **Datacenter → Metric Server → Add → InfluxDB**
   - Name: `skylab`
   - Server: SkyLab host IP, Port: `8086`
   - Protocol: `HTTP` (the InfluxDB 2 HTTP API)
   - Organization: `skylab`, Bucket: `proxmox`
   - Token: the write token created in step 3

   Or run on any node:
   ```bash
   pvesh create /cluster/metrics/server/skylab --type influxdb \
     --server <SkyLab IP> --port 8086 --influxdbproto http \
     --organization skylab --bucket proxmox --token <write token>
   ```
5. The setting is shared by the whole cluster; with several PVE connections (several clusters), configure it once per cluster. The Grafana Proxmox dashboard has data about 10 seconds later.

If you run several SkyLab instances or want the community dashboard, you can also import ID `15356` (Proxmox [Flux]) into Grafana with "InfluxDB (Proxmox)" as the data source.

### Gateway monitoring

`gateway/install.sh` installs two exporters (Debian packages) on the Gateway host, and Prometheus discovers the Gateway's address automatically through the backend's `/metrics/gateway-targets` (http_sd), so `prometheus.yml` needs no manual edits:

| exporter | port | Content |
|---|---|---|
| `prometheus-node-exporter` | 9100 | CPU, memory, disks, traffic per NIC (including `wg0`), TCP connection count |
| `prometheus-nginx-exporter` | 9113 | nginx `stub_status` (active connections, request count); stub_status binds only `127.0.0.1:9180` |

At install time, use `MONITORING_ALLOW_FROM` to specify the IP of the host running Prometheus (usually the one running SkyLab); UFW opens these two ports only to it:

```bash
sudo MONITORING_ALLOW_FROM=192.168.100.20 bash install.sh
```

Without it the exporters are still installed but Prometheus cannot reach them, so the `gateway-node` / `gateway-nginx` jobs stay down (the exporter status on the "SkyLab Gateway" dashboard shows DOWN). On a Gateway that is already installed, just re-run `install.sh` with this variable to add it.

`stub_status` only covers http (the public URLs); port forwarding (nginx stream) has no corresponding connection statistics, so look at the TCP connection count and NIC traffic on the "SkyLab Gateway" dashboard instead.

Notifications for Gateway service failures and certificates about to expire come from the built-in system alerts (`component:gateway`); the monitoring stack only shows the graphs.

### AI module monitoring

AI data comes from three places, all scraped automatically by Prometheus without editing `prometheus.yml`:

| job | Source | Content |
|---|---|---|
| `skylab-backend` | `skylab_ai_*` on the backend `/metrics` | The Campus side: the outcome, latency, time to first token and tokens of every call made with a user API key (`source=api_key`) or by a built-in AI feature (`source=platform`) |
| `litellm` | `litellm:4000/metrics` (Compose internal network) | LiteLLM's prometheus callback: requests and status codes, upstream deployment successes / failures, upstream latency, the gateway's own overhead, deployment status |
| `vllm` | `/metrics` of every vLLM instance | The engine itself: running / queued requests, KV cache, prefill / decode throughput, TTFT, inter-token latency, preemptions |

- **vLLM targets** are written by `bash scripts/prepare-ai-stack.sh` into `monitoring/prometheus/targets/vllm.json` (ignored by Git) based on `vllm-service/models.json`; each upstream host is scraped only once. After changing a model or IP, redeploy and Prometheus switches targets within a minute. Local models reach the host through `host.docker.internal` (same as LiteLLM).
- **Firewalls on remote inference hosts (DGX etc.)** must allow the host running Prometheus (usually the SkyLab deployment host) to reach the inference port; it is the same rule LiteLLM needs. vLLM's `/metrics` requires no API key (only `/v1` is protected). When the "Inference engine" panel on the "SkyLab AI" dashboard shows DOWN, first run `curl http://<DGX_IP>:8103/metrics` on the deployment host to confirm.
- **LiteLLM `/metrics` is unauthenticated**: `config.template.yaml` sets `require_auth_for_metrics_endpoint: false`, otherwise only the master key could read it (and the master key must not go into Prometheus). LiteLLM is only on the Compose internal network and the host's `127.0.0.1:4000`, and nginx does not forward it, so it is not exposed publicly. The `litellm` job drops unbounded labels such as `requested_model`, `client_ip`, `user_agent` and key hashes; for a per-model view use the deployment-level `litellm_model_name` / `model` instead.
- **The GPU itself** (temperature, VRAM, utilization) is not covered here; when needed, run the NVIDIA DCGM exporter on the DGX separately and add a Prometheus job the same way as for the Gateway.

Notifications for unhealthy models, or for LiteLLM or its database being down, come from the built-in system alerts (`component:ai_gateway`, `component:ai_model:<alias>`); the monitoring stack only shows the graphs.

### /metrics authentication (optional)

`/metrics` is open only on the compose internal network and `127.0.0.1:8000`; nginx returns 404 for `/metrics`. If other untrusted programs run on the host, you can add a token:

1. Set `METRICS_TOKEN=<random string>` in `.env`
2. Write the same string into `monitoring/prometheus/metrics_token` (single line; do not commit it to git)
3. Uncomment the three `authorization` lines in `monitoring/prometheus/prometheus.yml` and restart prometheus

### Metric reference (backend `/metrics`)

| Metric | Description |
|---|---|
| `http_requests_total{method,path,status}`, `http_request_duration_seconds` | Labelled by route template; requests that match no route always get `path="<unmatched>"` |
| `http_requests_in_progress` | Requests currently being handled |
| `skylab_websocket_connections{kind}` | vnc / terminal / jobs / classroom / classroom_watch / course_progress |
| `skylab_scheduler_task_runs_total{loop,task,result}`, `skylab_scheduler_task_duration_seconds` | Scheduler task run count and duration |
| `skylab_scheduler_task_last_success_timestamp_seconds`, `skylab_scheduler_task_consecutive_failures` | Heartbeats |
| `skylab_scheduler_loop_last_tick_timestamp_seconds`, `skylab_scheduler_loop_is_leader` | Whether the loop is running, and whether this process is the leader |
| `skylab_dependency_up{component}`, `skylab_dependency_latency_seconds` | database / redis / worker / pve:&lt;id&gt; / gateway / ai_gateway / ai_model:&lt;alias&gt; |
| `skylab_queue_jobs{queue}` | Number of tasks waiting in the arq queue |
| `skylab_task_records{status}` | queued / running (current), failed_24h / succeeded_24h |
| `skylab_ai_requests_total{source,model,request_type,outcome}` | AI call count; `outcome` = success / client_error / rate_limited / unavailable / upstream_error / stream_error / cancelled / error |
| `skylab_ai_request_duration_seconds{source,model,stream}`, `skylab_ai_time_to_first_token_seconds{source,model}` | End-to-end latency, streaming time to first token |
| `skylab_ai_tokens_total{source,model,direction}` | input / output tokens as reported by the model |

Dependency and queue metrics are refreshed only when Prometheus scrapes (at most once every 5 seconds); PVE connection, Gateway and AI status reuse the result of the most recent system health check, so a Prometheus scrape never hits PVE / SSH / LiteLLM.

The `model` label of `skylab_ai_*` keeps only model names that LiteLLM has actually served or that the health check discovered (up to 100); arbitrary names sent by callers are folded into `other`, so the number of time series stays bounded.

### Resources and retention

| Component | Retention | Adjust with |
|---|---|---|
| Prometheus | 15 days | `PROMETHEUS_RETENTION` |
| Loki | 14 days | `retention_period` in `monitoring/loki/loki-config.yml` |
| InfluxDB (Proxmox) | 30 days | `INFLUXDB_RETENTION` (applied only on first initialization) |

The whole monitoring stack needs roughly 1–1.5 GB of memory. cAdvisor and node-exporter read host information, so on Docker Desktop (Windows / macOS) they see the Docker VM rather than the physical machine.
