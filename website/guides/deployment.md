# Deployment Guide

How SkyLab is deployed on a server with Docker Compose. Development setup is in the [Development guide](/guides/development); the AI stack (LiteLLM, vLLM, keys) is covered in detail by the [AI API User Manual](/guides/ai-api-user-manual).

## Overview

- One `docker-compose.yml` at the repository root is the whole deployment. It `include`s the LiteLLM compose file from `vllm-service/litellm/`; the two keep separate `.env` files.
- The built-in `nginx` container is the only public entry point (host port `NGINX_HOST_PORT`, default 8082): `/api` and `/ws` go to the backend, `/grafana/` to Grafana, everything else to the frontend.
- The platform is an internal tool, so it asks search engines not to index it: nginx sends `X-Robots-Tag: noindex, nofollow` on every response and the frontend page carries the same robots meta tag. The public face of the project is the public site and this documentation.
- To expose the platform on a domain with HTTPS, route it through the gateway VM's nginx ("platform entry", below). SkyLab does not issue certificates; the administrator supplies one.
- The database is PostgreSQL 18 in the stack (behind PgBouncer), Redis holds queues and caches, and an `arq` worker runs background jobs. Migrations run automatically on start.
- Deployment to the test environment is a manually triggered GitHub Actions workflow on a self-hosted runner (below). Pushing to `main` does not deploy.

## 1. Prepare the server

- Linux host with Docker Engine and Compose ≥ 2.20. Rootless Docker works (the default port 8082 avoids privileged ports) but note the source-IP caveats below.
- Network reachability from the host to every Proxmox VE API endpoint (8006) and to each PVE node over SSH (LXC operations run `pct` on the node), to the gateway VM over SSH, and to the vLLM hosts if models run elsewhere.
- Clone the repository, for example to `/opt/skylab/app`, and copy the environment templates. Compose refuses to start without `vllm-service/litellm/.env`, even when you do not use the AI stack:

```bash
cp -n .env.example .env
cp -n vllm-service/litellm/.env.example vllm-service/litellm/.env
```

### Secrets

Generate strong values with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

`SECRET_KEY` does more than sign tokens: it also derives the key that encrypts credentials stored in the database (Proxmox and LDAP passwords, TOTP secrets, the gateway SSH key and so on). Therefore:

- It must be a fixed value of at least 32 characters, and the backend and the worker must use the same value (both read it from `.env`).
- When `ENVIRONMENT` is anything other than `local`, the backend refuses to start if `SECRET_KEY` is unset, empty or still `changethis`. A value shorter than 32 characters only logs a warning, but you should rotate it.
- Never change it by editing `.env` alone: every stored credential would become undecryptable. Rotate it with `backend/scripts/rotate_secret_key.py`, which re-encrypts the stored values with the new key in one transaction. On a server, run it in the backend container; the project `.env` is not mounted there, so `--skip-env-update` prints the new key instead of writing it:

```bash
docker compose exec backend python -m scripts.rotate_secret_key                              # preview, writes nothing
docker compose exec backend python -m scripts.rotate_secret_key --apply --skip-env-update   # rotate, print the new key
```

Put the printed value into `SECRET_KEY` in `.env` and run `docker compose up -d backend worker` so both are recreated with it; all users have to log in again. In a development checkout, `cd backend && uv run python -m scripts.rotate_secret_key --apply` updates `../.env` itself.

## 2. Environment variables

Every variable is listed in the [environment variable reference](/operations/environment), which is generated from `.env.example`. The ones that matter for a server:

