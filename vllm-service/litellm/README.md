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

From the repository root, with local vLLM engines already running:

```bash
bash scripts/prepare-ai-stack.sh --check-upstreams
bash scripts/prepare-ai-stack.sh --start
docker compose ps litellm
docker compose logs -f litellm
```

The prepare script checks both `.env` files and Compose secret isolation and
generates production config without showing secrets. `--check-only` validates
the current generated file without rewriting it. Remote keys named by
`api_key_env` are injected only into LiteLLM through this directory's `.env`.

After changing model routes, regenerate and recreate the gateway:

```bash
bash scripts/prepare-ai-stack.sh --check-upstreams
docker compose up -d --force-recreate litellm
```

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
`DATABASE_URL` and `LITELLM_SALT_KEY`.

Host networking reaches local engines on loopback; backend and worker use
`http://host.docker.internal:4000`. Keep port 4000 accessible only to permitted
backend, monitoring and admin sources. Do not expose local engine ports 8103/8104.

Use `/health/liveliness` for container health. `/health/readiness` checks gateway
readiness; authenticated `/health` deliberately exercises upstream models.
The historical `scripts/verify-litellm-staging.sh` validates the configured
allowlist and the Campus backend connection after deployment.
