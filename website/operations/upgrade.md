# Upgrading and migrations

How to move a running SkyLab deployment to a newer commit. The stack is one `docker-compose.yml`; upgrading means pulling the new code, rebuilding the images and letting `prestart` apply the database migrations.

## Before you upgrade

1. **Back up first.** Take a database dump and copy `.env`; see [Backup and restore](/operations/backup). Migrations cannot be undone safely without it.
2. **Read the changelog.** The [Changelog](/changelog) lists what changed since your commit. Entries about `.env`, the gateway, LDAP or `SECRET_KEY` usually need an action on your side.
3. **Check free space.** `docker compose build` keeps old image layers until you prune them.
4. **Pick a quiet time.** The backend and worker restart; consoles and running jobs are interrupted, machines on Proxmox keep running.

## Standard upgrade

```bash
cd /opt/skylab/app                       # your checkout
git fetch origin
git log --oneline HEAD..origin/main      # what you are about to get
git pull --ff-only origin main

docker compose build backend worker frontend nginx
docker compose up -d
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
- Two heads mean the database received migrations from a branch that was never merged (this happens on shared development databases). Do not run `upgrade` blindly; merge the heads in code first.
- `prestart` failing on a migration leaves the database at the last applied revision. Fix the cause, then `docker compose up -d` again; migrations are idempotent per revision.

Migrations run with a direct connection to PostgreSQL, bypassing PgBouncer, because DDL and advisory locks do not work through transaction pooling. Nothing has to be configured for that; `prestart` is already wired that way.

## Rolling back

There is no automatic downgrade. If a release has to be reverted:

1. `docker compose down` (keep the volumes).
2. Restore the database dump taken before the upgrade (see [Backup and restore](/operations/backup#restoring-the-database)).
3. `git checkout <previous commit>`, rebuild and `docker compose up -d`.

Rolling back the code without restoring the database is only safe when the new migrations were additive, which the changelog does not guarantee.

## Breaking changes to know about

These changes required an operator action when they shipped. If you upgrade across them, do the action.

| Change | Action |
| --- | --- |
| LDAP connections verify the server certificate and host name | Mount the campus CA into `backend` and `worker` and set `LDAP_CA_CERT_FILE`; see [Deployment](/guides/deployment#_6-ldap-active-directory-over-tls). |
| `SECRET_KEY` also encrypts stored credentials | Never edit it by hand; rotate with `backend/scripts/rotate_secret_key.py`. |
| Gateway moved from HAProxy / Traefik / certbot to nginx, certificates supplied by the administrator | Re-run the installer on a Debian 13 gateway, enter the certificate paths on the "Gateway VM" page. |
| Proxmox settings moved from `.env` to per-connection records | `PROXMOX_*` variables are ignored; configure connections on the "PVE connections" page. |
| Platform entry and the gateway trust `X-Forwarded-For` only from `SKYLAB_TRUSTED_PROXY` | Set the variable to the gateway's address when SkyLab is served through it. |
| Database normalised to 3NF (October 2026) | Class machines that were never recorded in a batch task show as "not provisioned" after the upgrade; rebuild them from the class workspace. |
| Templates are platform templates; PVE-side conversion removed | Existing PVE templates keep working; new templates are created from the "Templates" page without converting the source machine. |

## Upgrading the gateway and the desktop client

- The gateway VM is updated from the "Gateway VM" page: "Run installer" re-applies `gateway/install.sh` and re-syncs the nginx rules. The installer refuses anything but Debian 13.
- SkyLab Connect updates itself from the release feed configured in `DESKTOP_CLIENT_DOWNLOAD_URL`; the backend does not need to change for a client release.

## Upgrading the AI stack

LiteLLM and the vLLM services are separate processes with their own `.env`. After pulling, run `bash scripts/prepare-ai-stack.sh --start` again; it re-reads `vllm-service/models.json`, rewrites the LiteLLM configuration and the Prometheus targets, and restarts the gateway. See the [AI API user manual](/guides/ai-api-user-manual#_5-changing-connections-restarting-and-rolling-back).