| Variable | Notes |
| --- | --- |
| `SECRET_KEY`, `FIRST_SUPERUSER`, `FIRST_SUPERUSER_PASSWORD`, `POSTGRES_PASSWORD` | must be changed; the setup wizard can take over or disable the `.env` administrator |
| `ENVIRONMENT` | `production` on a server; with any value other than `local`, an unset `SECRET_KEY` or a `changethis` value in the variables above stops the backend from starting |
| `FRONTEND_HOST` | public URL users open (used in emails and by SkyLab Connect device login); update it when you enable the platform entry |
| `NGINX_HOST_PORT` | host port of the entry point, default 8082 |
| `SKYLAB_TRUSTED_PROXY` | source IP/CIDR of the gateway when the platform entry is used; default trusts nobody |
| `ENABLE_SIGNUP` | initial default for public self-registration; afterwards the admin UI controls email registration and automatic account creation on a first Google login. Outside `local`, `true` also requires both Turnstile keys and a real SMTP server (`SMTP_HOST` other than `mailcatcher`, plus `EMAILS_FROM_EMAIL`), otherwise the backend refuses to start; set `false` if you do not open registration |
| `LOGIN_RATE_LIMIT_PER_ACCOUNT`, `LOGIN_RATE_LIMIT_PER_IP` | login throttling per minute; the per-IP limit must cover a whole class behind one NAT |
| `BACKEND_CORS_ORIGINS` | leave empty when everything is served through nginx on one origin; `*` is rejected in `production` |
| `REDIS_ENABLED`, `REDIS_URL` | keep `true` in containers |
| `VLLM_BASE_URL`, `VLLM_API_KEY`, `VLLM_MODEL_NAME` | the model used by the built-in AI assistants ("System AI") |
| `AI_API_BASE_URL`, `AI_API_API_KEY`, `AI_API_PUBLIC_BASE_URL`, `LITELLM_RUNTIME_*` | the user-facing AI API through LiteLLM; `AI_API_API_KEY` is a restricted service key, never the master key |
| `SMTP_*`, `EMAILS_FROM_EMAIL` | outgoing mail (registration verification, alert emails); without `SMTP_HOST` mail goes to the MailCatcher container, which is only for local development |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID, also baked into the frontend image at build time; the Google Cloud "Audience" setting decides which accounts may log in, see [Registration settings and Google login](/guides/registration-and-google-login) |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Cloudflare Turnstile on password / LDAP login and sign-up; enabled only when both are set |
| `LDAP_CA_CERT_FILE` | CA for LDAP/AD over TLS, see below |
| `SENTRY_DSN`, `SENTRY_RELEASE`, `VITE_SENTRY_DSN` | error tracking; the frontend value is baked in at image build time |
| `DESKTOP_CLIENT_DOWNLOAD_URL`, `WIREGUARD_*` | SkyLab Connect and the WireGuard tunnel, see [WireGuard and SkyLab Connect](/guides/wireguard-desktop-architecture) |
| `LOG_LEVEL`, `LOG_JSON`, `DOCKER_LOG_MAX_*` | logging; keep JSON on when the monitoring stack parses logs |
| `COMPOSE_PROFILES=monitoring`, `GRAFANA_*`, `INFLUXDB_*`, `METRICS_TOKEN`, `DOCKER_SOCKET`… | optional monitoring stack, see [Monitoring](/guides/monitoring) |

Proxmox connections are **not** environment variables. They are entered in the setup wizard or on the "PVE Management" page and stored encrypted; the legacy `PROXMOX_*` variables are ignored.

### System AI model profiles

The built-in assistants send every request through the model's contract in `backend/config/llm-model-profiles.yaml`. The model key must match `VLLM_MODEL_NAME` and the upstream served model ID exactly; a model without a key is rejected before inference instead of falling back to a guessed model family, so add or rename a key when your deployment serves another ID.

- The contract decides thinking for every System AI call, including the adherence check: GPT-OSS sends `reasoning_effort=low`, Gemma sends `chat_template_kwargs.enable_thinking=false`, and `Qwen/Qwen3.8-27B` sends `chat_template_kwargs={"enable_thinking": true, "reasoning_effort": "low"}` because its [chat template](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/chat_template.jinja) otherwise defaults to `xhigh`. Legacy `enable_thinking` / `reasoning_effort` inputs do not override it; other template kwargs are kept.
- It also lists the JSON response formats and tool-choice modes the model supports. Sampling, token budgets and timeouts stay in `backend/config/system-ai.json`; with thinking enabled, the adherence check's 128-token budget may not be enough, so check real output after switching models.
- The file is part of the backend image: after editing it, run `docker compose up -d --build backend worker`.

