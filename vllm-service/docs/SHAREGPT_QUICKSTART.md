# ShareGPT Benchmark Quick Reference

> **English** | [繁體中文](./SHAREGPT_QUICKSTART.zh-TW.md)

All commands below are run from the `vllm-service/` directory.

## 🎯 Benchmark targets

| Target | Flag | Address | Key |
|------|------|----------|------|
| LiteLLM (default) | `--target litellm` | `LITELLM_BASE_URL`, default `http://127.0.0.1:4000/v1` | `LITELLM_API_KEY` or `AI_API_API_KEY` environment variable; otherwise `AI_API_API_KEY` from the repo root `.env` |
| Single-model vLLM | `--target single` | `API_HOST` / `API_PORT` from `.env.interface` | `API_KEY` from `.env.interface` |

The old `--target gateway` flag still works and is equivalent to `litellm`. When `--model` is not given, the LiteLLM
target uses the first model from `/v1/models` (falling back to the alias in `models.json` if that cannot be fetched);
the single-model target sends `SERVED_MODEL_NAME` (or the model path when it is not set).

## 🚀 Quick start (3 steps)

```bash
# 1. Download the ShareGPT dataset (stored in test_datasets/, ignored by Git)
./run_sharegpt_benchmark.sh --download

# 2. Quick test (100 samples, through LiteLLM)
export LITELLM_API_KEY=<service-key>
./run_sharegpt_benchmark.sh -n 100 -c 20 --model qwen3-14b

# 3. Inspect the results
ls -lh benchmark_results/sharegpt_bench_*.json
```

## 📝 Common commands

### Basic tests

```bash
# Smoke check (10 samples)
./run_sharegpt_benchmark.sh -n 10 -c 2

# Quick test (100 samples)
./run_sharegpt_benchmark.sh -n 100 -c 20

# Standard test (1000 samples)
./run_sharegpt_benchmark.sh -n 1000 -c 50

# Large-scale test (5000 samples)
./run_sharegpt_benchmark.sh -n 5000 -c 100

# Hit the single-model vLLM main service directly
./run_sharegpt_benchmark.sh --target single -n 100 -c 20
```

### Calling Python directly

```bash
# Basic usage (through LiteLLM by default)
python3 run_sharegpt_benchmark.py test_datasets/ShareGPT_V3_unfiltered_cleaned_split.json -n 100 -c 20

# Full set of options
python3 run_sharegpt_benchmark.py test_datasets/ShareGPT_V3_unfiltered_cleaned_split.json \
    --target litellm \
    --model qwen3-14b \
    -n 1000 \
    -c 50 \
    -m 512 \
    -t 0.7 \
    --seed 42
```

### Custom configuration

```bash
# Adjust the temperature
./run_sharegpt_benchmark.sh -n 500 -c 30 -t 0.0  # deterministic output
./run_sharegpt_benchmark.sh -n 500 -c 30 -t 1.0  # more creative

# Adjust the maximum token count
./run_sharegpt_benchmark.sh -n 500 -c 30 -m 1024

# Use a custom dataset
./run_sharegpt_benchmark.sh -d /path/to/custom_dataset.json -n 500 -c 30
```

## 📊 Output metrics

### Throughput
- **Requests/sec** (req/s) - requests processed per second
- **Tokens/sec** (tok/s) - total token throughput
- **Output tokens/sec** - generation speed

### Latency
- **End-to-End** - full request/response time
  - mean, min, max, P50, P90, P95, P99
- **TTFT** (Time To First Token) - first-token latency
  - mean, min, max, P50, P90, P99
- **TPOT** (Time Per Output Token) - average time per token
  - mean, P50, P90, P99

### Token statistics
- Total prompt tokens
- Total completion tokens
- Average input/output length

## 📁 File layout

```
vllm-service/
├── benchmark/
│   ├── _common.py               # shared helpers: streaming timers, percentiles, etc.
│   ├── sharegpt_dataset.py      # ShareGPT dataset parsing and download
│   ├── sharegpt_bench.py        # ShareGPT benchmark (LiteLLM / single-model)
│   └── async_bench.py           # single-prompt async stress test (direct to vLLM)
├── run_sharegpt_benchmark.py    # Python entry point
├── run_sharegpt_benchmark.sh    # shell script
├── test_datasets/
│   └── ShareGPT_V3_*.json       # dataset (created by --download)
└── benchmark_results/
    └── sharegpt_bench_*.json    # test reports
```

## 🔍 Suggested test scenarios

| Scenario | Samples | Concurrency | Expected time | Command |
|------|--------|--------|----------|------|
| Smoke check | 10-50 | 5-10 | 30 s - 1 min | `-n 50 -c 10` |
| Development test | 100 | 20 | 2-5 min | `-n 100 -c 20` |
| Standard test | 1000 | 50 | 10-20 min | `-n 1000 -c 50` |
| Stress test | 5000 | 100 | 30-60 min | `-n 5000 -c 100` |
| Stability test | 10000 | 50 | 1-2 h | `-n 10000 -c 50` |

## 🐛 Troubleshooting

### Dataset not found
```bash
# Download manually
./run_sharegpt_benchmark.sh --download

# Or let Python download it automatically (on first run, a missing dataset path triggers the download)
python3 run_sharegpt_benchmark.py test_datasets/ShareGPT_V3_unfiltered_cleaned_split.json -n 10 -c 2
```

### API connection or authentication errors
```bash
# LiteLLM: verify the service and the key
curl -H "Authorization: Bearer $LITELLM_API_KEY" http://127.0.0.1:4000/v1/models

# Single model: verify the address and key in .env.interface
grep -E "API_HOST|API_PORT|API_KEY" .env.interface
```

### Concurrency too high
```bash
# Lower the concurrency
./run_sharegpt_benchmark.sh -n 1000 -c 20  # down from 50 to 20
```

### Test code
```bash
# Unit tests for the benchmark (no GPU or model service required)
python -m pytest tests/test_sharegpt_benchmark.py
```

## 📚 Related resources

- **vllm-service README**: [README.md](../README.md)
- **AI API User Manual**: [ai-api-user-manual.md](../../docs/ai-api-user-manual.md)
- **Dataset source**: https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered

## 💡 Best practices

1. ✅ Start with a small run (`-n 10 -c 2`) to validate the setup
2. ✅ Increase the load gradually and watch how the system responds
3. ✅ Use a fixed seed (`--seed 42`) for reproducibility
4. ✅ Keep the test reports (the default behaviour)
5. ✅ Monitor system resources (GPU, CPU, memory)
6. ✅ Try different temperatures (0.0, 0.7, 1.0)

## 🎯 Sample output

The report is printed in Traditional Chinese by the benchmark script; the labels are, in order: time, model, dataset,
test configuration (samples, total tests, successful, failed, concurrency, total elapsed), throughput (req/s, total tok/s,
output tok/s), end-to-end latency, TTFT and TPOT (mean / P50 / P90 / P99).

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
