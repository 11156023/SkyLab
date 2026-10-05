# Deployment Guide

> **English** | [繁體中文](./deployment.zh-TW.md)

How SkyLab is deployed on a server with Docker Compose. Development setup is in [`development.md`](development.md); the AI stack (LiteLLM, vLLM, keys) is covered in detail by the [AI API User Manual](ai-api-user-manual.md).

## Overview

- One `docker-compose.yml` at the repository root is the whole deployment. It `include`s the LiteLLM compose file from `vllm-service/litellm/`; the two keep separate `.env` files.
- The built-in `nginx` container is the only public entry point (host port `NGINX_HOST_PORT`, default 8082): `/api` and `/ws` go to the backend, `/grafana/` to Grafana, everything else to the frontend. There is no Traefik or cloudflared.
- To expose the platform on a domain with HTTPS, route it through the gateway VM's nginx ("platform entry", below). SkyLab does not issue certificates; the administrator supplies one.
- The database is PostgreSQL in the stack (behind PgBouncer), Redis holds queues and caches, and an `arq` worker runs background jobs. Migrations run automatically on start.
- Deployment to the test environment is a manually triggered GitHub Actions workflow on a self-hosted runner (below). Pushing to `main` does not deploy.

## 1. Prepare the server

- Linux host with Docker Engine and Compose ≥ 2.20. Rootless Docker works (the default port 8082 avoids privileged ports) but note the source-IP caveats below.
- Network reachability from the host to every Proxmox VE API endpoint (8006), to the gateway VM over SSH, and to the vLLM hosts if models run elsewhere.
- Clone the repository, for example to `/opt/skylab/app`, and copy the environment templates:

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
- Never change it by editing `.env` alone: every stored credential would become undecryptable. Rotate it with `backend/scripts/rotate_secret_key.py`, which re-encrypts the stored values with the new key in one transaction and then updates `.env`:

```bash
cd backend
python -m scripts.rotate_secret_key            # preview, writes nothing
python -m scripts.rotate_secret_key --apply    # rotate and update ../.env
```

Inside the backend container the project `.env` is not mounted, so run it there with `--skip-env-update` (the new key is printed for you to put in `.env`), or pass `--env-file <path>`. Restart the backend and the worker afterwards; all users have to log in again.

## 2. Environment variables

`.env.example` is organised in numbered sections and documents every variable. The ones that matter for a server:

| Variable | Notes |
| --- | --- |
| `SECRET_KEY`, `FIRST_SUPERUSER`, `FIRST_SUPERUSER_PASSWORD`, `POSTGRES_PASSWORD` | must be changed; the setup wizard can take over or disable the `.env` administrator |
| `ENVIRONMENT` | `production` on a server; enables the `changethis` safety checks |
| `FRONTEND_HOST` | public URL users open (used in emails and by SkyLab Connect device login); update it when you enable the platform entry |
| `NGINX_HOST_PORT` | host port of the entry point, default 8082 |
| `SKYLAB_TRUSTED_PROXY` | source IP/CIDR of the gateway when the platform entry is used; default trusts nobody |
| `ENABLE_SIGNUP` | public self-registration and automatic account creation for education-domain Google logins |
| `LOGIN_RATE_LIMIT_PER_ACCOUNT`, `LOGIN_RATE_LIMIT_PER_IP` | login throttling per minute; the per-IP limit must cover a whole class behind one NAT |
| `BACKEND_CORS_ORIGINS` | leave empty when everything is served through nginx on one origin |
| `REDIS_ENABLED`, `REDIS_URL` | keep `true` in containers |
| `VLLM_BASE_URL`, `VLLM_API_KEY`, `VLLM_MODEL_NAME` | the model used by the built-in AI assistants ("System AI") |
| `AI_API_BASE_URL`, `AI_API_API_KEY`, `AI_API_PUBLIC_BASE_URL`, `LITELLM_RUNTIME_*` | the user-facing AI API through LiteLLM; `AI_API_API_KEY` is a restricted service key, never the master key |
| `SMTP_*`, `EMAILS_FROM_EMAIL` | outgoing mail; without `SMTP_HOST` the stack points at MailCatcher |
| `GOOGLE_CLIENT_ID` | Google login (ID token audience only) |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Cloudflare Turnstile on login and sign-up; both required to enable |
| `LDAP_CA_CERT_FILE` | CA for LDAP/AD over TLS, see below |
| `SENTRY_DSN`, `SENTRY_RELEASE`, `VITE_SENTRY_DSN` | error tracking; the frontend value is baked in at image build time |
| `DESKTOP_CLIENT_DOWNLOAD_URL`, `WIREGUARD_*` | SkyLab Connect and the WireGuard tunnel, see [`wireguard-desktop-architecture.md`](wireguard-desktop-architecture.md) |
| `LOG_LEVEL`, `LOG_JSON`, `DOCKER_LOG_MAX_*` | logging; keep JSON on when the monitoring stack parses logs |
| `COMPOSE_PROFILES=monitoring`, `GRAFANA_*`, `INFLUXDB_*`, `METRICS_TOKEN`, `DOCKER_SOCKET`… | optional monitoring stack, see [`monitoring.md`](monitoring.md) |

