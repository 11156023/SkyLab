# vLLM Service Project Overview

`vllm-service/` 是 SkyLab 的 canonical vLLM 推論服務。它只提供服務端能力：

- 單模型 OpenAI-compatible vLLM server
- 多模型 API Gateway
- 多模態 API 工具與 benchmark

本服務不提供 React/Vite 前端；互動介面由 SkyLab 主 frontend 或外部
OpenAI-compatible client 負責。

## 服務模式

| 模式 | 指令 | 用途 |
| --- | --- | --- |
| Single | `python main.py single` | 啟動單一 vLLM instance，供內部 AI 直接呼叫 |
| Gateway | `python main.py gateway` | 啟動多個 vLLM instance 與 FastAPI Gateway |
| Cluster | `python main.py cluster` | `gateway` 的相容別名 |

## 主要端點

Gateway 預設位於 `http://localhost:3000`：

- `GET /health`
- `GET /ready`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/completions`

內部工具相容端點仍保留：

- `POST /api/chat`
- `POST /api/chat/stream`
- `POST /api/chat/vision/stream`
- `POST /api/chat/document/stream`
- `POST /api/chat/video/info`
- `POST /api/chat/video/stream`

## 設定邊界

主 Campus-Cloud backend 使用兩條不同設定：

```env
VLLM_BASE_URL=http://localhost:8000/v1
AI_API_BASE_URL=http://localhost:3000
```

- `VLLM_BASE_URL` 指向單模型主服務，且包含 `/v1`。
- `AI_API_BASE_URL` 指向 Gateway root，不包含 `/v1`。
- 多模型 alias 與 per-model port 由 `models.json` 管理。

## 目錄

```text
vllm-service/
├── main.py
├── start_single_model.sh
├── start_multi_model_gateway.sh
├── config/
├── core/
├── api/
├── utils/
├── tools/
├── benchmark/
└── gateway/
    └── main.py
```

`gateway/main.py` 是多模型 Gateway 的 FastAPI app，launcher 會以
`uvicorn gateway.main:app` 啟動。