## 3. Start the stack

```bash
# without the AI stack: leave out the LiteLLM container
docker compose up -d --build --scale litellm=0

# with the AI stack (needs a Python with PyYAML and python-dotenv, see the AI API User Manual):
bash scripts/prepare-ai-stack.sh --init-env   # fill in missing keys
bash scripts/prepare-ai-stack.sh --start      # check models and keys, generate config.yaml, start everything
```

LiteLLM mounts `vllm-service/litellm/config.yaml`, which only `prepare-ai-stack.sh` generates, so a plain `docker compose up` fails on the `litellm` container until the AI stack is set up. Add `--scale litellm=0` to every `docker compose up` while you run without it. (Docker Desktop does not fail here but creates an empty `config.yaml/` directory instead; delete it before running the helper.)

`prestart` waits for PostgreSQL, applies Alembic migrations and creates the first superuser; `docker compose ps` shows the services as `(healthy)` once ready. Open `http://<host>:8082` and complete the setup wizard: administrator → PVE connection → IP subnet → gateway → platform → finish (the gateway and platform steps can be skipped and configured later).

Upgrades and backups have their own pages: [Upgrading and migrations](/operations/upgrade) and [Backup and restore](/operations/backup).

## 4. Platform entry: serving SkyLab through the gateway's nginx

The nginx on the gateway host normally proxies only VM domains and port forwards. The "platform entry" lets the SkyLab platform itself use the same nginx, so it is reachable on a domain with HTTPS (Web Push requires HTTPS).

```
user ──https──▶ gateway nginx (TLS termination) ──http──▶ deployment host :8082 (built-in nginx) ──▶ backend / frontend
```

**Where to configure:** the "Platform entry" tab of the "Gateway VM" page; on a fresh install the "Gateway" and "Platform" steps of the setup wizard (`/setup`) offer the same settings.

1. Enter the platform's domain and the deployment host address and port that the gateway can reach (default 8082).
2. On save the backend first connects from the gateway to `http://<deployment host>:<port>/nginx-health`; if that fails nothing is saved. On success the settings are written to `/etc/nginx/skylab/http.conf` on the gateway (the same file as the VM domains, with the same `nginx -t`-and-roll-back protection). Reinstalling the gateway empties that file; "Sync rules to Gateway" on the "Install services" tab writes it back together with the VM rules (the tab does this by itself when it sees an installation finish).
3. HTTPS needs the certificate from the next section. Configure the certificate before enabling HTTPS, and make sure it covers the platform domain.

## 5. Gateway HTTPS certificate (supplied by the administrator)

SkyLab **does not issue certificates**. The administrator places a certificate on the gateway host and SkyLab only records the paths. The platform entry and every published VM domain **share this one certificate**, so a wildcard certificate covering the whole domain is recommended (for example `example.com` + `*.example.com`). A wildcard covers one label only: `a.b.example.com` is not covered by `*.example.com`.

**Where to configure:** the "HTTPS certificate" tab of the "Gateway VM" page; the setup wizard also asks for it when you tick HTTPS in the platform-entry step.

1. Put the full-chain certificate and the private key on the gateway (the installer creates `/etc/ssl/skylab/` with mode 750):
   ```bash
   install -m 644 fullchain.pem /etc/ssl/skylab/fullchain.pem
   install -m 600 privkey.pem   /etc/ssl/skylab/privkey.pem
   ```
   The private key must not be password protected (nginx cannot prompt for it at start).
2. Enter the two full paths on the "HTTPS certificate" tab and save. Over SSH the backend first checks on the gateway that the files are readable, well formed, that the key matches the certificate and that it has not expired; if the platform entry already uses HTTPS the certificate must also cover the platform domain. It then rewrites `/etc/nginx/skylab/http.conf` (rolled back if `nginx -t` fails) and reloads nginx.
3. The "Certificate on the gateway" card below the form lists the expiry date, the names the certificate covers, and **the HTTPS domains it does not cover** (those still work, but browsers warn).

