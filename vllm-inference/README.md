# vLLM Inference

基於官方 vLLM 的高併發單模型推論部署，採乾淨分層架構（config / core / api / benchmark / utils / tools / webapp）。內建 vLLM 伺服器啟停管理、OpenAI 相容客戶端、benchmark 套件，以及一個 React + FastAPI 的多模態 Web UI（位於 [`webapp/`](webapp/README.md)）。

## 架構

```
vllm-inference/
├── config/                  # Pydantic Settings（.env > 預設值）
│   └── settings.py
├── core/                    # vLLM 引擎啟停與健康檢查
│   └── engine.py
├── api/                     # OpenAI 相容客戶端（同步 / 異步 / 串流）
│   └── client.py
├── benchmark/               # 壓力測試套件
│   ├── async_bench.py            # 通用 async benchmark
│   ├── enhanced_bench.py         # 自訂 JSON 資料集
│   ├── sharegpt_bench.py         # ShareGPT 資料集
│   ├── dataset.py
│   └── sharegpt_dataset.py
├── utils/                   # GPU / 系統健康、log、模型偵測
├── tools/                   # call_model.py、call_vision_model.py、call_video_model.py
├── webapp/                  # React + FastAPI 多模態 Web UI（見 webapp/README.md）
├── main.py                  # 主入口：pre-flight check + 啟動 vLLM
├── run_benchmark.py         # async benchmark 入口
├── run_sharegpt_benchmark.py
├── run_sharegpt_benchmark.sh
├── start_webapp.sh
└── requirements.txt
```

## 設計原則

| 原則 | 實現 |
| --- | --- |
| 參數優先級 | `.env` 環境變數 > `config/settings.py` 預設值 |
| 關注點分離 | config / core / api / benchmark 層各自獨立 |
| 高併發 | vLLM 原生 continuous batching + async API |
| 可觀測 | Benchmark 報告含延遲分位數、TTFT、TPOT、吞吐量 |
| Pre-flight | `main.py` 啟動前檢查 GPU、CUDA、模型可用性 |

## 快速開始

### 1. 安裝

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 設定 `.env`

```env
MODEL_NAME=nvidia/Qwen3-235B-A22B-NVFP4
HF_CACHE_DIR=/raid/hf-cache/hub
API_HOST=0.0.0.0
API_PORT=8000
API_KEY=vllm-secret-key-change-me
DTYPE=auto
MAX_MODEL_LEN=4096
GPU_MEMORY_UTILIZATION=0.95
MAX_NUM_SEQS=64
TENSOR_PARALLEL_SIZE=1
ENABLE_PREFIX_CACHING=true
ENFORCE_EAGER=false
```

### 3. 啟動伺服器

```bash
python main.py                 # 完整 pre-flight + 啟動
python main.py --no-wait       # 不等待 health 直接啟動
python main.py --skip-check    # 跳過 pre-flight 檢查
```

### 4. Linux 背景啟動 / 停止

```bash
chmod +x start_main_bg.sh stop_main_bg.sh

./start_main_bg.sh
tail -f main.log

./stop_main_bg.sh
```

- 啟動後會在專案根目錄建立 `.main.pid`（記錄背景程序 PID）
- 日誌輸出至 `main.log`

### 5. 呼叫模型

```bash
python tools/call_model.py
python tools/call_vision_model.py
python tools/call_video_model.py
```

或在程式中使用 `api.client.ModelClient`：

```python
from api.client import ModelClient, quick_chat

answer = quick_chat("什麼是機器學習？")

client = ModelClient()
response = client.chat_simple("介紹 vLLM", max_tokens=256)

for chunk in client.chat_stream("什麼是 LLM？"):
    print(chunk, end="")

import asyncio
answer = asyncio.run(client.achat_simple("解釋 GPU 推論"))
```

或直接 cURL：

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer vllm-secret-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nvidia/Qwen3-235B-A22B-NVFP4",
    "messages": [{"role": "user", "content": "你好"}],
    "max_tokens": 256
  }'
```

### 6. 執行 Benchmark

#### 簡易 async benchmark

```bash
python run_benchmark.py                       # 使用 .env 預設值
python run_benchmark.py -n 100 -c 20 -t 512    # 100 req / 20 concurrency / 512 tokens
python run_benchmark.py --requests 200 --concurrency 30 --max-tokens 256 \
                       --prompt "請介紹深度學習"
