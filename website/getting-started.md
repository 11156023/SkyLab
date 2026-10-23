# Getting started

This page takes a fresh machine from nothing to a working SkyLab with one Proxmox VE connection. It stops where the role-specific guides begin. For a production server read [Deployment](/guides/deployment) afterwards; for hacking on the code read [Development](/guides/development).

## Prerequisites

| What | Why |
| --- | --- |
| A Linux host with Docker Engine and Compose ≥ 2.20 | The whole stack is one `docker-compose.yml`; the root file uses `include`, which older Compose versions do not understand. Rootless Docker works. |
| A Proxmox VE node or cluster reachable from that host on port 8006 | SkyLab talks to PVE over its API, and over SSH for a few operations (LXC terminal, `pct`, node commands). You need a PVE user with enough privileges to clone, configure and start guests. |
| An IP range for the machines SkyLab creates | The setup wizard asks for the subnet, gateway and DNS that cloud-init writes into every new machine. The range must be routed on the PVE bridge you intend to use. |
| Optional: a Debian 13 VM for the gateway | Only needed for WireGuard access from the desktop client, port forwarding, domain reverse proxy and the HTTPS platform entry. It can be added later from the "Gateway VM" page. |

Nothing has to be installed on the host besides Docker. `uv` and `Bun` are only needed when you run the backend or the frontend outside the containers.

## 1. Get the code and configure it

```bash
git clone https://github.com/ntubclass/SkyLab.git
cd SkyLab
cp -n .env.example .env
cp -n vllm-service/litellm/.env.example vllm-service/litellm/.env   # Compose reads it even without the AI stack
```

`.env.example` documents every variable in numbered sections. Before the first start, change at least:

| Variable | Notes |
| --- | --- |
| `SECRET_KEY` | At least 32 random characters. It signs tokens **and** encrypts the credentials stored in the database (PVE passwords, LDAP bind password, TOTP secrets, the gateway SSH key). Never change it by hand later; use `backend/scripts/rotate_secret_key.py`. |
| `FIRST_SUPERUSER`, `FIRST_SUPERUSER_PASSWORD` | The bootstrap administrator. The setup wizard can take this account over or disable it. |
| `POSTGRES_PASSWORD` | Database password used by the stack. |
| `ENVIRONMENT` | `local` for a laptop, `production` on a server. Outside `local` the backend refuses to start while any of the values above is still `changethis`. |
| `FRONTEND_HOST` | The URL users will open, used in e-mails and by the desktop client. |

Generate secrets with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Proxmox credentials are **not** environment variables. They are entered in the setup wizard and stored encrypted in the database.

## 2. Start the stack

```bash
docker compose up -d --build --scale litellm=0
```

`--scale litellm=0` leaves out the LiteLLM service included from `vllm-service/litellm/`: it cannot start before the AI setup in step 5 has generated its `config.yaml`. To work on the code with hot reload, run the backend and frontend on the host instead; see [Development](/guides/development).

The first start takes a few minutes: `prestart` waits for PostgreSQL, applies more than 200 migrations and creates the bootstrap administrator, then exits. Check progress with:

```bash
docker compose logs -f prestart backend
curl -s http://localhost:8082/api/v1/utils/health-check/ready
```

The readiness endpoint returns `503` until the database and Redis are reachable. Until a Proxmox connection exists the scheduler logs an error every minute; that is expected on an empty database.

The entry point is nginx on host port `NGINX_HOST_PORT` (default 8082). `/api` and `/ws` go to the backend, everything else to the frontend.

## 3. Run the setup wizard

Open `http://<host>:8082`. On a fresh database you are redirected to `/setup`, a wizard that needs no login and is disabled for good once completed. Its steps:

1. **Administrator.** Create the system administrator, or enter the `.env` administrator's e-mail to take that account over. Passwords must satisfy the complexity policy (8 to 128 characters with upper case, lower case, digits and symbols). The wizard refuses to promote an existing non-administrator account.
2. **Proxmox connection.** Host, port, credentials and, for a private CA, the CA certificate. The connection is tested before it is saved; node names must be unique across all connections. The pool, storages and default node used for new machines are set per connection.
3. **IP subnet.** CIDR, gateway, DNS and PVE bridge for the machines SkyLab creates. Static addresses are allocated from this range; the firewall is not synchronised with the subnet at this point.
4. **Gateway VM.** Optional. SSH details of the gateway; the wizard can run the installer for you. Skip it in development.
5. **Platform entry.** Optional. Expose SkyLab itself on a domain through the gateway's nginx, with an administrator-supplied HTTPS certificate. Skip it in development.
6. **Finish.** The wizard marks the setup as completed; any later request to it returns 403.

Everything the wizard sets can be changed afterwards from the administration pages ("PVE Management", "IP Management", "Gateway VM").

## 4. First login

Log in with the administrator account. After the password:

- **Two-step verification.** TOTP is optional per account. An administrator can require it for individual users; a lost device is reset from the user list or with `docker compose exec backend python app/reset_totp.py <email>`.
- **Service check.** On every login SkyLab checks the database, Redis, the worker, Proxmox, the gateway and the AI gateway before the dashboard opens. When a check fails, anyone can check again or continue with a warning; administrators also see which component failed and why.
- **First-time setup.** On an account's first login a short wizard sets the language, profile, appearance and, optionally, two-step verification. Language and appearance are stored in the browser.

## 5. Next steps

- **Templates.** Machines are cloned from platform templates. A teacher prepares a base VM or container, then uses "Create from VM" on the "Templates" page; cloud-init (or cloudbase-init for Windows) must be present for SkyLab to set the login user, password and IP.
- **Governance and quotas.** The "Resource Policies" page controls TTL reclamation, idle detection, resource alerts, mining detection and provisioning concurrency, and holds the quotas: a global default with per-user overrides, which the request forms respect.
- **AI.** Start the vLLM models, run `bash scripts/prepare-ai-stack.sh --init-env` to fill in the keys, then `bash scripts/prepare-ai-stack.sh --start` to generate the LiteLLM configuration and start the whole stack including LiteLLM; see the [AI API user manual](/guides/ai-api-user-manual).
- **Monitoring.** `docker compose --profile monitoring up -d` adds Prometheus, Grafana, Loki, Alloy and InfluxDB; see [Monitoring](/guides/monitoring).
- **Registration and Google login.** Self-registration, administrator approval and Google OAuth are described in [Registration and Google login](/guides/registration-and-google-login).
- **Production hardening.** Platform entry, HTTPS, LDAP over TLS and the operations checklist are in [Deployment](/guides/deployment).
