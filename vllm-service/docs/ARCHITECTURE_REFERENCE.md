# vLLM Service Architecture Reference

## Runtime Layout

```text
Campus-Cloud backend
  ├─ internal AI clients -> VLLM_BASE_URL=http://localhost:8000/v1
  └─ AI proxy          -> AI_API_BASE_URL=http://localhost:3000

vllm-service
  ├─ single mode
  │   └─ one vLLM OpenAI-compatible server
  └─ gateway mode
      ├─ model instance A -> http://127.0.0.1:<api_port>/v1
      ├─ model instance B -> http://127.0.0.1:<api_port>/v1
      └─ FastAPI Gateway -> http://0.0.0.0:3000
```

## Launcher Flow

`python main.py gateway`:

1. Load `.env`.
2. Load `models.json`.
3. Validate duplicate aliases, duplicate ports, and aggregate GPU memory settings.
4. Start each vLLM instance sequentially through `MultiModelEngineManager`.
5. Start `uvicorn webapp.backend.main:app`.
6. Wait for Gateway `/health`.

`python main.py single`:

1. Load `.env`.
2. Build one `VLLMEngine`.
3. Start the vLLM OpenAI-compatible server.

## Gateway Routing

`webapp/backend/main.py` builds routes from `models.json`:

- requested `model` matches alias first
- then exact resolved model name
- then case-insensitive alias
- then model basename

The upstream payload replaces `model` with the resolved vLLM model path/name before forwarding.

## API Contract

Primary service endpoints:

- `GET /health`
- `GET /ready`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/completions`

Compatibility endpoints:

- `/api/model-info`
- `/api/config`
- `/api/chat*`

These compatibility endpoints are service APIs, not a browser UI contract.

## Frontend Boundary

`vllm-service` intentionally does not ship a frontend. Do not add React/Vite assets,
static file mounts, or frontend dev-server startup scripts under this service.
