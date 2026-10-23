# ShareGPT Benchmark 快速參考

以下指令都在 `vllm-service/` 目錄執行。不帶參數執行 `./run_sharegpt_benchmark.sh` 會進入互動式流程，
依序詢問壓測目標、模型、樣本數與併發數。

## 壓測目標

| 目標 | 參數 | 連線位址 | 金鑰 |
|------|------|----------|------|
| LiteLLM（預設） | `--target litellm` | `LITELLM_BASE_URL`，預設 `http://127.0.0.1:4000/v1` | `LITELLM_API_KEY` 或 `AI_API_API_KEY` 環境變數，否則讀 repo 根目錄 `.env` 的 `AI_API_API_KEY` |
| 單模型 vLLM | `--target single` | `.env.interface` 的 `API_HOST`／`API_PORT` | `.env.interface` 的 `API_KEY` |

`--target gateway` 是 `litellm` 的舊名稱。未指定 `--model` 時，LiteLLM 目標會取 `/v1/models` 依字母
排序後的第一個模型 ID（取不到清單時改讀 `models.json` 的 alias）；單模型目標送 `SERVED_MODEL_NAME`
（未設定時送模型路徑）。

## 快速開始

```bash
# 1. 下載 ShareGPT 資料集（存到 test_datasets/，已由 Git 忽略）
./run_sharegpt_benchmark.sh --download

# 2. 快速測試（100 個樣本，經 LiteLLM）
export LITELLM_API_KEY=<service-key>
./run_sharegpt_benchmark.sh -n 100 -c 20 --model qwen3-14b

# 3. 查看結果
ls -lh benchmark_results/sharegpt_bench_*.json
```

## 參數

| 參數 | 預設 | 說明 |
| --- | --- | --- |
| `-n`、`--num-samples` | 整個資料集 | 取樣的對話數；快速測試務必指定 |
| `-c`、`--concurrency` | 目標設定檔（`.env.API` 或 `.env.interface`）的 `BENCH_CONCURRENCY`，未設定為 64 | 併發請求數 |
| `-m`、`--max-tokens` | 同一設定檔的 `BENCH_MAX_TOKENS`，未設定為 2048 | 每次請求最多生成的 token 數 |
| `-t`、`--temperature` | `0.7` | 取樣溫度；`0.0` 為確定性輸出 |
| `--seed` | `42` | 取樣種子，讓結果可重現 |
| `-d`、`--dataset` | `test_datasets/ShareGPT_V3_unfiltered_cleaned_split.json` | 改用其他 ShareGPT 格式的資料集 |
| `--target`、`--model`、`--base-url` | `litellm`，見上節 | 壓測目標、模型 alias、覆寫 OpenAI base URL |
| `--no-save` | 會儲存報告 | 不寫出 JSON 報告 |

Python 入口的第一個參數是資料集路徑，其餘參數相同：

```bash
python3 run_sharegpt_benchmark.py test_datasets/ShareGPT_V3_unfiltered_cleaned_split.json \
    --target litellm --model qwen3-14b -n 1000 -c 50 -m 512 -t 0.7 --seed 42
```

## 測試情境建議

先用小規模測試驗證設定，再逐步加大負載，同時觀察推論主機的 GPU、CPU 與記憶體。

| 情境 | 樣本數 | 併發數 | 預計時間 | 指令 |
|------|--------|--------|----------|------|
| 快速驗證 | 10-50 | 5-10 | 30 秒-1 分鐘 | `-n 50 -c 10` |
| 開發測試 | 100 | 20 | 2-5 分鐘 | `-n 100 -c 20` |
| 標準測試 | 1000 | 50 | 10-20 分鐘 | `-n 1000 -c 50` |
| 壓力測試 | 5000 | 100 | 30-60 分鐘 | `-n 5000 -c 100` |
| 穩定性測試 | 10000 | 50 | 1-2 小時 | `-n 10000 -c 50` |

## 輸出指標

- **吞吐量**：每秒請求數（req/s），以及每秒總 token、輸入 token、輸出 token 數。
- **端到端延遲**：完整請求到回應的時間；平均、最小、最大、P50、P90、P95、P99。
- **TTFT**（Time To First Token）：第一個 token 的延遲；平均、最小、最大、P50、P90、P99。
- **TPOT**（Time Per Output Token）：每個生成 token 的平均時間；平均、P50、P90、P99。
- **Token 統計**：prompt、completion 與總 token 數，平均輸入與輸出長度。

報告範例（數值僅為示意）：

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
  ▸ Token 統計
    Prompt Token:      125,441
    Completion Token:  89,305
    總 Token:          214,746
    平均輸入長度:      126 tokens
    平均輸出長度:      89 tokens
────────────────────────────────────────────────────────────────────────────────
  ▸ 吞吐量
    請求/秒:           22.02 req/s
    總 Token/秒:       4738.44 tok/s
    輸入 Token/秒:     2767.89 tok/s
    輸出 Token/秒:     1970.54 tok/s
────────────────────────────────────────────────────────────────────────────────
  ▸ 延遲 (End-to-End)
    平均:    2130.5ms
    最小:    412.8ms
    最大:    9876.4ms
    P50:     1987.3ms
    P90:     3456.7ms
    P95:     4321.0ms
    P99:     6789.2ms
────────────────────────────────────────────────────────────────────────────────
  ▸ TTFT (Time To First Token)
    平均:    123.4ms
    最小:    45.2ms
    最大:    812.6ms
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

JSON 報告寫到 `benchmark_results/sharegpt_bench_<時間戳>.json`（已由 Git 忽略）。

## 常見問題

### 找不到資料集
```bash
# 下載資料集（shell 腳本在資料集不存在時會直接停止）
./run_sharegpt_benchmark.sh --download

# Python 入口遇到資料集不存在時會自動下載
python3 run_sharegpt_benchmark.py test_datasets/ShareGPT_V3_unfiltered_cleaned_split.json -n 10 -c 2
```

### API 連線或認證錯誤
```bash
# LiteLLM：確認服務與金鑰
curl -H "Authorization: Bearer $LITELLM_API_KEY" http://127.0.0.1:4000/v1/models

# 單模型：確認 .env.interface 的位址與金鑰
grep -E "API_HOST|API_PORT|API_KEY" .env.interface
```

### 併發數過高
降低併發數，例如從 `-c 50` 降到 `-c 20`。

### Benchmark 單元測試
```bash
# 不需要 GPU 或模型服務
python -m pytest tests/test_sharegpt_benchmark.py
```

## 相關資源

- [vLLM 服務](/zh-TW/guides/vllm-service)
- [AI API 使用手冊](/zh-TW/guides/ai-api-user-manual)
- 資料集來源：https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered
