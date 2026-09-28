# LiteLLM deployment

The root Campus `docker-compose.yml` includes this service definition for normal
deployment. This file is also retained for standalone deployment. Both modes
use the same configuration and host port 4000; run only one at a time.

Complete setup, remote routes, database/key operations, project handover and
user API examples: [AI API 使用手冊](../../docs/ai-api-user-manual.md).

## Files and secret boundaries

```text
vllm-service/litellm/
├── docker-compose.yml       # included by root; standalone entry point retained
├── config.template.yaml     # tracked static routing policy
├── config.yaml              # generated from ../models.json (ignored)
├── .env.example             # deployment environment template
└── .env                     # master, upstream, database, salt, remote keys (ignored)
```

Keep `config.yaml` here. Edit `../models.json` for model connections and the
template for shared policy; manual edits to generated config will be replaced.
The root Campus `.env` holds `AI_API_*` and `LITELLM_RUNTIME_*` with a restricted
LiteLLM service key. Never give the backend the LiteLLM master key or the upstream
keys; never inject the Campus service key into the LiteLLM container.

## Normal integrated deployment

From the repository root, with local vLLM engines (if any) already running:

```bash
bash scripts/prepare-ai-stack.sh --init-env   # fill missing secrets; never overwrites
bash scripts/prepare-ai-stack.sh --start
docker compose ps litellm
docker compose logs -f litellm
```

`--init-env` generates the master key, salt, `DATABASE_URL` (role `litellm` on
`db:5432`) and the Campus service key when they are missing or still template
values; upstream vLLM keys must be supplied. `--start` checks both `.env` files
and Compose secret isolation, generates the production config, creates the
dedicated role/database on the Compose PostgreSQL, recreates the gateway, waits
for its database, registers the Campus service key (or syncs its model
allowlist) and finally starts the whole stack. `--check-only` validates the
current generated file without rewriting it. Remote keys named by
`api_key_env` are injected only into LiteLLM through this directory's `.env`.

After changing model routes, rerun `bash scripts/prepare-ai-stack.sh --start`
so the gateway reloads and the service key allowlist follows the new aliases.

## Networking

The gateway joins the root `skylab` network: backend/worker call
`http://litellm:4000`, and LiteLLM connects to PostgreSQL directly as `db:5432`
(not through PgBouncer). Port 4000 is published on `127.0.0.1` only, for health
checks, key provisioning and admin tools (SSH tunnel for the UI). It is not
routed through nginx; users reach models only via the Campus `/api/v1/ai-proxy`.
Local engines (`deployment: local`) are reached as `host.docker.internal:<port>`,
so `.env.API` must set `API_HOST=0.0.0.0` with a firewall limiting the engine ports.

## Standalone deployment

From this directory, with `.env` and generated `config.yaml` prepared:

```bash
docker compose up -d
docker compose ps
docker compose stop litellm
```

For handover, stop the old project's gateway first, then start the other
project's gateway. Neither stopping the container nor changing its Compose
project migrates or deletes the external database. Preserve the original
`DATABASE_URL` and `LITELLM_SALT_KEY`. Standalone mode has no `db` service, so its
`DATABASE_URL` must name an externally reachable PostgreSQL host.

Use `/health/liveliness` for container health. `/health/readiness` checks gateway
readiness; authenticated `/health` deliberately exercises upstream models.
The historical `scripts/verify-litellm-staging.sh` validates the configured
allowlist and the Campus backend connection after deployment.
