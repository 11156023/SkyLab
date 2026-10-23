# Troubleshooting

Failures people have actually hit while deploying and running SkyLab, with the cause and the fix. Each entry starts with what you see. For the AI stack see also the troubleshooting section of the [AI API user manual](/guides/ai-api-user-manual#_7-troubleshooting).

## Start-up and configuration

### The backend exits immediately with a message about `changethis`

`ENVIRONMENT` is not `local` and `SECRET_KEY`, `FIRST_SUPERUSER_PASSWORD` or `POSTGRES_PASSWORD` still holds the template value (an unset `SECRET_KEY` is refused as well). Set real values in `.env` and start again. Use at least 32 characters for `SECRET_KEY`; a shorter one only logs a warning.

### Stored credentials stopped working after editing `SECRET_KEY`

`SECRET_KEY` derives the key that encrypts PVE, LDAP and gateway credentials, TOTP secrets and generated machine passwords. Changing it by hand makes all of them undecryptable. Put the old value back, then rotate properly:

```bash
cd backend
uv run python -m scripts.rotate_secret_key            # preview
uv run python -m scripts.rotate_secret_key --apply    # re-encrypt and update ../.env
```

Restart the backend and the worker afterwards. Everyone has to log in again.

### The entry point does not come up on port 80

The default is 8082 on purpose: rootless Docker cannot bind ports below 1024, and on Windows port 80 is usually held by HTTP.sys. Either keep 8082 and put the platform entry in front, or set `NGINX_HOST_PORT=80` on a host that allows it.

### The scheduler logs an error every minute

On a database with no Proxmox connection every scheduler task fails and says so. It stops as soon as the setup wizard or the "PVE Management" page has a working connection.

### Nothing arrives in MailCatcher during development

An `.env` copied from a server carries `SMTP_PORT=587` and `SMTP_TLS=True`. MailCatcher listens on 1025 without TLS. Remove those lines or set `SMTP_HOST=mailcatcher`, `SMTP_PORT=1025`, `SMTP_TLS=False`.

### Running the backend on the host fails with "SSH backend is unavailable"

A globally installed `fastapi` was used instead of the project's virtual environment, so `paramiko` and other dependencies are missing. Run `uv sync` in `backend/`, then start it with `uv run fastapi dev app/main.py` (or activate the `.venv` in the repository root first).

## Proxmox

### The PVE connection test fails with a certificate error, or every machine shows "creating" or "creation failed"

Two different causes look the same:

- **Private CA without a `keyUsage` extension.** Recent Python and urllib3 versions validate certificates strictly, and a PVE CA generated without `keyUsage` is rejected, which makes the whole connection unreachable. Current SkyLab releases handle this; if you run an older build, upgrade. Paste the CA certificate into the connection's CA field rather than disabling verification.
- **The container cannot reach the PVE host at all.** See the next entry.

While the connection is down, machines are shown from the database with a placeholder state; they come back as soon as the connection recovers.

### Containers cannot reach the PVE host, although the host machine can

The PVE network overlaps with Docker's own ranges. Docker's bridge uses 172.17.0.0/16 and Compose allocates further 172.x networks, so a PVE host on 172.17.x or 172.20.x is routed into the container network instead of out of the host. Move Docker elsewhere in `/etc/docker/daemon.json`:

```json
{
  "bip": "10.254.0.1/24",
  "default-address-pools": [{ "base": "10.253.0.0/16", "size": 24 }]
}
```

Restart Docker, then `docker compose down` and `up -d` so the Compose networks are recreated.

### The Snapshots tab is missing, or the API returns 409 for snapshots

The guest's storage does not support snapshots. SkyLab asks PVE for the `snapshot` feature of each guest and hides the tab when it is unavailable; writes are refused with 409. Thick-provisioned LVM is the usual reason. Put templates and clones on LVM-thin, ZFS or a qcow2-capable storage if you need snapshots. Backups keep working either way.

### A Windows machine comes up but the chosen password does not work

cloudbase-init rejects passwords that fail Windows complexity rules (at least three of: upper case, lower case, digits, symbols; must not contain the account name). SkyLab validates user-chosen Windows passwords before provisioning; passwords set outside SkyLab are not checked. Reset the password from the machine's settings.

## Login and accounts

### A whole class gets `429 Too Many Requests` at the start of a lesson

Under rootless Docker the source address of every connection is the Docker bridge, so all users share one IP and the per-IP login limit trips. Raise `LOGIN_RATE_LIMIT_PER_IP` in `.env` to cover the whole class, or switch the rootless port driver to one that preserves the source address (`slirp4netns` port driver, see the Docker rootless documentation). The per-account limit is unaffected.

### LDAP login fails with `ldap.serverUnavailable` after an upgrade

SkyLab verifies the directory server's certificate and host name. A domain controller with a self-signed certificate or a private campus CA needs the CA configured:

1. Put the CA (PEM) on the host, for example `./certs/campus-ca.pem`.
2. Mount it into **both** the `backend` and the `worker` containers.
3. Set `LDAP_CA_CERT_FILE=/app/certs/campus-ca.pem` in `.env` and restart.

The host in the LDAP URI must appear in the certificate's subjectAltName (DNS or IP entry).

### An administrator lost their TOTP device

```bash
docker compose exec backend python app/reset_totp.py <email>
```

Do not wrap it in `uv run` inside the container: the image lacks the workspace lock file, so `uv run` tries to resolve the dependencies again and fails. Other users are reset by an administrator from the user list.

### Login works in the browser but scripts calling `/api/v1/login/access-token` are rejected

Cloudflare Turnstile is enabled (both `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY` are set). Every password or LDAP login and every registration must send an `X-Turnstile-Token` header. Use a Turnstile test key pair in test environments, or obtain a token from the widget. The site key must be registered for the exact domain users open.

### Grafana asks for a password

Grafana's own login is disabled. Log into SkyLab as an administrator and open the "Resource Monitoring" page first: it issues the cookie that nginx checks before passing your identity to Grafana. Then use "View details in Grafana" or open `/grafana/`. The built-in `admin` account cannot log in; the emergency procedure is in the "Logging in to Grafana" section of [Monitoring](/guides/monitoring).

## Gateway and network

### The gateway installer stops before changing anything

The installer supports Debian 13 (trixie) only. Its nftables rules use `nft destroy table`, which needs nftables 1.0.8; Debian 12 ships 1.0.6. Rebuild the gateway on Debian 13 and run the installer again.

### nginx on the gateway fails to reload after publishing many teaching environments

`nginx -t` reports `could not build server_names_hash`. Many long host names exceed the default 64-byte hash bucket. SkyLab writes a large enough `server_names_hash_bucket_size` into the generated `http.conf`; if you see the error on an older build, upgrade and re-sync the gateway from the "Gateway VM" page. A single server block never triggers this, so it only shows up at scale.

### Users arriving through the platform entry are logged with the gateway's address

Set `SKYLAB_TRUSTED_PROXY` in `.env` to the gateway's IP or CIDR so the inner nginx trusts `X-Forwarded-For` from it. When the domain is proxied through Cloudflare (orange cloud), tick that option on the platform entry; SkyLab then adds the Cloudflare ranges and `CF-Connecting-IP` to the gateway configuration.

### A blocked network range is still reachable from a machine

Block rules are inserted before the default gateway rules on purpose; a blocked range must not overlap the lab subnet, and the machine's owner can still add their own rules. Check the rule order in the firewall view and the owner's rules.

## Monitoring

### Grafana's container panels are empty under rootless Docker

Three things are needed: the rootless socket and data root in `.env` (`DOCKER_SOCKET`, `CONTAINERD_SOCKET`, `DOCKER_DATA_ROOT`), cAdvisor with host cgroup access, and cgroup v2 delegation for the user (`docker info --format '{{.CgroupDriver}} {{.CgroupVersion}}'` should print `systemd 2`). The "Rootless Docker" section of [Monitoring](/guides/monitoring) has the exact lines.

### Proxmox dashboards show nothing

PVE node and guest metrics do not pass through the backend. Each PVE node's built-in Metric Server must push to SkyLab's InfluxDB, which means `INFLUXDB_BIND_ADDRESS` has to be reachable from the nodes. The steps are in "Proxmox VE Metric Server setup" of [Monitoring](/guides/monitoring).

### Every page slows down or times out under heavy load

Compare the two health endpoints while it is happening:

| What you see | Likely cause |
| --- | --- |
| `/api/v1/utils/health-check/` answers at once, `/api/v1/utils/health-check/ready` is slow or returns 503 | The database connections (the PgBouncer pool) are used up |
| `/api/v1/utils/health-check/` is slow as well | The backend's event loop is blocked by a synchronous call |
| Only AI API calls are slow or return 429 / 503 | Model capacity: see the limits in the [AI API user manual](/guides/ai-api-user-manual) and the LiteLLM and vLLM dashboards |

To see what holds the connections, open `docker compose exec db psql -U postgres -d app` (the `.env.example` defaults) and run `SELECT pid, state, now() - xact_start AS xact_age, wait_event, left(query, 160) FROM pg_stat_activity WHERE datname = current_database() ORDER BY xact_start NULLS LAST;`. Many `idle in transaction` rows whose age grows with traffic mean a code path keeps its transaction open across a slow call; see [Backend](/guides/backend).

## Getting more information

- `GET /api/v1/utils/health-check/ready` tells you whether the database and Redis are reachable (503 otherwise).
- The "System health" card on the Resource Monitoring page shows every component SkyLab checks, including Proxmox, the gateway and the AI gateway.
- Every API response carries an `X-Request-ID` header. Search for that value in `docker compose logs backend worker nginx` (JSON logs include it as `request_id`).
- Scheduler tasks write heartbeats to Redis; a task that has not run for a while is reported as a system alert.
- When you report a bug, include the SkyLab commit (shown under Account settings → About), the PVE version and the request id.
