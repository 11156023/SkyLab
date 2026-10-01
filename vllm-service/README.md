# vLLM Service

`vllm-service/` 是 SkyLab 的 canonical vLLM 推論服務目錄，已收斂早期的
`vllm-inference/` 單模型部署與 `vllm-API/` 多模型 Gateway（這兩個舊目錄不在 repo 內，見文末）。
多模型的對外 API 一律由 LiteLLM（`litellm/`）提供；早期自寫的 FastAPI Gateway 已移除。

## 服務模式

| 模式 | 腳本 | 用途 | 對外端點 |
| --- | --- | --- | --- |
| 單一模型主服務 | `./start_single_model.sh` | 系統內部 AI、MVP、單模型除錯 | `http://<API_HOST>:<API_PORT>/v1` |
| 多模型 vLLM cluster | `./start_multi_model_cluster.sh` | 只啟動各模型 instance，供 LiteLLM 使用 | `http://127.0.0.1:8103/8104/v1` |
| LiteLLM gateway | `bash scripts/prepare-ai-stack.sh --start`（repo 根目錄） | 多模型對外 API、金鑰與路由 | `http://127.0.0.1:4000/v1` |

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
- 多模型 cluster 讀取 `.env.API`（各模型共用的部署值）與 `models.json`。
- `models.json` 管理多模型各自的 `model_name`、`api_port`、engine/parser 參數。
- 目前第二個 instance 使用 `AImodels/NVIDIA-Nemotron-Nano-9B-v2-FP8`；Nemotron
  的 Mamba SSM cache 會以 `float32` 啟動，並以 32K context / 16 路併發作為多模型
  初始部署基線；另外關閉 Nemotron 的實驗性 prefix cache。
- `.env.API` 將 `MAX_JOBS` / `NINJAFLAGS` 限制為單工，避免第二個模型首次做
  FlashInfer CUDA JIT 時耗盡主機記憶體。
- `API_KEY` 是 vLLM 各 instance 的 Bearer key。LiteLLM 以
  `VLLM_UPSTREAM_API_KEY` 取得同一個值，轉發到本機 vLLM `/v1`；變數名稱分開只是
  Docker 容器注入邊界，並非第二組權限。backend 呼叫 LiteLLM 用的是另一把受限的
  service key（根目錄 `.env` 的 `AI_API_API_KEY`）。

### 升級注意：`ALLOWED_LOCAL_MEDIA_PATH`

`ALLOWED_LOCAL_MEDIA_PATH` 現在預設為空（關閉 `file://` 本機媒體讀取），且設為 `/`
時啟動會直接失敗（設定驗證錯誤）。舊版 `.env.example` 內建 `ALLOWED_LOCAL_MEDIA_PATH=/`，
從舊範本複製出來的 `.env.API`、`.env.interface`，以及在 `models.json` entry 設定
`allowed_local_media_path: "/"` 的部署，重啟前必須先清空該值，或改指向專用的媒體目錄
（任何能呼叫 API 的使用者都能讀到該目錄下的檔案）。這會影響 `main.py single`／`cluster`
與 benchmark，因為它們都經 `Settings` 讀設定。

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

## 啟動多模型 vLLM cluster（主要 AI API 服務）

```bash
bash ./start_multi_model_cluster.sh
LITELLM_SERVICE_API_KEY=<campus-service-key-from-secret-manager> \
  python ./tools/generate_litellm_config.py --mode production
```

cluster 腳本等同 `python main.py cluster --base-env .env.API`（舊指令帶的 `--no-gateway`
仍會被接受但不影響行為）。它只管理
vLLM instance 的啟動、ready check 與優雅關閉；模型 alias／路由由 LiteLLM 的產生設定管理。
每個 `models.json` entry 必須有唯一的 `alias`；本機模型的 `served_model_name` 與 `api_port` 也必須唯一。
`served_model_name` 會傳入 vLLM 的 `--served-model-name`，所以各 instance 的
`/v1/models` 不會暴露主機模型路徑。

若需重新下載 Nemotron 模型，可使用固定 revision，避免模型檔案更新造成不可重現的部署：

```bash
./.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8', revision='8bc5eece2eb5514c4bca7f2ec655b91eb554f4c0', local_dir='AImodels/NVIDIA-Nemotron-Nano-9B-v2-FP8', max_workers=4)"
```

