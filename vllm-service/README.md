# vLLM Service

> **English** | [繁體中文](./README.zh-TW.md)

`vllm-service/` is SkyLab's canonical vLLM inference service directory. It consolidates the
earlier `vllm-inference/` single-model deployment and the `vllm-API/` multi-model gateway (neither
of those legacy directories is in the repo; see the end of this document).
The public multi-model API is always served by LiteLLM (`litellm/`); the early hand-written FastAPI
gateway has been removed.

## Service modes

| Mode | Script | Purpose | Public endpoint |
| --- | --- | --- | --- |
| Single-model main service | `./start_single_model.sh` | Internal system AI, MVP, single-model debugging | `http://<API_HOST>:<API_PORT>/v1` |
| Multi-model vLLM cluster | `./start_multi_model_cluster.sh` | Starts only the per-model instances, for LiteLLM to use | `http://127.0.0.1:8103/8104/v1` |
| LiteLLM gateway | `bash scripts/prepare-ai-stack.sh --start` (repo root) | Public multi-model API, keys and routing | `http://127.0.0.1:4000/v1` |

## Quick start

```bash
cd vllm-service
```

Install vLLM first according to your GPU/CUDA/platform version; `requirements.txt` only lists the
service's surrounding dependencies and does not pin a GPU wheel:

```bash
pip install -r requirements.txt
pip install vllm
```

Edit the configuration file that matches your deployment mode:

- Single-model mode reads `.env.interface`; the values that matter are `MODEL_NAME`, `API_PORT`, `API_KEY` and the single-model vLLM capacity parameters.
- The multi-model cluster reads `.env.API` (deployment values shared by all models) and `models.json`.
- `models.json` manages each model's own `model_name`, `api_port` and engine/parser parameters.
- `models.json.example` ships two local entries as a starting point (`gemma-4-31b` on 8103 and
  `qwen3-14b` on 8104); replace them with the models you actually deploy. The model names in the
  rest of this document are examples, not a statement of what is running anywhere.
- Models with a Mamba SSM cache (for example the NVIDIA Nemotron Nano family) start the cache in
  `float32`; a 32K context / 16 concurrent requests is a reasonable initial baseline for them, and
  their experimental prefix cache should stay disabled.
- `.env.API` limits `MAX_JOBS` / `NINJAFLAGS` to a single job so that the second model's first
  FlashInfer CUDA JIT compile does not exhaust host memory.
- `API_KEY` is the Bearer key of each vLLM instance. LiteLLM obtains the same value as
  `VLLM_UPSTREAM_API_KEY` and forwards to the local vLLM `/v1`; the separate variable name is only a
  Docker container injection boundary, not a second set of permissions. The backend calls LiteLLM
  with a different, restricted service key (`AI_API_API_KEY` in the root `.env`).

### Upgrade note: `ALLOWED_LOCAL_MEDIA_PATH`

`ALLOWED_LOCAL_MEDIA_PATH` now defaults to empty (disabling `file://` local media reads), and setting
it to `/` makes startup fail outright (configuration validation error). The old `.env.example`
shipped with `ALLOWED_LOCAL_MEDIA_PATH=/`; any `.env.API` or `.env.interface` copied from the old
template, and any deployment whose `models.json` entry sets `allowed_local_media_path: "/"`, must
clear that value before restarting, or point it at a dedicated media directory (any user who can
call the API can read the files under that directory). This affects `main.py single` / `cluster`
and the benchmark, because they all read configuration through `Settings`.

## Starting the single-model main service

```bash
bash ./start_single_model.sh
```

This script starts the service in the background; the main output is written to `logs/main.log`
and the launcher PID to `.runtime/single-model.pid`. If that PID is still running, starting again
simply reports the existing process.

Equivalent to:

```bash
python main.py single --env-file .env.interface
```

This mode starts one vLLM OpenAI-compatible server. The main backend's internal AI features can use:

```env
VLLM_BASE_URL=http://localhost:8000/v1
VLLM_API_KEY=vllm-secret-key-change-me
VLLM_MODEL_NAME=<MODEL_NAME>
```

## Starting the multi-model vLLM cluster (the main AI API service)

```bash
bash ./start_multi_model_cluster.sh
LITELLM_SERVICE_API_KEY=<campus-service-key-from-secret-manager> \
  python ./tools/generate_litellm_config.py --mode production
```

The cluster script is equivalent to `python main.py cluster --base-env .env.API` (the `--no-gateway`
flag from the old command is still accepted but has no effect). It only manages
starting the vLLM instances, their ready checks and graceful shutdown; model aliases / routing are
managed by LiteLLM's generated configuration.
Every `models.json` entry must have a unique `alias`; for local models, `served_model_name` and `api_port` must be unique as well.
`served_model_name` is passed to vLLM's `--served-model-name`, so each instance's
`/v1/models` does not expose the host model path.