Proxmox connections are **not** environment variables. They are entered in the setup wizard or on the "PVE Connections" page and stored encrypted; the legacy `PROXMOX_*` variables are ignored.

## 3. Start the stack

```bash
docker compose up -d --build
# or, when the AI stack is configured, let the helper verify models and keys first:
bash scripts/prepare-ai-stack.sh --start
```

`prestart` waits for PostgreSQL, applies Alembic migrations and creates the first superuser; `docker compose ps` shows the services as `(healthy)` once ready. Open `http://<host>:8082` and complete the setup wizard: administrator → Proxmox connection → IP subnet → gateway → platform entry → done (the last two steps can be skipped and configured later).

Upgrading is `git pull && docker compose up -d --build`. Migrations run on start; keep `SECRET_KEY` unchanged across upgrades. Back up the `app-db-data` volume (or `pg_dump` through the `db` container) before major upgrades.

## 4. Platform entry: serving SkyLab through the gateway's nginx

The nginx on the gateway host normally proxies only VM domains and port forwards. The "platform entry" lets the SkyLab platform itself use the same nginx, so it is reachable on a domain with HTTPS (Web Push requires HTTPS).

```
user ──https──▶ gateway nginx (TLS termination) ──http──▶ deployment host :8082 (built-in nginx) ──▶ backend / frontend
```

**Where to configure:** the "Platform entry" tab of the "Gateway VM" page; on a fresh install the "Gateway" and "Platform entry" steps of the setup wizard (`/setup`) offer the same settings.

1. Enter the platform's domain and the deployment host address and port that the gateway can reach (default 8082).
2. On save the backend first connects from the gateway to `http://<deployment host>:<port>/nginx-health`; if that fails nothing is saved. On success the settings are written to `/etc/nginx/skylab/http.conf` on the gateway (the same file as the VM domains, with the same `nginx -t`-and-roll-back protection). After reinstalling the gateway, "Resync" restores it together with the VM rules.
3. HTTPS needs the certificate from the next section. Configure the certificate before enabling HTTPS, and make sure it covers the platform domain.

## 5. Gateway HTTPS certificate (supplied by the administrator)

SkyLab **does not issue certificates** (no certbot, no ACME). The administrator places a certificate on the gateway host and SkyLab only records the paths. The platform entry and every published VM domain **share this one certificate**, so a wildcard certificate covering the whole domain is recommended (for example `example.com` + `*.example.com`). A wildcard covers one label only: `a.b.example.com` is not covered by `*.example.com`.

**Where to configure:** the "HTTPS certificate" tab of the "Gateway VM" page; the setup wizard also asks for it when you tick HTTPS in the platform-entry step.

1. Put the full-chain certificate and the private key on the gateway (the installer creates `/etc/ssl/skylab/` with mode 750):
   ```bash
   install -m 644 fullchain.pem /etc/ssl/skylab/fullchain.pem
   install -m 600 privkey.pem   /etc/ssl/skylab/privkey.pem
   ```
   The private key must not be password protected (nginx cannot prompt for it at start).
2. Enter the two full paths on the "HTTPS certificate" tab and save. Over SSH the backend first checks on the gateway that the files are readable, well formed, that the key matches the certificate and that it has not expired; if the platform entry already uses HTTPS the certificate must also cover the platform domain. It then rewrites `/etc/nginx/skylab/http.conf` (rolled back if `nginx -t` fails) and reloads nginx.
3. The "Certificate on the gateway" card below the form lists the expiry date, the names the certificate covers, and **the HTTPS domains it does not cover** (those still work, but browsers warn).

**Renewing:** overwrite the files at the same paths, then press "Reapply" on the tab (or run `nginx -t && systemctl reload nginx` on the gateway). Platform health monitoring marks the gateway as "needs attention" when the certificate has less than 14 days left or has expired.

**Before a certificate is configured**, HTTPS domains are served with the self-signed certificate generated when the gateway was installed (`/etc/nginx/skylab/fallback.crt`): reachable, but browsers warn.

> Versions before 2026-10 obtained certificates automatically with certbot and Cloudflare DNS-01. After upgrading, `/etc/letsencrypt` and certbot are left on old gateways but no longer used by SkyLab. You can keep using those certificates by entering `/etc/letsencrypt/live/<name>/fullchain.pem` and `privkey.pem` on the "HTTPS certificate" tab (renewal is then up to you). The Cloudflare API token is still used for DNS records in domain management.

**Three things that remain manual** (the page lists them too):