`generate_litellm_config.py` 讀取 `models.json` 與 `litellm/config.template.yaml`，產出
`litellm/config.yaml`。產物已由 Git 忽略；一般模型使用 `os.environ/...` secret reference，
遠端模型若設定 `apikeys`，該值會直接寫入這份本機 runtime 產物。
`integration` 模式不含資料庫設定；`production` 模式要求部署程序先注入
`LITELLM_SERVICE_API_KEY`，並產生 `DATABASE_URL` reference。

LiteLLM 已由 Campus 主 Compose `include` 引用，原獨立 Compose 仍保留。
從專案根目錄先補齊金鑰（缺 `litellm/.env` 時由範本建立），再啟動：

```bash
cd ..
bash scripts/prepare-ai-stack.sh --init-env
bash scripts/prepare-ai-stack.sh --start
```

`--start` 會自動建立 LiteLLM 專用資料庫（`db:5432`，不經 PgBouncer）並核發／同步
Campus service key。Campus backend 透過根目錄 `.env` 的 `AI_API_BASE_URL`、
`AI_API_API_KEY` 與 `LITELLM_RUNTIME_*` 連往 gateway；Compose backend 在同一內網使用
`http://litellm:4000`，主機程序執行的 backend 使用 `http://127.0.0.1:4000`。
LiteLLM 容器經 `host.docker.internal` 連本機 vLLM，所以 `.env.API` 的 `API_HOST`
須為 `0.0.0.0`（以防火牆限制引擎埠）。根目錄 `docker compose up/down` 會管理
LiteLLM，但不管理主機上的 vLLM 程序。

跨主機模型請在 `models.json` 設定 `deployment: "remote"`、`api_base`（含 `/v1`），
並選擇 `api_key_env`（值放在 `litellm/.env`）或 literal `apikeys`。兩者都未指定時沿用
`VLLM_UPSTREAM_API_KEY`；`apikeys` 與 `api_key_env` 不可同時設定。本機啟動器略過這些項目；產生器將它們
整合至相同的 LiteLLM 路由。完整範例、既有獨立容器接管和使用者 API 操作見
[AI API 使用手冊](../docs/ai-api-user-manual.md)。

## Benchmark

```bash
./run_sharegpt_benchmark.sh --download        # 下載 ShareGPT_V3 資料集到 test_datasets/
LITELLM_API_KEY=<service-key> ./run_sharegpt_benchmark.sh -n 100 -c 20 --model qwen3-14b
./run_sharegpt_benchmark.sh --target single -n 100 -c 20   # 直連 .env.interface 的單模型 vLLM
```

ShareGPT benchmark 預設打 LiteLLM（`LITELLM_BASE_URL`，預設 `http://127.0.0.1:4000/v1`），
金鑰依序讀 `LITELLM_API_KEY`、`AI_API_API_KEY` 環境變數，再退回 repo 根目錄 `.env` 的
`AI_API_API_KEY`。`python -m benchmark.async_bench` 則以單一 prompt 直接壓測 `.env` 設定的
vLLM instance。完整說明見 [docs/SHAREGPT_QUICKSTART.md](docs/SHAREGPT_QUICKSTART.md)。

## 目錄責任

| 路徑 | 責任 |
| --- | --- |
| `main.py` | CLI 入口，支援 `single` / `cluster` |
| `core/engine.py` | 單一 vLLM instance 啟停、health check、日誌 |
| `core/cluster.py` | 多模型 instance 生命週期 |
| `config/settings.py` | 共用 vLLM 設定 |
| `config/multi_model.py` | `models.json` 載入與 cluster 資源檢查 |
| `litellm/` | Git-managed LiteLLM 靜態 routing policy template |
| `tools/` | SkyLab AI 整合測試（`campus_ai_integration_test.py`），以及 LiteLLM 部署工具（`generate_litellm_config.py` 產生 LiteLLM 設定、`prepare_ai_stack.py` 檢查金鑰邊界並準備／啟動 AI stack） |
| `benchmark/` | async / ShareGPT benchmark（ShareGPT 預設經 LiteLLM） |

## 前端狀態

`vllm-service` 只提供推論服務與 LiteLLM gateway 設定，不再維護 React/Vite 前端。
若需要互動介面，請由 SkyLab 主 frontend 或外部 OpenAI-compatible client 呼叫 Campus backend。

## 舊目錄狀態

`vllm-API/` 與 `vllm-inference/` 已不在 repo 內（兩者皆由 `.gitignore` 忽略），只可能以
未追蹤的本機副本留在既有部署主機上供遷移對照，且不再維護。所有維護一律在 `vllm-service/` 進行。
