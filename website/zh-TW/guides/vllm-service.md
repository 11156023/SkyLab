# vLLM 服務

`vllm-service/` 是 SkyLab 的 vLLM 推論服務。它負責啟動與監看 vLLM 程序，並存放 LiteLLM gateway 設定；
本身沒有使用者介面。多模型的對外 API 一律由 LiteLLM（`litellm/`）提供，使用者經 SkyLab 或任何
OpenAI 相容 client 使用模型。

## 服務模式

| 模式 | 啟動方式 | 用途 | 端點 |
| --- | --- | --- | --- |
| 單一模型主服務 | `./start_single_model.sh`（`python main.py single`） | 系統內建 AI、單模型除錯 | `.env.interface` 的 `http://<API_HOST>:<API_PORT>/v1` |
| 多模型 vLLM cluster | `./start_multi_model_cluster.sh`（`python main.py cluster`，預設模式） | 依 `models.json` 為每個本機模型各起一個 vLLM instance，只給 LiteLLM 連 | `http://127.0.0.1:<api_port>/v1`，例如 8103／8104 |
| LiteLLM gateway | `bash scripts/prepare-ai-stack.sh --start`（repo 根目錄） | 多模型對外 API、金鑰與路由 | 主機上 `http://127.0.0.1:4000/v1`，Compose 內網 `http://litellm:4000` |

主 SkyLab backend 在根目錄 `.env` 使用兩條不同設定：

- `VLLM_BASE_URL` 指向單模型主服務，且包含 `/v1`；系統內建 AI 功能使用它。
- `AI_API_BASE_URL` 指向 LiteLLM gateway root，不包含 `/v1`，搭配受限的 service key `AI_API_API_KEY`；
  對外 AI API 經由它轉送。

## 快速開始

在 `vllm-service/` 內操作。先依 GPU／CUDA／平台版本安裝 vLLM；`requirements.txt` 只列出服務本身的
依賴，未固定 GPU wheel：

```bash
cd vllm-service
pip install -r requirements.txt
pip install vllm
```

由版控內的範例建立設定（實際檔案都由 Git 忽略）：

- `.env.example` 是完整的設定參考。單模型模式複製成 `.env.interface`（主要看 `MODEL_NAME`、
  `API_PORT`、`API_KEY` 與單模型容量參數），cluster 複製成 `.env.API`（各模型共用的部署值）。
- `models.json.example` 複製成 `models.json`，存放各模型自己的 `model_name`、`api_port` 與
  engine／parser 參數。範例附兩個本機項目（`gemma-4-31b` 用 8103、`qwen3-14b` 用 8104）與兩個遠端
  項目，請換成實際部署的模型；本頁其餘出現的模型名稱都只是範例，不代表哪裡正在跑什麼。

需要留意的設定：

- `API_KEY` 是各 vLLM instance 的 Bearer key。LiteLLM 以 `VLLM_UPSTREAM_API_KEY` 取得同一個值，
  轉發到本機 vLLM `/v1`；變數名稱分開只是反映注入到哪個容器，並非第二組權限。backend 呼叫 LiteLLM
  用的是另一把受限的 service key（根目錄 `.env` 的 `AI_API_API_KEY`）。
- `ALLOWED_LOCAL_MEDIA_PATH`（`models.json` 項目中為 `allowed_local_media_path`）預設為空，關閉
  `file://` 本機媒體讀取；設為 `/` 時啟動會直接失敗。真的需要時只能指向專用的媒體目錄：任何能呼叫
  API 的使用者都能讀到該目錄下的檔案。從舊範本複製出來的 `.env.API`、`.env.interface` 可能仍是 `/`，
  重啟前請先清空。
- 帶 Mamba SSM cache 的模型（例如 NVIDIA Nemotron Nano 系列）請在其 `models.json` 項目設定
  `mamba_ssm_cache_dtype` 為 `float32`、`enable_prefix_caching` 為 `false`（prefix cache 預設開啟，
  對這類模型仍是實驗性功能）；32K context／16 路併發是合理的初始基線。
