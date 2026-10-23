# Upgrading and migrations

How to move a running SkyLab deployment to a newer commit. The stack is one `docker-compose.yml`; upgrading means pulling the new code, rebuilding the images and letting `prestart` apply the database migrations.

## Before you upgrade

1. **Back up first.** Take a database dump and copy `.env`; see [Backup and restore](/operations/backup). Migrations cannot be undone safely without it.
2. **Read the changelog.** The [Changelog](/changelog) lists what changed since your commit. Entries about `.env`, the gateway, LDAP or `SECRET_KEY` usually need an action on your side.
3. **Check free space.** `docker compose build` keeps old image layers until you prune them.
4. **Pick a quiet time.** The backend and worker restart; consoles and running jobs are interrupted, machines on Proxmox keep running.
5. **Watch the `db` image.** If an upgrade changes the PostgreSQL major version of the `db` service (currently `postgres:18`), the existing data volume cannot be reused as is: dump before, then restore into the new version.

## Standard upgrade

```bash
cd /opt/skylab/app                       # your checkout
git fetch origin
git log --oneline HEAD..origin/main      # what you are about to get
git pull --ff-only origin main

docker compose up -d --build --scale litellm=0   # with the AI stack: bash scripts/prepare-ai-stack.sh --start
docker compose logs -f prestart          # waits for the DB, runs "alembic upgrade head", exits
```

`prestart` applies every migration between your old revision and `head`, creates the bootstrap administrator if it does not exist, then exits. The backend only starts after `prestart` has finished successfully. When the readiness endpoint answers `200` again the upgrade is done:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8082/api/v1/utils/health-check/ready
```

The test environment is upgraded by the "Deploy to PVE Test" GitHub Actions workflow instead; it performs the same steps on the self-hosted runner, see [Deployment](/guides/deployment#_8-continuous-deployment-test-environment).

## New environment variables

Compare the template with your file after pulling:

```bash
diff <(grep -oE '^#? ?[A-Z_]+=' .env.example | tr -d '# ' | sort) \
     <(grep -oE '^[A-Z_]+=' .env | sort)
```

Variables that only exist in `.env.example` are new. Most have defaults; the [environment variable reference](/operations/environment) shows which ones are required. The same applies to `vllm-service/litellm/.env` when you run the AI stack.

## Checking the migration state

```bash
docker compose exec backend alembic current      # revision the database is at
docker compose exec backend alembic history | head
docker compose exec backend alembic heads        # must print exactly one head
```

- `current` equals `heads` after a successful upgrade.
- `heads` reads the migration scripts in the image, not the database. More than one head means the code has unmerged migration branches and `upgrade head` refuses to run; merge the heads in code first.
- If `current` fails with "Can't locate revision", the database was migrated by code this checkout does not contain (this happens on shared development databases). Do not force it; find the code that created that revision first.
- A failed migration rolls back its transaction, so the database stays at the revision `current` reports. Fix the cause, then run the start command above again; `prestart` continues from there.

Migrations connect to PostgreSQL directly instead of through PgBouncer, because they run DDL in long transactions. Nothing has to be configured for that; `prestart` is already wired that way (`POSTGRES_SERVER=db`).

## Rolling back

There is no automatic downgrade. If a release has to be reverted:

1. `docker compose down` (keep the volumes).
2. Restore the database dump taken before the upgrade (see [Backup and restore](/operations/backup#restoring-the-database)).
3. `git checkout <previous commit>`, then rebuild and start with the same command as in the standard upgrade.

Rolling back the code without restoring the database is only safe when the new migrations were additive, which the changelog does not guarantee.

## Breaking changes to know about

These changes required an operator action when they shipped. If you upgrade across them, do the action.

| Change | Action |
| --- | --- |
| LDAP connections verify the server certificate and host name | Mount the campus CA into `backend` and `worker` and set `LDAP_CA_CERT_FILE`; see [Deployment](/guides/deployment#_6-ldap-active-directory-over-tls). |
| `SECRET_KEY` also encrypts stored credentials | Never edit it by hand; rotate with `backend/scripts/rotate_secret_key.py`. |
| Gateway moved from HAProxy / Traefik / certbot to nginx, certificates supplied by the administrator | Re-run the installer on a Debian 13 gateway (it stops the old HAProxy / Traefik services), then enter the certificate paths on the "HTTPS certificate" tab of the "Gateway VM" page. Certificates certbot already issued can be reused by entering `/etc/letsencrypt/live/<name>/fullchain.pem` and `privkey.pem`; renewing them is then up to you. |
| Proxmox settings moved from `.env` to per-connection records | `PROXMOX_*` variables are ignored; configure connections on the "PVE Management" page. |
| The built-in nginx trusts `X-Real-IP` / `X-Forwarded-Proto` only from `SKYLAB_TRUSTED_PROXY` | Set the variable to the gateway's address when SkyLab is served through it. |
| Database normalised to 3NF (October 2026) | Class machines that were never recorded in a batch task show as "not provisioned" after the upgrade; rebuild them from the class workspace. |
| Templates are platform templates; PVE-side conversion removed | Existing PVE templates keep working; new templates are created from the "Templates" page without converting the source machine. |

## Upgrading the gateway and the desktop client

- The gateway VM is updated from the "Install services" tab of the "Gateway VM" page: "Reinstall" runs the `gateway/install.sh` shipped in the backend image, so upgrade SkyLab first. Reinstalling empties the generated nginx files; "Sync rules to Gateway" writes the rules back (the tab does it by itself when it sees the installation finish). The installer refuses anything but Debian 13.
- SkyLab Connect checks GitHub (`1Ray0/SkyLab-Connect-Releases`) for a newer release when it starts and offers the new installer. `DESKTOP_CLIENT_DOWNLOAD_URL` only decides where the platform's download link points; a client release needs no backend change.

## Upgrading the AI stack

LiteLLM runs in the Compose stack with its own `vllm-service/litellm/.env`; the vLLM engines run outside Compose. After pulling, run `bash scripts/prepare-ai-stack.sh --start` instead of `docker compose up -d --build`: it re-reads `vllm-service/models.json`, rewrites the LiteLLM configuration and the Prometheus targets, recreates the LiteLLM container, syncs the service key's model allowlist and then runs `docker compose up -d --build`. The LiteLLM image defaults to `litellm/litellm:latest`, and `up` does not pull a newer `latest` by itself: run `docker compose pull litellm` to update it, or pin a tested tag with `LITELLM_IMAGE` in `vllm-service/litellm/.env`. See the [AI API user manual](/guides/ai-api-user-manual#_5-changing-connections-restarting-and-rolling-back).