**Renewing:** overwrite the files at the same paths, then press "Apply again" on the tab (or run `nginx -t && systemctl reload nginx` on the gateway). Platform health monitoring marks the gateway as "needs attention" when the certificate has less than 14 days left or has expired.

**Before a certificate is configured**, HTTPS domains are served with the self-signed certificate generated when the gateway was installed (`/etc/nginx/skylab/fallback.crt`): reachable, but browsers warn.

**Three things that remain manual** (the page lists them too):

| Item | What to do |
|---|---|
| DNS | If the domain is in a zone managed in Cloudflare and domain management has an API token and a default DNS target, saving the platform entry points the domain at the default DNS target once nginx is applied; changing the domain or disabling the entry deletes that record. An address record of a different type with the same name (for example an AAAA record for the old entry point) blocks the save, so deal with it in Cloudflare first. Otherwise, point the platform domain at the gateway's public IP yourself. |
| Trust the gateway | Set `SKYLAB_TRUSTED_PROXY=<source IP or CIDR the gateway connects from>` in the deployment host's `.env`, then `docker compose up -d nginx`. Without it every request appears to come from the gateway: IP-based rate limiting and audit logs are wrong, and the Grafana single-sign-on cookie is not marked `Secure`. |
| URL-related settings | Change `FRONTEND_HOST` in `.env` to the new https URL; update the authorised origins of Google login and the domain list of Turnstile. |

**Notes:**

- **Keep a direct path as a fallback.** The backend manages the gateway's nginx over SSH; if the gateway is down, the domain is unreachable and so is the page you would use to fix it. Keep a way to reach `http://<deployment host>:8082` directly from the internal network or a VPN.
- **Rootless Docker** does not preserve source IPs: nginx inside the container sees Docker's forwarding address for every connection. In that case set `SKYLAB_TRUSTED_PROXY` to that address (shown as "Client IP seen by the backend" on the platform-entry tab) and firewall the deployment host's public port so that only the gateway can connect; otherwise anyone connecting directly could forge `X-Real-IP`.
- The platform domain is reserved: even while the platform entry is disabled, VM owners cannot publish that domain on their own machines.

**Proxying through Cloudflare (orange cloud)** is a checkbox on the platform entry, off by default:

