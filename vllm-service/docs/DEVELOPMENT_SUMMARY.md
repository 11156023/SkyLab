# vLLM Service Development Summary

## Current Direction

`vllm-service` has been converged into a service-only vLLM runtime:

- `single` mode for one OpenAI-compatible vLLM server
- `gateway` / `cluster` mode for multi-model routing
- FastAPI Gateway for `/v1/*` API proxying
- no bundled React/Vite frontend

## Preserved Capabilities

- OpenAI-compatible model listing and completions
- streaming chat proxy
- image, document, and video compatibility APIs
- per-model alias routing from `models.json`
- upload size/type validation
- async upstream HTTP client and inflight limiting
- ShareGPT and async benchmark utilities

## Removed Scope

- React application
- Vite dev server
- frontend build output serving
- `start_webapp.sh`
- browser-oriented Web UI documentation

## Validation Strategy

For service-only changes, use:

```bash
python -B -m py_compile vllm-service/main.py vllm-service/gateway/main.py
python -B vllm-service/main.py --help
python -B vllm-service/main.py single --help
python -B vllm-service/main.py gateway --help
```

Runtime validation still requires a machine with the intended GPU/CUDA/vLLM setup.
