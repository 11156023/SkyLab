# vLLM Service

`vllm-service/` 是 SkyLab 的 canonical vLLM 推論服務目錄，收斂原本的
`vllm-inference/` 單模型部署與 `vllm-API/` 多模型 Gateway。

## 服務模式

| 模式 | 腳本 | 用途 | 對外端點 |
| --- | --- | --- | --- |
| 單一模型主服務 | `./start_single_model.sh` | 系統內部 AI、MVP、單模型除錯 | `http://<API_HOST>:<API_PORT>/v1` |
| 多模型 API Gateway | `./start_multi_model_gateway.sh` | 對外 AI API、模型 alias、使用量代理 | `http://<GATEWAY_HOST>:<GATEWAY_PORT>/v1` |

## 快速開始

```bash
cd vllm-service
cp .env.example .env
cp models.json.example models.json
```

先依 GPU/CUDA/平台版本安裝 vLLM；`requirements.txt` 只列出服務周邊依賴，未固定
GPU wheel：

```bash
pip install -r requirements.txt
pip install vllm
```

依部署需求編輯 `.env`：

- 單模型模式主要看 `MODEL_NAME`、`API_PORT`、`API_KEY`。
- Gateway 模式主要看 `GATEWAY_*` 與 `models.json`。
- `API_KEY` 需與主 backend 的 `VLLM_API_KEY` / `AI_API_API_KEY` 對齊。

## 啟動單一模型主服務

```bash
bash ./start_single_model.sh
```

等同：

```bash
python main.py single
```

此模式會啟動一個 vLLM OpenAI-compatible server。主 backend 的內部 AI 功能可用：

```env
VLLM_BASE_URL=http://localhost:8000/v1
VLLM_API_KEY=vllm-secret-key-change-me
VLLM_MODEL_NAME=<MODEL_NAME>
```

## 啟動多模型 API Gateway

```bash
bash ./start_multi_model_gateway.sh
```

等同：

```bash
python main.py gateway
```

此模式會：

1. 讀取 `.env` 的共用設定。
2. 讀取 `models.json` 的模型 alias 與各模型 port。
3. 依序啟動每個 vLLM instance。
4. 啟動 FastAPI Gateway，提供 `/v1/models`、`/v1/chat/completions`、`/v1/completions`。

主 backend 的對外 AI API proxy 可用：

```env
AI_API_BASE_URL=http://localhost:3000
AI_API_API_KEY=vllm-secret-key-change-me
```

## 目錄責任

| 路徑 | 責任 |
| --- | --- |
| `main.py` | CLI 入口，支援 `single` / `gateway` / `cluster` |
| `core/engine.py` | 單一 vLLM instance 啟停、health check、日誌 |
| `core/cluster.py` | 多模型 instance 生命週期 |
| `config/settings.py` | 共用 vLLM 設定 |
| `config/multi_model.py` | `models.json` 載入、Gateway route 建立 |
| `webapp/backend/main.py` | 純 FastAPI Gateway/API service；不提供前端 |
| `tools/` | 單模型呼叫工具與 SkyLab AI 整合測試 |
| `benchmark/` | async / ShareGPT benchmark |

## 前端狀態

`vllm-service` 只提供推論服務與 API Gateway，不再維護 React/Vite 前端。
若需要互動介面，請由 SkyLab 主 frontend 或外部 OpenAI-compatible client 呼叫 Gateway。

## 舊目錄狀態

`vllm-API/` 與 `vllm-inference/` 暫時保留作為遷移參考。新的維護入口應優先使用
`vllm-service/`。
