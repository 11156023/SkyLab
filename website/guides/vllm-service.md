# vLLM Service

`vllm-service/` is SkyLab's vLLM inference service. It starts and supervises the vLLM processes and holds
the LiteLLM gateway configuration; it has no user interface of its own. The public multi-model API is
always served by LiteLLM (`litellm/`), and people reach the models through SkyLab or any
OpenAI-compatible client.

## Service modes

| Mode | Start | Purpose | Endpoint |
| --- | --- | --- | --- |
| Single-model main service | `./start_single_model.sh` (`python main.py single`) | Built-in system AI, single-model debugging | `http://<API_HOST>:<API_PORT>/v1` from `.env.interface` |
| Multi-model vLLM cluster | `./start_multi_model_cluster.sh` (`python main.py cluster`, the default mode) | One vLLM instance per local model in `models.json`; only LiteLLM connects to them | `http://127.0.0.1:<api_port>/v1`, for example 8103 / 8104 |
| LiteLLM gateway | `bash scripts/prepare-ai-stack.sh --start` (repo root) | Public multi-model API, keys and routing | `http://127.0.0.1:4000/v1` on the host, `http://litellm:4000` on the Compose network |

The main SkyLab backend uses two separate settings in the root `.env`:

- `VLLM_BASE_URL` points at the single-model main service and includes `/v1`; the built-in AI features use it.
- `AI_API_BASE_URL` points at the LiteLLM gateway root, without `/v1`, and uses the restricted service key
  `AI_API_API_KEY`; the public AI API goes through it.

## Quick start

Work in `vllm-service/`. Install vLLM for your GPU / CUDA / platform first; `requirements.txt` only lists
the service's own dependencies and does not pin a GPU wheel:

```bash
cd vllm-service
pip install -r requirements.txt
pip install vllm
```

Create the configuration from the tracked examples (the real files are ignored by Git):

- `.env.example` is the full settings reference. Copy it to `.env.interface` for single-model mode
  (`MODEL_NAME`, `API_PORT`, `API_KEY` and the single-model capacity parameters matter most) and to
  `.env.API` for the cluster (deployment values shared by all models).
- Copy `models.json.example` to `models.json`; it holds each model's own `model_name`, `api_port` and
  engine/parser parameters. The example ships two local entries (`gemma-4-31b` on 8103 and `qwen3-14b`
  on 8104) and two remote ones; replace them with the models you actually deploy. The model names in the
  rest of this page are examples, not a statement of what is running anywhere.

Settings that need care:

- `API_KEY` is the Bearer key of every vLLM instance. LiteLLM receives the same value as
  `VLLM_UPSTREAM_API_KEY` and forwards to the local vLLM `/v1`; the separate variable name only reflects
  which container it is injected into, not a second set of permissions. The backend calls LiteLLM with a
  different, restricted service key (`AI_API_API_KEY` in the root `.env`).
- `ALLOWED_LOCAL_MEDIA_PATH` (`allowed_local_media_path` in a `models.json` entry) is empty by default,
  which disables `file://` local media reads, and setting it to `/` makes startup fail. If you need it,
  point it at a dedicated media directory: anyone who can call the API can read the files there. An
  `.env.API` or `.env.interface` copied from an older template may still contain `/`; clear it before
  restarting.
- For models with a Mamba SSM cache (for example the NVIDIA Nemotron Nano family), set
  `mamba_ssm_cache_dtype` to `float32` and `enable_prefix_caching` to `false` in their `models.json` entry
  (prefix caching is on by default and is experimental for these models); a 32K context and 16 concurrent
  requests are a reasonable initial baseline.
- `.env.API` and `.env.interface` only feed the launcher's own settings; other variables in them are
  ignored. Build variables such as `MAX_JOBS` / `NINJAFLAGS`, used to keep the second model's first
  FlashInfer CUDA JIT compile from exhausting host memory, must be exported in the shell that runs the
  start script; the launcher passes its environment through to vLLM.

## Starting the single-model main service

```bash
bash ./start_single_model.sh   # same as: python main.py single --env-file .env.interface
```

The script starts the service in the background, writes the output to `logs/main.log` and the launcher
PID to `.runtime/single-model.pid`. If that PID is still running, starting again only reports the
existing process.

The backend's built-in AI features then use (root `.env`):

```env
VLLM_BASE_URL=http://<inference-host>:8000/v1
VLLM_API_KEY=<API_KEY from .env.interface>
VLLM_MODEL_NAME=<model ID served by the instance>
```

`VLLM_MODEL_NAME` is the ID the instance serves (`SERVED_MODEL_NAME`, or the model path when that is
empty) and must exactly match a model key in `backend/config/llm-model-profiles.yaml`. Inside the Compose
stack `localhost` is the backend container itself, so use an address of the inference host that the
container can reach.

## Starting the multi-model vLLM cluster

```bash
bash ./start_multi_model_cluster.sh   # same as: python main.py cluster --base-env .env.API
```

The launcher starts the local instances listed in `models.json` (entries with `deployment: "remote"` are
skipped), checks that they are ready and shuts them down gracefully; its output goes to
`logs/cluster.log` and each model's to `logs/<alias>.log`. Model aliases and routing belong to the
generated LiteLLM configuration. Every `models.json` entry must have a unique `alias`; local entries also
need a unique `served_model_name` and `api_port`. `served_model_name` is passed to vLLM's
`--served-model-name`, so an instance's `/v1/models` does not expose the host model path.