| Item | What to do |
|---|---|
| DNS | If the domain is in a zone managed in Cloudflare and domain management has an API token and a default DNS target, saving the platform entry points the domain at the default DNS target once nginx is applied (not proxied through Cloudflare, so the real client IP is visible and uploads and long-lived connections are not subject to Cloudflare's limits); changing the domain or disabling the entry deletes that record. An address record of a different type with the same name (for example an AAAA record for the old entry point) blocks the save, so deal with it in Cloudflare first. Otherwise, point the platform domain at the gateway's public IP yourself. |
| Trust the gateway | Set `SKYLAB_TRUSTED_PROXY=<source IP or CIDR the gateway connects from>` in the deployment host's `.env`, then `docker compose up -d nginx`. Without it every request appears to come from the gateway: IP-based rate limiting and audit logs are wrong, and the Grafana single-sign-on cookie is not marked `Secure`. |
| URL-related settings | Change `FRONTEND_HOST` in `.env` to the new https URL; update the authorised origins of Google login and the domain list of Turnstile. |

**Notes:**

- **Keep a direct path as a fallback.** The backend manages the gateway's nginx over SSH; if the gateway is down, the domain is unreachable and so is the page you would use to fix it. Keep a way to reach `http://<deployment host>:8082` directly from the internal network or a VPN.
- **Rootless Docker** does not preserve source IPs: nginx inside the container sees Docker's forwarding address for every connection. In that case set `SKYLAB_TRUSTED_PROXY` to that address (shown as "source IP seen by the backend" on the platform-entry page) and firewall the deployment host's public port so that only the gateway can connect; otherwise anyone connecting directly could forge `X-Real-IP`.
- The platform domain is reserved: even while the platform entry is disabled, VM owners cannot publish that domain on their own machines.

## 6. LDAP / Active Directory over TLS

LDAP connections over `ldaps://` or StartTLS always verify the server certificate and its hostname. The host in the LDAP server URI (a DNS name such as `dc01.campus.example` or an IP address such as `192.168.10.5`) must be listed in the directory server certificate's subjectAltName (a DNS entry for names, an IP Address entry for IPs); otherwise the handshake fails with a hostname / IP address mismatch. Private CAs without a keyUsage extension are accepted.

**Upgrade note (breaking change):** earlier versions did not verify the certificate. If your domain controller uses a self-signed certificate or one issued by a private campus / enterprise CA, LDAP login stops working after the upgrade (the bind fails and the error shows as `ldap.serverUnavailable`) until the CA is configured:

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

The gateway is a separate Linux VM installed with `gateway/install.sh` (nginx stream + http, WireGuard, nftables ACL, SNAT, optional Prometheus exporters). SkyLab manages it over SSH from the "Gateway VM" page, which can also run the installer for you. Details: [`wireguard-desktop-architecture.md`](wireguard-desktop-architecture.md) and the "Gateway monitoring" section of [`monitoring.md`](monitoring.md).

## 8. Continuous deployment (test environment)

`.github/workflows/deploy-pve-test.yml` ("Deploy to PVE Test") is started manually from the Actions tab (`workflow_dispatch`). It runs on the self-hosted runner labelled `skylab-main` and waits for approval of the `pve-test` environment before doing anything.

On the runner:

- Secrets and model routing never enter the repository. The workflow copies `/opt/skylab/.env`, `/opt/skylab/vllm-service/litellm/.env`, `/opt/skylab/vllm-service/models.json` (and `.env.API` if present) into the checkout, filling in only missing LiteLLM master/salt/DB values and the Campus service key.
- It then runs `docker compose up -d` (with `--profile monitoring` if `COMPOSE_PROFILES=monitoring` is set in that `.env`), waits for the backend health check and LiteLLM readiness, and runs an AI smoke test.
- For the smoke test it deletes any old `pve-deploy-smoke` credential, issues a new one valid for one day, and cleans it up afterwards whether the test passed or not (unused keys are deleted; keys with usage are revoked and kept for accounting). A failed cleanup fails the workflow; an abandoned key still expires on its own.

To set up a new runner, install the GitHub Actions runner as a dedicated user on the deployment host, give it the `skylab-main` label, create the `pve-test` environment with required reviewers in the repository settings, and place the files above under `/opt/skylab`.

## 9. Operations checklist

- Health: `GET /api/v1/utils/health-check/ready` returns 503 when the database or Redis is down; the "System health" card on the Resource Monitoring page and the optional Grafana dashboards show the rest ([`monitoring.md`](monitoring.md)).
- Logs: `docker compose logs backend worker nginx`; JSON logs carry `request_id`, which is also returned in the `X-Request-ID` response header.
- Locked-out administrator (lost TOTP device): `docker compose exec backend uv run python app/reset_totp.py <email>`.
- Backups of machines are handled inside SkyLab (per-resource backup/restore); back up the PostgreSQL volume separately.
