# ShareGPT Benchmark 快速參考

## 🚀 快速開始（3 步驟）

```bash
# 1. 下載 ShareGPT 數據集
./run_sharegpt_benchmark.sh --download

# 2. 快速測試（100 個樣本）
./run_sharegpt_benchmark.sh -n 100 -c 20

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
```

### Python 直接調用

```bash
# 基本用法
python3 run_sharegpt_benchmark.py ShareGPT_V3_unfiltered_cleaned_split.json -n 100 -c 20

# 完整參數
python3 run_sharegpt_benchmark.py ShareGPT_V3_unfiltered_cleaned_split.json \
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
vllm_single/
├── benchmark/
│   ├── sharegpt_dataset.py      # ShareGPT 數據集解析
│   └── sharegpt_bench.py        # ShareGPT Benchmark 測試
├── run_sharegpt_benchmark.py    # Python 入口
├── run_sharegpt_benchmark.sh    # Shell 腳本
├── test_sharegpt_setup.py       # 測試驗證腳本
├── ShareGPT_V3_*.json           # 數據集（首次運行後下載）
├── benchmark_results/
│   └── sharegpt_bench_*.json    # 測試報告
└── docs/
    └── SHAREGPT_BENCHMARK_GUIDE.md  # 完整文檔
```

## 🔍 測試場景建議

| 場景 | 樣本數 | 併發數 | 預計時間 | 命令 |
|------|--------|--------|----------|------|
| 快速驗證 | 10-50 | 5-10 | 30秒-1分鐘 | `-n 50 -c 10` |
| 開發測試 | 100 | 20 | 2-5分鐘 | `-n 100 -c 20` |
| 標準測試 | 1000 | 50 | 10-20分鐘 | `-n 1000 -c 50` |
| 壓力測試 | 5000 | 100 | 30-60分鐘 | `-n 5000 -c 100` |
| 穩定性測試 | 10000 | 50 | 1-2小時 | `-n 10000 -c 50` |

## 🆚 與其他測試方法對比

| 特性 | benchmark_current.sh | enhanced_bench.py | **sharegpt_bench.py** |
|------|---------------------|-------------------|---------------------|
| 數據集 | ShareGPT (vllm CLI) | 自定義 JSON | **ShareGPT** |
| 調用方式 | vllm bench 命令 | Python API | **Python API** |
| 併發控制 | vllm 內建 | asyncio | **asyncio** |
| TPOT 指標 | ✅ | ❌ | **✅** |
| 彈性採樣 | ❌ | ❌ | **✅** |
| 可複現性 | 部分 | ✅ | **✅** |
| 數據格式 | ShareGPT | 自定義 | **ShareGPT** |

## 🐛 常見問題

### 數據集未找到
```bash
# 手動下載
./run_sharegpt_benchmark.sh --download

# 或使用 Python 自動下載（首次運行時）
python3 run_sharegpt_benchmark.py ShareGPT_V3_unfiltered_cleaned_split.json -n 10 -c 2
```

### API 連接錯誤
```bash
# 檢查服務是否運行
curl http://localhost:8000/v1/models

# 檢查 .env 配置
cat .env | grep -E "API_HOST|API_PORT"
```

### 併發數過高
```bash
# 降低併發數
./run_sharegpt_benchmark.sh -n 1000 -c 20  # 從 50 降到 20
```

### 測試代碼
```bash
# 運行驗證測試
source .venv/bin/activate
python3 test_sharegpt_setup.py
```

## 📚 相關資源

- **完整文檔**: [docs/SHAREGPT_BENCHMARK_GUIDE.md](docs/SHAREGPT_BENCHMARK_GUIDE.md)
- **通用 Benchmark**: [docs/BENCHMARK_GUIDE.md](docs/BENCHMARK_GUIDE.md)
- **主 README**: [README.md](README.md)
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
  模型:          nvidia/Qwen3-235B-A22B-NVFP4
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

---

**需要幫助？** 查看完整文檔：`cat docs/SHAREGPT_BENCHMARK_GUIDE.md`