When you download a model from Hugging Face, pin a fixed revision so that upstream file updates
cannot make the deployment non-reproducible, for example:

```bash
./.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8', revision='8bc5eece2eb5514c4bca7f2ec655b91eb554f4c0', local_dir='AImodels/NVIDIA-Nemotron-Nano-9B-v2-FP8', max_workers=4)"
```

`generate_litellm_config.py` reads `models.json` and `litellm/config.template.yaml` and produces
`litellm/config.yaml`. The output is ignored by Git; ordinary models use `os.environ/...` secret references,
and if a remote model sets `apikeys`, that value is written directly into this local runtime artifact.
`integration` mode contains no database settings; `production` mode requires the deployment process
to inject `LITELLM_SERVICE_API_KEY` first, and generates a `DATABASE_URL` reference.

LiteLLM is referenced by the Campus main Compose via `include`; the original standalone Compose is retained.
From the project root, fill in the keys first (`litellm/.env` is created from the template if missing), then start:

```bash
cd ..
bash scripts/prepare-ai-stack.sh --init-env
bash scripts/prepare-ai-stack.sh --start
```

`--start` automatically creates the LiteLLM-dedicated database (`db:5432`, not through PgBouncer) and
issues / syncs the Campus service key. The Campus backend reaches the gateway through `AI_API_BASE_URL`,
`AI_API_API_KEY` and `LITELLM_RUNTIME_*` in the root `.env`; the Compose backend uses
`http://litellm:4000` on the same internal network, while a backend run as a host process uses `http://127.0.0.1:4000`.
The LiteLLM container reaches the local vLLM via `host.docker.internal`, so `API_HOST` in `.env.API`
must be `0.0.0.0` (restrict the engine ports with a firewall). `docker compose up/down` in the root
manages LiteLLM but does not manage the vLLM processes on the host.

For cross-host models, set `deployment: "remote"` and `api_base` (including `/v1`) in `models.json`,
and choose either `api_key_env` (value stored in `litellm/.env`) or a literal `apikeys`. When neither is
specified, `VLLM_UPSTREAM_API_KEY` is used; `apikeys` and `api_key_env` cannot be set at the same time. The local launcher skips these entries; the generator
merges them into the same LiteLLM routes. Full examples, taking over an existing standalone container and user API operations are in the
[AI API User Manual](../docs/ai-api-user-manual.md).

## Benchmark

```bash
./run_sharegpt_benchmark.sh --download        # download the ShareGPT_V3 dataset into test_datasets/
LITELLM_API_KEY=<service-key> ./run_sharegpt_benchmark.sh -n 100 -c 20 --model qwen3-14b
./run_sharegpt_benchmark.sh --target single -n 100 -c 20   # hit the single-model vLLM from .env.interface directly
```

The ShareGPT benchmark targets LiteLLM by default (`LITELLM_BASE_URL`, default `http://127.0.0.1:4000/v1`);
the key is read from the `LITELLM_API_KEY` and then `AI_API_API_KEY` environment variables, falling back to
`AI_API_API_KEY` in the repo root `.env`. `python -m benchmark.async_bench` instead stress-tests the
vLLM instance configured in `.env` directly with a single prompt. Full details are in [docs/SHAREGPT_QUICKSTART.md](docs/SHAREGPT_QUICKSTART.md).

## Directory responsibilities

| Path | Responsibility |
| --- | --- |
| `main.py` | CLI entry point, supports `single` / `cluster` |
| `core/engine.py` | Start/stop, health check and logging of a single vLLM instance |
| `core/cluster.py` | Multi-model instance lifecycle |
| `config/settings.py` | Shared vLLM settings |
| `config/multi_model.py` | `models.json` loading and cluster resource checks |
| `litellm/` | Git-managed LiteLLM static routing policy template |
| `tools/` | SkyLab AI integration test (`campus_ai_integration_test.py`), plus LiteLLM deployment tools (`generate_litellm_config.py` generates the LiteLLM config; `prepare_ai_stack.py` checks key boundaries and prepares / starts the AI stack) |
| `benchmark/` | async / ShareGPT benchmark (ShareGPT goes through LiteLLM by default) |

## Frontend status

`vllm-service` only provides the inference service and the LiteLLM gateway configuration; it no longer maintains a React/Vite frontend.
If you need an interactive interface, call the Campus backend from the SkyLab main frontend or an external OpenAI-compatible client.

## Legacy directory status

`vllm-API/` and `vllm-inference/` are no longer in the repo (both are ignored via `.gitignore`); they may only remain as
untracked local copies on existing deployment hosts for migration reference, and are no longer maintained. All maintenance happens in `vllm-service/`.
