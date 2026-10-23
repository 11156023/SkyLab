# LiteLLM deployment

`vllm-service/litellm/` holds the LiteLLM gateway behind SkyLab's user-facing AI API. The root
`docker-compose.yml` includes its `docker-compose.yml`, which can also be run on its own
(standalone). Both modes use the same configuration and host port 4000; run only one at a time.

This page is a short reference for the directory. Complete setup, remote routes, database/key
operations, taking over a standalone gateway and user API examples are in the
[AI API User Manual](/guides/ai-api-user-manual).

## Files and secret boundaries

```text
vllm-service/litellm/
├── docker-compose.yml       # included by the root Compose; also the standalone entry point
├── config.template.yaml     # tracked static routing policy
├── config.yaml              # generated from ../models.json (ignored, written with mode 0600)
├── .env.example             # template for .env
└── .env                     # master, upstream, database, salt, remote keys (ignored)
```

Keep `config.yaml` here. Edit `../models.json` for model connections and the
template for shared policy; manual edits to the generated config are overwritten.
The root `.env` holds `AI_API_*` and `LITELLM_RUNTIME_*` with a restricted
LiteLLM service key. Never give the backend the LiteLLM master key or the upstream
keys; never inject the SkyLab service key into the LiteLLM container.

## Integrated deployment

From the repository root, `bash scripts/prepare-ai-stack.sh --init-env` fills in missing
secrets, and `bash scripts/prepare-ai-stack.sh --start` checks the configuration, generates
`config.yaml`, creates the LiteLLM database, registers the SkyLab service key and starts the
whole stack. Rerun `--start` after changing model routes. The full procedure is in
[Production launch](/guides/ai-api-user-manual#_4-production-launch-and-taking-over-an-existing-standalone-gateway)
of the AI API User Manual.

## Networking

The gateway joins the root `skylab` network: backend/worker call
`http://litellm:4000`, and LiteLLM connects to PostgreSQL directly as `db:5432`
(not through PgBouncer). Port 4000 is published on `127.0.0.1` only, for health
checks, key provisioning and admin tools (SSH tunnel for the UI). It is not
routed through nginx; users reach models only via SkyLab's `/api/v1/ai-proxy`.
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

## Health checks

`/health/liveliness` is the container health check. `/health/readiness` reports whether the
gateway is ready and its database connected. The authenticated `/health` returns the results of
LiteLLM's background model health checks, which send requests to the upstream models every 60
seconds (`health_check_interval` in the template).

After deployment, `scripts/verify-litellm-staging.sh` (needs `jq` and an exported
`LITELLM_MASTER_KEY`) checks liveliness and readiness, that the public model list matches
`models.json`, that every deployment is healthy, and that the backend can reach LiteLLM; see
[Operational verification](/guides/ai-api-user-manual#operational-verification).