- `.env.API` 與 `.env.interface` 只提供 launcher 自己的設定，其他變數會被忽略。用來避免第二個模型首次做
  FlashInfer CUDA JIT 時耗盡主機記憶體的 `MAX_JOBS`／`NINJAFLAGS` 等建置變數，必須在執行啟動腳本的
  shell 中 export；launcher 會把自己的環境變數傳給 vLLM。

## 啟動單一模型主服務

```bash
bash ./start_single_model.sh   # 等同 python main.py single --env-file .env.interface
```

此腳本會背景啟動服務，輸出寫入 `logs/main.log`，launcher PID 寫入 `.runtime/single-model.pid`。
若該 PID 仍在執行，再次啟動只會提示既有程序。

backend 的內建 AI 功能接著使用（根目錄 `.env`）：

```env
VLLM_BASE_URL=http://<推論主機>:8000/v1
VLLM_API_KEY=<.env.interface 的 API_KEY>
VLLM_MODEL_NAME=<該 instance 提供的模型 ID>
```

`VLLM_MODEL_NAME` 是 instance 提供的模型 ID（`SERVED_MODEL_NAME`，未設定時為模型路徑），且必須與
`backend/config/llm-model-profiles.yaml` 的 model key 完全一致。在 Compose stack 內 `localhost` 指的是
backend 容器自己，請改用該容器連得到的推論主機位址。

## 啟動多模型 vLLM cluster

```bash
bash ./start_multi_model_cluster.sh   # 等同 python main.py cluster --base-env .env.API
```

launcher 只啟動 `models.json` 列出的本機 instance（`deployment: "remote"` 的項目會略過），並負責
ready check 與優雅關閉；launcher 的輸出寫入 `logs/cluster.log`，各模型寫入 `logs/<alias>.log`。
模型 alias 與路由由產生的 LiteLLM 設定管理。每個 `models.json` 項目必須有唯一的 `alias`；本機項目的
`served_model_name` 與 `api_port` 也必須唯一。`served_model_name` 會傳入 vLLM 的
`--served-model-name`，所以各 instance 的 `/v1/models` 不會暴露主機模型路徑。

### 崩潰自動重啟

兩種模式下，launcher 在啟動完成後都會持續監看 vLLM 程序。某個程序意外結束時，記錄退出碼、
清掉它程序群組裡殘留的 worker（釋放 GPU 記憶體與 port），然後**只重啟那個模型**，其他模型照常
服務。重啟時不等待就緒；該模型的 `logs/<alias>.log` 會接續寫入，保留崩潰前的輸出。重啟次數有
上限並逐次拉長等待，在 `.env.API`／`.env.interface` 設定：

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `ENGINE_RESTART_MAX_ATTEMPTS` | `3` | 每個模型放棄前最多重啟幾次；`0`＝不自動重啟 |
| `ENGINE_RESTART_BACKOFF_SECONDS` | `10` | 第一次重啟前等待的秒數，之後每次加倍（上限 300 秒） |
| `ENGINE_RESTART_RESET_SECONDS` | `600` | 連續運行這麼久才崩潰的模型，重啟次數重新計算 |

超過上限後 launcher 放棄該模型並記錄錯誤（`logs/cluster.log` 或 `logs/main.log`）。單模型模式，
或 cluster 的所有模型都已放棄時，launcher 會以非 0 狀態結束；排除原因後重新執行啟動腳本。

從 Hugging Face 下載模型時請固定 revision，避免上游檔案更新造成不可重現的部署，例如：

```bash
./.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8', revision='8bc5eece2eb5514c4bca7f2ec655b91eb554f4c0', local_dir='AImodels/NVIDIA-Nemotron-Nano-9B-v2-FP8', max_workers=4)"
```

## LiteLLM 設定與 gateway