### Crash recovery

The launcher keeps watching the vLLM processes after start-up (both modes). When one exits unexpectedly
it logs the exit code, cleans up any worker processes left in its process group (so GPU memory and the
port are released) and restarts **only that model**; the other models keep serving. Restarts do not
wait for readiness, and the model's `logs/<alias>.log` is appended to, so the output from before the
crash is kept. Retries are limited and back off, configured in `.env.API` / `.env.interface`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ENGINE_RESTART_MAX_ATTEMPTS` | `3` | Restarts per model before giving up; `0` disables automatic restart |
| `ENGINE_RESTART_BACKOFF_SECONDS` | `10` | Wait before the first restart, doubled each time (capped at 300 s) |
| `ENGINE_RESTART_RESET_SECONDS` | `600` | A model that ran this long before crashing starts counting from zero again |

After the limit the launcher gives up on that model and logs an error (`logs/cluster.log` or
`logs/main.log`). In single-model mode, or when every cluster model has been given up, the launcher
then exits with a non-zero status. Fix the cause and start the script again.

When you download a model from Hugging Face, pin a fixed revision so that upstream file updates
cannot make the deployment non-reproducible, for example:

```bash
./.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8', revision='8bc5eece2eb5514c4bca7f2ec655b91eb554f4c0', local_dir='AImodels/NVIDIA-Nemotron-Nano-9B-v2-FP8', max_workers=4)"
```

## LiteLLM configuration and gateway

From the repository root, `bash scripts/prepare-ai-stack.sh --init-env` fills in the keys (creating
`litellm/.env` from its template when missing) and `bash scripts/prepare-ai-stack.sh --start` generates
the configuration, creates the LiteLLM database, issues the SkyLab service key and starts the stack.
Addresses, keys, remote models and taking over a standalone gateway are covered in the
[AI API User Manual](/guides/ai-api-user-manual). Points specific to this directory:

- The LiteLLM container reaches the local instances through `host.docker.internal`, so `.env.API` must
  set `API_HOST=0.0.0.0` (restrict the engine ports with a firewall). `docker compose up/down` at the root
  manages LiteLLM but not the vLLM processes on the host.
- `tools/generate_litellm_config.py`, which `prepare-ai-stack.sh` also uses, renders `models.json` and
  `litellm/config.template.yaml` into `litellm/config.yaml` (ignored by Git). Keys are written as
  `os.environ/...` references, except a remote model's literal `apikeys`, which goes into the file itself;
  for that reason the file is always written, or an existing copy tightened, with mode `0600` (owner
  read/write only). Generate it as the account that runs Docker: the LiteLLM container reads it as root,
  which under rootless Docker is that account. The script's default `--mode integration` contains no
  database settings; `--mode production` requires `LITELLM_SERVICE_API_KEY` to be injected and adds the
  `DATABASE_URL` reference.

## Benchmark

```bash
./run_sharegpt_benchmark.sh --download        # download the ShareGPT_V3 dataset into test_datasets/
LITELLM_API_KEY=<service-key> ./run_sharegpt_benchmark.sh -n 100 -c 20 --model qwen3-14b
./run_sharegpt_benchmark.sh --target single -n 100 -c 20   # hit the single-model vLLM from .env.interface directly
```

Targets, key lookup, options and the report are described in
[ShareGPT benchmark](/guides/vllm-sharegpt-quickstart). `python -m benchmark.async_bench` instead
stress-tests one vLLM instance directly with a single prompt, using `vllm-service/.env` (or the file
named by `VLLM_SERVICE_ENV_FILE`).

## Directory responsibilities

| Path | Responsibility |
| --- | --- |
| `main.py` | CLI entry point, supports `single` / `cluster` |
| `start_single_model.sh`, `start_multi_model_cluster.sh` | Background launchers (PID in `.runtime/`, output in `logs/`) |
| `model_deployment.py` | Local / remote deployment detection and upstream route validation |
| `core/engine.py` | Start/stop, health check and logging of a single vLLM instance |
| `core/cluster.py` | Multi-model instance lifecycle, including restarting a crashed model |
| `core/supervisor.py` | Crash restart policy (attempt limit, backoff) shared by both modes |
| `config/settings.py` | Shared vLLM settings |
| `config/multi_model.py` | `models.json` loading and cluster resource checks |
| `utils/` | Logging and pre-start health checks |
| `litellm/` | LiteLLM Compose file and the tracked routing policy template |
| `tools/` | LiteLLM deployment tools (`generate_litellm_config.py` generates the config; `prepare_ai_stack.py` checks key boundaries and prepares / starts the AI stack) and the SkyLab AI integration test (`campus_ai_integration_test.py`) |
| `benchmark/`, `run_sharegpt_benchmark.py`, `run_sharegpt_benchmark.sh` | async / ShareGPT benchmark (ShareGPT goes through LiteLLM by default) |
| `tests/` | pytest suite, runs without a GPU or model service |

## Legacy directories

`vllm-API/` and `vllm-inference/` (the earlier multi-model gateway and single-model deployment) are not in
the repo and are ignored via `.gitignore`. They may remain as untracked local copies on existing
deployment hosts for migration reference only; all maintenance happens in `vllm-service/`.