```

#### 自訂 JSON 資料集（enhanced）

```bash
python run_enhanced_benchmark.py test_datasets/chinese_qa_standard.json -c 20
python run_enhanced_benchmark.py test_datasets/chinese_qa_standard.json --category 技術問答
```

#### ShareGPT（推薦）

```bash
./run_sharegpt_benchmark.sh --download
./run_sharegpt_benchmark.sh -n 100 -c 20      # 快速
./run_sharegpt_benchmark.sh -n 1000 -c 50     # 標準
./run_sharegpt_benchmark.sh -n 5000 -c 100    # 大規模壓測

python run_sharegpt_benchmark.py ShareGPT_V3_unfiltered_cleaned_split.json \
    -n 1000 -c 50 -m 512 -t 0.7
```

Benchmark 結果會輸出於 `benchmark_results/`，包含：

- 請求吞吐量（req/s）
- Token 吞吐量（tok/s）
- 端到端延遲（mean / P50 / P90 / P95 / P99）
- TTFT（Time To First Token）
- TPOT（Time Per Output Token）
- Token 長度統計

## SkyLab 主架構整合測試

可用以下腳本直接驗證「主架構」AI 路由（非子專案路由整合測試）：

- AI 評分助手：`/api/v1/rubric/upload`
- AI 模板推薦：`/api/v1/ai/template-recommendation/chat`
- AI-PVE：`/api/v1/ai/pve-log/chat`

腳本位置：`tools/campus_ai_integration_test.py`

### 執行方式

```bash
python tools/campus_ai_integration_test.py \
  --base-url http://localhost:8000 \
  --username teacher@example.com \
  --password your-password \
  --rubric-file "專題 AI 實戰評分測試表.docx" \
  --template-prompt "我想建立python環境" \
  --pve-prompt "請幫我看節點狀態" \
  --strict \
  --report-file campus-ai-test-report.json
```

### 參數重點

- `--skip-rubric`：略過評分表上傳情境
- `--insecure`：停用 TLS 憑證驗證（測試環境可用）
- `--strict`：任一案例失敗時回傳非 0 exit code
- `--report-file`：輸出完整 JSON 報告

也可用環境變數帶入：

- `CAMPUS_BACKEND_BASE_URL`
- `CAMPUS_BACKEND_API_V1`
- `CAMPUS_BACKEND_USERNAME`
- `CAMPUS_BACKEND_PASSWORD`
- `CAMPUS_RUBRIC_FILE`
- `CAMPUS_TEMPLATE_PROMPT`
- `CAMPUS_PVE_PROMPT`
- `CAMPUS_TEST_TIMEOUT`

## `.env` 主要參數

| 變數 | 說明 | 預設值 |
| --- | --- | --- |
| `MODEL_NAME` | 模型名稱或本地路徑 | `nvidia/Qwen3-235B-A22B-NVFP4` |
| `HF_CACHE_DIR` | HuggingFace 快取目錄 | `/raid/hf-cache/hub` |
| `API_HOST` / `API_PORT` | 監聽地址 / 埠 | `0.0.0.0` / `8000` |
| `API_KEY` | API 金鑰 | `vllm-secret-key-change-me` |
| `DTYPE` | 運算精度 | `auto` |
| `MAX_MODEL_LEN` | 最大上下文長度 | `4096` |
| `GPU_MEMORY_UTILIZATION` | GPU 記憶體利用率 | `0.95` |
| `MAX_NUM_SEQS` | 最大併發序列 | `64` |
| `MAX_NUM_BATCHED_TOKENS` | 單批最大 token 數 | — |
| `TENSOR_PARALLEL_SIZE` | TP 並行度 | `1` |
| `ENFORCE_EAGER` | 強制 eager 模式 | `false` |
| `ENABLE_PREFIX_CACHING` | 前綴快取 | `true` |
| `BENCH_TOTAL_REQUESTS` | benchmark 總請求 | `50` |
| `BENCH_CONCURRENCY` | benchmark 併發 | `10` |
| `BENCH_MAX_TOKENS` | benchmark 最大 token | `256` |

## Web UI

`webapp/` 提供 React + FastAPI 的多模態前端，支援文字 / 圖片 / 影片 / 文件、串流輸出等。詳見 [`webapp/README.md`](webapp/README.md)。

## 與 `vllm-API` 的差別

| | `vllm-inference` | `vllm-API` |
| --- | --- | --- |
| 範疇 | 單一模型部署 | 多模型 + 統一 Gateway |
| 設定 | `.env`（單一 `MODEL_NAME`） | `.env` + `models.json`（每模型獨立） |
| Port | 單一 API port（8000） | 多個模型 port + Gateway port |
| 適用 | MVP / 單模型實驗 | 校園叢集 + 成本最佳化 |
