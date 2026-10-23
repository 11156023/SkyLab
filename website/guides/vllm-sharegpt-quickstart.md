# ShareGPT Benchmark Quick Reference

All commands below are run from the `vllm-service/` directory. Running `./run_sharegpt_benchmark.sh`
without arguments starts an interactive flow that asks for the target, the model, the number of samples
and the concurrency.

## Benchmark targets

| Target | Flag | Address | Key |
|------|------|----------|------|
| LiteLLM (default) | `--target litellm` | `LITELLM_BASE_URL`, default `http://127.0.0.1:4000/v1` | `LITELLM_API_KEY` or `AI_API_API_KEY` environment variable; otherwise `AI_API_API_KEY` from the repo root `.env` |
| Single-model vLLM | `--target single` | `API_HOST` / `API_PORT` from `.env.interface` | `API_KEY` from `.env.interface` |

`--target gateway` is an old name for `litellm`. When `--model` is not given, the LiteLLM target uses the
first model ID (in alphabetical order) from `/v1/models`, falling back to the aliases in `models.json` if
the list cannot be fetched; the single-model target sends `SERVED_MODEL_NAME` (or the model path when it
is not set).

## Quick start

```bash
# 1. Download the ShareGPT dataset (stored in test_datasets/, ignored by Git)
./run_sharegpt_benchmark.sh --download

# 2. Quick test (100 samples, through LiteLLM)
export LITELLM_API_KEY=<service-key>
./run_sharegpt_benchmark.sh -n 100 -c 20 --model qwen3-14b

# 3. Inspect the results
ls -lh benchmark_results/sharegpt_bench_*.json
```

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `-n`, `--num-samples` | The whole dataset | Number of conversations to sample; always set it for a quick run |
| `-c`, `--concurrency` | `BENCH_CONCURRENCY` from the target's env file (`.env.API` or `.env.interface`), otherwise 64 | Concurrent requests |
| `-m`, `--max-tokens` | `BENCH_MAX_TOKENS` from the same file, otherwise 2048 | Maximum generated tokens per request |
| `-t`, `--temperature` | `0.7` | Sampling temperature; `0.0` gives deterministic output |
| `--seed` | `42` | Sampling seed, for reproducible runs |
| `-d`, `--dataset` | `test_datasets/ShareGPT_V3_unfiltered_cleaned_split.json` | Path to another ShareGPT-format dataset |
| `--target`, `--model`, `--base-url` | `litellm`, see above | Target service, model alias, override of the OpenAI base URL |
| `--no-save` | Report is saved | Do not write the JSON report |

The Python entry point takes the dataset path as its first argument and the same options:

```bash
python3 run_sharegpt_benchmark.py test_datasets/ShareGPT_V3_unfiltered_cleaned_split.json \
    --target litellm --model qwen3-14b -n 1000 -c 50 -m 512 -t 0.7 --seed 42
```

## Suggested test scenarios

Start small to validate the setup, then raise the load step by step while watching the GPU, CPU and
memory of the inference host.

| Scenario | Samples | Concurrency | Expected time | Command |
|------|--------|--------|----------|------|
| Smoke check | 10-50 | 5-10 | 30 s - 1 min | `-n 50 -c 10` |
| Development test | 100 | 20 | 2-5 min | `-n 100 -c 20` |
| Standard test | 1000 | 50 | 10-20 min | `-n 1000 -c 50` |
| Stress test | 5000 | 100 | 30-60 min | `-n 5000 -c 100` |
| Stability test | 10000 | 50 | 1-2 h | `-n 10000 -c 50` |

## Output metrics

- **Throughput**: requests per second (req/s), total, input and output tokens per second.
- **End-to-end latency**: full request/response time; mean, min, max, P50, P90, P95, P99.
- **TTFT** (Time To First Token): first-token latency; mean, min, max, P50, P90, P99.
- **TPOT** (Time Per Output Token): average time per generated token; mean, P50, P90, P99.
- **Token statistics**: total prompt, completion and overall tokens; average input and output length.

The report is printed in Chinese by the script. Its sections are, in order: time, model and dataset;
test configuration (samples, total tests, successful, failed, concurrency, total elapsed); token
statistics; throughput; end-to-end latency; TTFT; TPOT. Example (the values are illustrative):

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

The JSON report is written to `benchmark_results/sharegpt_bench_<timestamp>.json` (ignored by Git).

## Troubleshooting

### Dataset not found
```bash
# Download it (the shell script stops if the dataset is missing)
./run_sharegpt_benchmark.sh --download

# The Python entry point downloads a missing dataset by itself
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
Lower the concurrency, for example from `-c 50` to `-c 20`.

### Benchmark unit tests
```bash
# No GPU or model service required
python -m pytest tests/test_sharegpt_benchmark.py
```

## Related

- [vLLM service](/guides/vllm-service)
- [AI API User Manual](/guides/ai-api-user-manual)
- Dataset source: https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered
