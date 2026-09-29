# ShareGPT Benchmark 快速參考

以下指令都在 `vllm-service/` 目錄執行。

## 🎯 壓測目標

| 目標 | 參數 | 連線位址 | 金鑰 |
|------|------|----------|------|
| LiteLLM（預設） | `--target litellm` | `LITELLM_BASE_URL`，預設 `http://127.0.0.1:4000/v1` | `LITELLM_API_KEY` 或 `AI_API_API_KEY` 環境變數，否則讀 repo 根目錄 `.env` 的 `AI_API_API_KEY` |
| 單模型 vLLM | `--target single` | `.env.interface` 的 `API_HOST`／`API_PORT` | `.env.interface` 的 `API_KEY` |

舊參數 `--target gateway` 仍可用，等同 `litellm`。未指定 `--model` 時，LiteLLM 目標會取
`/v1/models` 的第一個模型（取不到時改讀 `models.json` 的 alias）；單模型目標送
`SERVED_MODEL_NAME`（未設定時送模型路徑）。

## 🚀 快速開始（3 步驟）

```bash
# 1. 下載 ShareGPT 數據集（存到 test_datasets/，已由 Git 忽略）
./run_sharegpt_benchmark.sh --download

# 2. 快速測試（100 個樣本，經 LiteLLM）
export LITELLM_API_KEY=<service-key>
./run_sharegpt_benchmark.sh -n 100 -c 20 --model qwen3-14b

# 3. 查看結果
ls -lh benchmark_results/sharegpt_bench_*.json
```

## 📝 常用命令

### 基本測試

```bash
# 快速驗證（10 個樣本）
./run_sharegpt_benchmark.sh -n 10 -c 2

# 快速測試（100 個樣本）
./run_sharegpt_benchmark.sh -n 100 -c 20

# 標準測試（1000 個樣本）
./run_sharegpt_benchmark.sh -n 1000 -c 50

# 大規模測試（5000 個樣本）
./run_sharegpt_benchmark.sh -n 5000 -c 100

# 直連單模型 vLLM 主服務
./run_sharegpt_benchmark.sh --target single -n 100 -c 20
```

### Python 直接調用

```bash
# 基本用法（預設經 LiteLLM）
python3 run_sharegpt_benchmark.py test_datasets/ShareGPT_V3_unfiltered_cleaned_split.json -n 100 -c 20

# 完整參數
python3 run_sharegpt_benchmark.py test_datasets/ShareGPT_V3_unfiltered_cleaned_split.json \
    --target litellm \
    --model qwen3-14b \
    -n 1000 \
    -c 50 \
    -m 512 \
    -t 0.7 \
    --seed 42
```

### 自定義配置

```bash
# 調整溫度參數
./run_sharegpt_benchmark.sh -n 500 -c 30 -t 0.0  # 確定性輸出
./run_sharegpt_benchmark.sh -n 500 -c 30 -t 1.0  # 更多創意

# 調整最大 Token 數
./run_sharegpt_benchmark.sh -n 500 -c 30 -m 1024

# 使用自定義數據集
./run_sharegpt_benchmark.sh -d /path/to/custom_dataset.json -n 500 -c 30
```

## 📊 輸出指標

### 吞吐量
- **請求/秒** (req/s) - 每秒處理的請求數
- **Token/秒** (tok/s) - 總 token 吞吐量
- **輸出 Token/秒** - 生成速度

### 延遲
- **End-to-End** - 完整請求響應時間
  - 平均、最小、最大、P50、P90、P95、P99
- **TTFT** (Time To First Token) - 首 token 延遲
  - 平均、最小、最大、P50、P90、P99
- **TPOT** (Time Per Output Token) - 每 token 平均時間
  - 平均、P50、P90、P99

### Token 統計
- Prompt Token 總數
- Completion Token 總數
- 平均輸入/輸出長度

## 📁 文件結構

