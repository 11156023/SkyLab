# vLLM Service Project Overview

> **English** | [繁體中文](./PROJECT_OVERVIEW.zh-TW.md)

`vllm-service/` is SkyLab's canonical vLLM inference service. It provides server-side capabilities only:

- Single-model OpenAI-compatible vLLM server
- Multi-model vLLM cluster (one independent instance per model)
- LiteLLM gateway configuration (public multi-model API, keys and routing)
- Benchmark tools

This service does not ship a React/Vite frontend; the interactive interface is the responsibility of the
SkyLab main frontend or an external OpenAI-compatible client. The early hand-written FastAPI gateway has been
removed, and the multi-model API always goes through LiteLLM.

## Service modes

| Mode | Command | Purpose |
| --- | --- | --- |
| Single | `python main.py single` (`start_single_model.sh`) | Starts a single vLLM instance for internal AI to call directly |
| Cluster | `python main.py cluster` (default mode, `start_multi_model_cluster.sh`) | Starts multiple local vLLM instances according to `models.json`; routing and the public API are left to LiteLLM |

`python main.py gateway` is no longer supported; running it fails immediately with a hint to use cluster + LiteLLM instead.
The `--no-gateway` and `--gateway-ready-timeout` flags from the old command are still accepted but have no effect.

## Main endpoints

| Service | Default address | Notes |
| --- | --- | --- |
| Single-model vLLM | `http://<API_HOST>:<API_PORT>/v1` | `.env.interface` |
| Each cluster instance | `http://127.0.0.1:<api_port>/v1` | `api_port` from `models.json`; only LiteLLM connects to it |
| LiteLLM gateway | `http://127.0.0.1:4000/v1` (`http://litellm:4000` on the Compose network) | OpenAI-compatible endpoints such as `GET /v1/models`, `POST /v1/chat/completions` |

## Configuration boundaries

The main Campus-Cloud backend uses two different settings:

```env
VLLM_BASE_URL=http://localhost:8000/v1
AI_API_BASE_URL=http://litellm:4000
```

- `VLLM_BASE_URL` points at the single-model main service and includes `/v1`.
- `AI_API_BASE_URL` points at the LiteLLM gateway root, without `/v1`; the key is the restricted service key `AI_API_API_KEY`.
- Public multi-model aliases, per-model ports and remote models are managed in `models.json`;
  `tools/generate_litellm_config.py` generates `litellm/config.yaml` from it.

## Directory

```text
vllm-service/
├── main.py                     # launcher: single / cluster
├── start_single_model.sh
├── start_multi_model_cluster.sh
├── model_deployment.py         # local / remote deployment detection and upstream connection
├── config/                     # Settings and models.json loading
├── core/                       # vLLM instance and cluster lifecycle
├── utils/                      # logging, pre-start health checks
├── litellm/                    # LiteLLM Compose and config template
├── tools/                      # LiteLLM config generation / deployment tools, backend AI integration test
├── benchmark/                  # async / ShareGPT benchmark
├── run_sharegpt_benchmark.py
└── run_sharegpt_benchmark.sh
```

## Related documents

- [README.md](../README.md): installation, startup and the LiteLLM deployment flow
- [SHAREGPT_QUICKSTART.md](SHAREGPT_QUICKSTART.md): ShareGPT benchmark usage
- [AI API User Manual](../../docs/ai-api-user-manual.md): LiteLLM deployment, keys and user API operations