在 repo 根目錄執行 `bash scripts/prepare-ai-stack.sh --init-env` 補齊金鑰（缺 `litellm/.env` 時由範本
建立），再以 `bash scripts/prepare-ai-stack.sh --start` 產生設定、建立 LiteLLM 資料庫、核發 SkyLab
service key 並啟動整個 stack。位址、金鑰、遠端模型與接管既有獨立 gateway 見
[AI API 使用手冊](/zh-TW/guides/ai-api-user-manual)。與本目錄相關的重點：

- LiteLLM 容器經 `host.docker.internal` 連本機 instance，所以 `.env.API` 的 `API_HOST` 須為 `0.0.0.0`
  （以防火牆限制引擎埠）。根目錄 `docker compose up/down` 會管理 LiteLLM，但不管理主機上的 vLLM 程序。
- `tools/generate_litellm_config.py`（`prepare-ai-stack.sh` 也會呼叫它）把 `models.json` 與
  `litellm/config.template.yaml` 產生成 `litellm/config.yaml`（由 Git 忽略）。金鑰以 `os.environ/...`
  reference 寫入，只有遠端模型的 literal `apikeys` 會直接寫進檔案；因此每次寫出都設為 `0600`（只有擁有者
  可讀寫），既有檔案也會就地收緊。請以執行 Docker 的帳號產生：LiteLLM 容器以 root 讀取，rootless Docker
  下就是該帳號。腳本預設的 `--mode integration` 不含資料庫設定；`--mode production` 要求先注入
  `LITELLM_SERVICE_API_KEY`，並加上 `DATABASE_URL` reference。

## Benchmark

```bash
./run_sharegpt_benchmark.sh --download        # 下載 ShareGPT_V3 資料集到 test_datasets/
LITELLM_API_KEY=<service-key> ./run_sharegpt_benchmark.sh -n 100 -c 20 --model qwen3-14b
./run_sharegpt_benchmark.sh --target single -n 100 -c 20   # 直連 .env.interface 的單模型 vLLM
```

壓測目標、金鑰來源、參數與報告見 [ShareGPT 基準測試](/zh-TW/guides/vllm-sharegpt-quickstart)。
`python -m benchmark.async_bench` 則以單一 prompt 直接壓測一個 vLLM instance，設定讀
`vllm-service/.env`（或 `VLLM_SERVICE_ENV_FILE` 指定的檔案）。

## 目錄責任

| 路徑 | 責任 |
| --- | --- |
| `main.py` | CLI 入口，支援 `single` / `cluster` |
| `start_single_model.sh`、`start_multi_model_cluster.sh` | 背景啟動腳本（PID 在 `.runtime/`、輸出在 `logs/`） |
| `model_deployment.py` | local／remote 部署判定與上游路由驗證 |
| `core/engine.py` | 單一 vLLM instance 啟停、health check、日誌 |
| `core/cluster.py` | 多模型 instance 生命週期，含重啟崩潰的模型 |
| `core/supervisor.py` | 兩種模式共用的崩潰重啟策略（次數上限、退避） |
| `config/settings.py` | 共用 vLLM 設定 |
| `config/multi_model.py` | `models.json` 載入與 cluster 資源檢查 |
| `utils/` | 日誌與啟動前健康檢查 |
| `litellm/` | LiteLLM Compose 檔與版控內的路由政策 template |
| `tools/` | LiteLLM 部署工具（`generate_litellm_config.py` 產生設定、`prepare_ai_stack.py` 檢查金鑰邊界並準備／啟動 AI stack）與 SkyLab AI 整合測試（`campus_ai_integration_test.py`） |
| `benchmark/`、`run_sharegpt_benchmark.py`、`run_sharegpt_benchmark.sh` | async／ShareGPT benchmark（ShareGPT 預設經 LiteLLM） |
| `tests/` | pytest 測試，不需要 GPU 或模型服務 |

## 舊目錄

`vllm-API/` 與 `vllm-inference/`（早期的多模型 Gateway 與單模型部署）不在 repo 內，並由 `.gitignore`
忽略；只可能以未追蹤的本機副本留在既有部署主機上供遷移對照。所有維護一律在 `vllm-service/` 進行。
