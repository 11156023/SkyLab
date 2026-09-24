# vLLM Service

`vllm-service/` 是 SkyLab 的 canonical vLLM 推論服務目錄，收斂原本的
`vllm-inference/` 單模型部署與 `vllm-API/` 多模型 Gateway。

## 服務模式

| 模式 | 腳本 | 用途 | 對外端點 |
| --- | --- | --- | --- |
| 單一模型主服務 | `./start_single_model.sh` | 系統內部 AI、MVP、單模型除錯 | `http://<API_HOST>:<API_PORT>/v1` |
| 多模型 vLLM cluster | `./start_multi_model_cluster.sh` | 只啟動各模型 instance，供 LiteLLM 使用 | `http://127.0.0.1:8103/8104/v1` |
| 舊多模型 Gateway（回滾用） | `python main.py gateway` | 遷移觀察期的回滾路徑 | `http://<GATEWAY_HOST>:<GATEWAY_PORT>/v1` |

## 快速開始

```bash
cd vllm-service
```

先依 GPU/CUDA/平台版本安裝 vLLM；`requirements.txt` 只列出服務周邊依賴，未固定
GPU wheel：

```bash
pip install -r requirements.txt
pip install vllm
```

依部署模式編輯對應設定檔：

- 單模型模式讀取 `.env.interface`，主要看 `MODEL_NAME`、`API_PORT`、`API_KEY` 與單模型 vLLM 容量參數。
- Gateway 模式讀取 `.env.API`，主要看 `GATEWAY_*`、共用部署值與 `models.json`。
- `models.json` 管理多模型各自的 `model_name`、`api_port`、engine/parser 參數。
- 影片、文件、溫度、Top-P/K、重複懲罰等不常改的推論預設值集中在 `config/settings.py`。
- `API_KEY` 是 vLLM 各 instance 的 Bearer key。LiteLLM 以
  `VLLM_UPSTREAM_API_KEY` 取得同一個值，轉發到本機 vLLM `/v1`；變數名稱分開只是
  Docker 容器注入邊界，並非第二組權限。舊 Gateway 回滾路徑才使用 backend 的
  `AI_API_API_KEY`。

## 啟動單一模型主服務

```bash
bash ./start_single_model.sh
```

此腳本會背景啟動服務，主控輸出寫入 `logs/main.log`，launcher PID 寫入
`.runtime/single-model.pid`。若 PID 仍在執行，再次啟動會直接提示既有進程。

等同：

```bash
python main.py single --env-file .env.interface
```

此模式會啟動一個 vLLM OpenAI-compatible server。主 backend 的內部 AI 功能可用：

```env
VLLM_BASE_URL=http://localhost:8000/v1
VLLM_API_KEY=vllm-secret-key-change-me
VLLM_MODEL_NAME=<MODEL_NAME>
```

## 啟動多模型 vLLM cluster（LiteLLM 遷移路徑）

```bash
bash ./start_multi_model_cluster.sh
python ./tools/generate_litellm_config.py --mode integration
```

cluster 腳本等同 `python main.py cluster --no-gateway --base-env .env.API`。它只管理
vLLM instance 的啟動、ready check 與優雅關閉；模型 alias／路由由 LiteLLM 的產生設定管理。
每個 `models.json` entry 必須有唯一的 `alias`、`served_model_name` 與 `api_port`。
`served_model_name` 會傳入 vLLM 的 `--served-model-name`，所以各 instance 的
`/v1/models` 不會暴露主機模型路徑。

`generate_litellm_config.py` 讀取 `models.json` 與 `litellm/config.template.yaml`，產出
`litellm/config.yaml`。產物已由 Git 忽略，且只含 `os.environ/...` secret reference，不含任何明文 key。
`integration` 模式不含資料庫設定；`production` 模式要求部署程序先注入
`LITELLM_SERVICE_API_KEY`，並產生 `DATABASE_URL` reference。

LiteLLM 與 Campus 主 Compose 是兩個獨立專案。先建立 `litellm/.env`（可由
`litellm/.env.example` 複製），再從該目錄啟動：

```bash
cd litellm
docker compose up -d
```

Campus backend 未來切換時，仍透過根目錄 `.env` 的 `AI_API_BASE_URL`、
`AI_API_API_KEY` 與 `LITELLM_RUNTIME_*` 連往獨立 gateway；同機 host-network
部署使用 `http://host.docker.internal:4000`。根目錄 `docker-compose.yml` 不會啟動、
停止或掛載 LiteLLM。

## 舊多模型 API Gateway（僅回滾）

```bash
python main.py gateway
```


此腳本會背景啟動服務，主控輸出寫入 `logs/main.log`，Gateway API 輸出寫入
`logs/gateway.log`，各模型 instance 另寫入 `logs/<alias>.log`。launcher PID 寫入
`.runtime/multi-model-gateway.pid`。若 PID 仍在執行，再次啟動會直接提示既有進程。

等同：

```bash
python main.py gateway --base-env .env.API
```

此模式會：

1. 讀取 `.env.API` 的共用設定與 `GATEWAY_*`。
2. 讀取 `models.json` 的模型 alias 與各模型 port。
3. 依序啟動每個 vLLM instance。
4. 啟動 FastAPI Gateway，提供 `/v1/models`、`/v1/chat/completions`、`/v1/completions`。

在 LiteLLM 切換前，主 backend 的對外 AI API proxy 仍可用：

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
| `litellm/` | Git-managed LiteLLM 靜態 routing policy template |
| `gateway/main.py` | 純 FastAPI Gateway/API service；不提供前端 |
| `tools/` | 單模型呼叫工具與 SkyLab AI 整合測試 |
| `benchmark/` | async / ShareGPT benchmark |

## 前端狀態

`vllm-service` 只提供推論服務與遷移期間的舊 Gateway，不再維護 React/Vite 前端。
若需要互動介面，請由 SkyLab 主 frontend 或外部 OpenAI-compatible client 呼叫 Campus backend。

## 舊目錄狀態

`vllm-API/` 與 `vllm-inference/` 暫時保留作為遷移參考。新的維護入口應優先使用
`vllm-service/`。
