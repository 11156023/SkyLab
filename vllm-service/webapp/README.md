# vLLM Service — API Gateway

`vllm-service/webapp/backend/main.py` 是純 FastAPI API Gateway。它負責把 OpenAI-compatible
請求依 `model` alias 轉發到對應的 vLLM instance；本目錄不再提供 React/Vite 前端。

## 功能

- 多模型路由：依 `model` 參數轉發到對應上游 vLLM instance
- OpenAI-compatible API：`/v1/models`、`/v1/chat/completions`、`/v1/completions`
- 相容 demo/integration API：`/api/chat*`、圖片、文件、影片串流端點
- SSE 串流轉發
- inflight semaphore 保護
- 模型列表 60 秒快取
- 上傳檔案大小與類型驗證
- 上游連線池與非同步轉發

## 架構

```text
vllm-service/
├── main.py                       # 啟動入口：single 或 gateway/cluster
├── models.json                   # 多模型實例設定
├── config/
│   ├── settings.py               # 共用設定
│   └── multi_model.py            # 載入 models.json + Gateway 設定
├── core/
│   ├── engine.py                 # 單一 vLLM 實例啟停
│   └── cluster.py                # MultiModelEngineManager
└── webapp/
    └── backend/main.py           # FastAPI API Gateway
```

## Gateway API

Gateway 預設位於 port `3000`，主 backend 以 `AI_API_BASE_URL=http://localhost:3000`
對接，並自行 append `/v1/...` 路徑。

| 端點 | 方法 | 說明 |
| --- | --- | --- |
| `/health` | GET | Gateway liveness |
| `/ready` | GET | 檢查已設定的上游模型健康狀態 |
| `/v1/models` | GET | OpenAI-compatible 模型列表 |
| `/v1/chat/completions` | POST | OpenAI-compatible chat completions proxy |
| `/v1/completions` | POST | OpenAI-compatible completions proxy |
| `/api/model-info` | GET | 相容既有工具的模型資訊 |
| `/api/config` | GET | 相容既有工具的推論預設值 |
| `/api/chat` | POST | 相容既有工具的文字對話 |
| `/api/chat/stream` | POST | 相容既有工具的文字串流 |
| `/api/chat/vision/stream` | POST | 圖片串流分析 |
| `/api/chat/document/stream` | POST | 文件串流分析 |
| `/api/chat/video/info` | POST | 影片預檢資訊 |
| `/api/chat/video/stream` | POST | 影片串流分析 |

## 啟動

完整多模型 Gateway：

```bash
cd vllm-service
cp .env.example .env
cp models.json.example models.json

pip install -r requirements.txt
# 依 GPU/CUDA/平台版本另行安裝 vllm

python main.py gateway
```

只啟動 Gateway API 而不由 launcher 管理模型時：

```bash
cd vllm-service
python -m uvicorn webapp.backend.main:app --host 0.0.0.0 --port 3000
```

## 呼叫範例

```bash
curl http://localhost:3000/v1/models \
  -H "Authorization: Bearer vllm-secret-key-change-me"
```

```bash
curl http://localhost:3000/v1/chat/completions \
  -H "Authorization: Bearer vllm-secret-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-14b",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
  }'
```

## 設計重點

- `main.py gateway` 會啟動多個 vLLM instance，再啟動 `webapp.backend.main:app`。
- `GATEWAY_MAX_INFLIGHT` 限制同時轉發的請求數，避免 Gateway 被打爆。
- `models.json` 是多模型 alias 與 per-model port 的唯一來源。
- 本服務不再提供瀏覽器前端；請用 SkyLab 主 frontend 或其他 OpenAI-compatible client 呼叫。