```
vllm-service/
├── benchmark/
│   ├── _common.py               # 串流計時、百分位數等共用函式
│   ├── sharegpt_dataset.py      # ShareGPT 數據集解析與下載
│   ├── sharegpt_bench.py        # ShareGPT Benchmark（LiteLLM／單模型）
│   └── async_bench.py           # 單一 prompt 的異步壓測（直連 vLLM）
├── run_sharegpt_benchmark.py    # Python 入口
├── run_sharegpt_benchmark.sh    # Shell 腳本
├── test_datasets/
│   └── ShareGPT_V3_*.json       # 數據集（--download 後產生）
└── benchmark_results/
    └── sharegpt_bench_*.json    # 測試報告
```

## 🔍 測試場景建議

| 場景 | 樣本數 | 併發數 | 預計時間 | 命令 |
|------|--------|--------|----------|------|
| 快速驗證 | 10-50 | 5-10 | 30秒-1分鐘 | `-n 50 -c 10` |
| 開發測試 | 100 | 20 | 2-5分鐘 | `-n 100 -c 20` |
| 標準測試 | 1000 | 50 | 10-20分鐘 | `-n 1000 -c 50` |
| 壓力測試 | 5000 | 100 | 30-60分鐘 | `-n 5000 -c 100` |
| 穩定性測試 | 10000 | 50 | 1-2小時 | `-n 10000 -c 50` |

## 🐛 常見問題

### 數據集未找到
```bash
# 手動下載
./run_sharegpt_benchmark.sh --download

# 或使用 Python 自動下載（首次運行時，資料集路徑不存在會自動下載）
python3 run_sharegpt_benchmark.py test_datasets/ShareGPT_V3_unfiltered_cleaned_split.json -n 10 -c 2
```

### API 連接或認證錯誤
```bash
# LiteLLM：確認服務與金鑰
curl -H "Authorization: Bearer $LITELLM_API_KEY" http://127.0.0.1:4000/v1/models

# 單模型：確認 .env.interface 的位址與金鑰
grep -E "API_HOST|API_PORT|API_KEY" .env.interface
```

### 併發數過高
```bash
# 降低併發數
./run_sharegpt_benchmark.sh -n 1000 -c 20  # 從 50 降到 20
```

### 測試代碼
```bash
# benchmark 的單元測試（不需要 GPU 或模型服務）
python -m pytest tests/test_sharegpt_benchmark.py
```

## 📚 相關資源

- **vllm-service README**: [README.md](../README.md)
- **AI API 使用手冊**: [ai-api-user-manual.md](../../docs/ai-api-user-manual.md)
- **數據集來源**: https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered

## 💡 最佳實踐

1. ✅ 先進行小規模測試（`-n 10 -c 2`）驗證設定
2. ✅ 逐步增加負載，觀察系統響應
3. ✅ 使用固定種子（`--seed 42`）確保可複現
4. ✅ 保存測試報告（默認行為）
5. ✅ 監控系統資源（GPU、CPU、內存）
6. ✅ 測試不同溫度參數（0.0、0.7、1.0）

## 🎯 示例輸出

```
================================================================================
  🚀 ShareGPT vLLM Benchmark 報告
================================================================================
  時間:          2026-02-15T12:00:00
  模型:          qwen3-14b
  數據集:        ShareGPT (ShareGPT_V3_unfiltered_cleaned_split.json)
────────────────────────────────────────────────────────────────────────────────
  測試配置:
    樣本數:        1000
    總測試數:      1000
    成功測試:      998
    失敗測試:      2
    併發數:        50
    總耗時:        45.32s
────────────────────────────────────────────────────────────────────────────────
  ▸ 吞吐量
    請求/秒:           22.02 req/s
    總 Token/秒:       4,738.45 tok/s
    輸出 Token/秒:     1,970.56 tok/s
────────────────────────────────────────────────────────────────────────────────
  ▸ 延遲 (End-to-End)
    平均:    2,130.5ms
    P50:     1,987.3ms
    P90:     3,456.7ms
    P99:     6,789.2ms
────────────────────────────────────────────────────────────────────────────────
  ▸ TTFT (Time To First Token)
    平均:    123.4ms
    P50:     115.6ms
    P90:     189.3ms
    P99:     345.6ms
────────────────────────────────────────────────────────────────────────────────
  ▸ TPOT (Time Per Output Token)
    平均:    22.456ms/token
    P50:     21.234ms/token
    P90:     31.567ms/token
    P99:     45.678ms/token
================================================================================
```