| | DNS only (default) | Proxied through Cloudflare |
|---|---|---|
| DNS record SkyLab creates | grey cloud | orange cloud |
| Gateway's public IP | visible in DNS | hidden behind Cloudflare |
| User IP seen by the platform | the connection source | taken from `CF-Connecting-IP`, trusted only from [Cloudflare's published ranges](https://www.cloudflare.com/ips/) |
| Uploads | no Cloudflare limit | limited by the Cloudflare plan (100 MB per request on Free) |
| Cloudflare SSL/TLS mode | not involved | **Full (strict)** when HTTPS is enabled on the gateway; Flexible makes the gateway's HTTP-to-HTTPS redirect loop |

When the box is ticked, the gateway's nginx adds `set_real_ip_from` for each Cloudflare range plus `real_ip_header CF-Connecting-IP` to the platform server blocks, so `SKYLAB_TRUSTED_PROXY` still points at the gateway. The ranges are built into the backend (`CLOUDFLARE_IP_RANGES` in `nginx_gateway_service.py`); if Cloudflare adds a range, update the list, or users arriving from it are recorded with Cloudflare's address. If the domain is not in a zone managed in Cloudflare, the box only adds the nginx settings and you switch the record to the orange cloud yourself. The status card flags the DNS record when its proxy status no longer matches the checkbox.

## 6. LDAP / Active Directory over TLS

LDAP connections over `ldaps://` or StartTLS always verify the server certificate and its hostname. The host in the LDAP server URI (a DNS name such as `dc01.campus.example` or an IP address such as `192.168.10.5`) must be listed in the directory server certificate's subjectAltName (a DNS entry for names, an IP Address entry for IPs); otherwise the handshake fails with a hostname / IP address mismatch. Private CAs without a keyUsage extension are accepted.

If your domain controller uses a self-signed certificate or one issued by a private campus / enterprise CA, LDAP login fails (the bind fails and the error shows as `ldap.serverUnavailable`) until the CA is configured:

1. Put the CA certificate (PEM) on the host, for example `./certs/campus-ca.pem` next to `docker-compose.yml`.
2. Mount it into **both** the `backend` and the `worker` containers, for example in a Compose override file:

   ```yaml
   services:
     backend:
       volumes:
         - ./certs:/app/certs:ro
     worker:
       volumes:
         - ./certs:/app/certs:ro
   ```

3. Set the path in `.env` and restart the stack:

   ```bash
   LDAP_CA_CERT_FILE=/app/certs/campus-ca.pem
   ```

Leaving `LDAP_CA_CERT_FILE` empty uses the system trust store, which is enough when the domain controller has a certificate from a public CA.

## 7. Gateway VM

The gateway is a separate Linux VM installed with `gateway/install.sh` (nginx stream + http, WireGuard, nftables ACL, SNAT, optional Prometheus exporters). The installer supports **Debian 13 (trixie) only** and stops before changing anything on other systems: its nftables rules use `nft destroy table`, which needs nftables 1.0.8 or later, and Debian 12 ships 1.0.6. Upgrade or rebuild a Debian 12 gateway on Debian 13 before (re)running the installer. SkyLab manages it over SSH from the "Gateway VM" page, whose "Install services" tab can also run the installer for you ("One-click install"). Details: [WireGuard and SkyLab Connect](/guides/wireguard-desktop-architecture) and the "Gateway monitoring" section of [Monitoring](/guides/monitoring).

## 8. Continuous deployment (test environment)

`.github/workflows/deploy-pve-test.yml` ("Deploy to PVE Test") is started manually from the Actions tab (`workflow_dispatch`) and only runs in the `SkyLab-tw/SkyLab` repository. It runs on the self-hosted runner labelled `skylab-main` and waits for approval of the `pve-test` environment before doing anything.

On the runner:

- Secrets and model routing never enter the repository. The workflow first fills in missing LiteLLM master/salt/DB values and the SkyLab service key in the files under `/opt/skylab` (`prepare-ai-stack.sh --init-env`; missing upstream vLLM keys fail the run here), then copies `/opt/skylab/.env`, `/opt/skylab/vllm-service/litellm/.env`, `/opt/skylab/vllm-service/models.json` (and `.env.API` if present) into the checkout.
- It deploys with `prepare-ai-stack.sh --start`, which ends with `docker compose up -d --build` (the monitoring stack comes up too when that `.env` sets `COMPOSE_PROFILES=monitoring`), waits for the backend health check and LiteLLM readiness, and then smoke-tests every public AI model. A failing model only produces a warning; the deployment stays.
- For the smoke test it deletes any old `pve-deploy-smoke` credential, issues a new one valid for one day, and cleans it up afterwards whether the test passed or not (unused keys are deleted; keys with usage are revoked and kept for accounting). A failed cleanup fails the workflow; an abandoned key still expires on its own.

To set up a new runner, install the GitHub Actions runner as a dedicated user on the deployment host, give it the `skylab-main` label, create the `pve-test` environment with required reviewers in the repository settings, and place the files above under `/opt/skylab`.

## 9. Operations checklist

- Health: `GET /api/v1/utils/health-check/ready` returns 503 when the database or Redis is down; the "System health" card on the Resource Monitoring page and the optional Grafana dashboards show the rest ([Monitoring](/guides/monitoring)).
- Logs: `docker compose logs backend worker nginx`; JSON logs carry `request_id`, which is also returned in the `X-Request-ID` response header.
- Locked-out administrator (lost TOTP device): `docker compose exec backend python app/reset_totp.py <email>` (do not use `uv run` inside the container).
- Machines are backed up inside SkyLab (per-machine backup/restore); the platform itself (database and `.env`) is covered in [Backup and restore](/operations/backup).
