# vLLM Service Project Overview

`vllm-service/` 是 SkyLab 的 canonical vLLM 推論服務。它只提供服務端能力：

- 單模型 OpenAI-compatible vLLM server
- 多模型 vLLM cluster（各模型獨立 instance）
- LiteLLM gateway 設定（多模型對外 API、金鑰與路由）
- Benchmark 工具

本服務不提供 React/Vite 前端；互動介面由 SkyLab 主 frontend 或外部
OpenAI-compatible client 負責。早期自寫的 FastAPI Gateway 已移除，多模型 API
一律經 LiteLLM。

## 服務模式

| 模式 | 指令 | 用途 |
| --- | --- | --- |
| Single | `python main.py single`（`start_single_model.sh`） | 啟動單一 vLLM instance，供內部 AI 直接呼叫 |
| Cluster | `python main.py cluster`（預設模式，`start_multi_model_cluster.sh`） | 依 `models.json` 啟動多個本機 vLLM instance，路由與對外 API 交給 LiteLLM |

`python main.py gateway` 已不再支援，執行時會直接報錯並提示改用 cluster＋LiteLLM。
舊指令帶的 `--no-gateway`、`--gateway-ready-timeout` 仍會被接受但不影響行為。

## 主要端點

| 服務 | 預設位址 | 說明 |
| --- | --- | --- |
| 單模型 vLLM | `http://<API_HOST>:<API_PORT>/v1` | `.env.interface` |
| cluster 各 instance | `http://127.0.0.1:<api_port>/v1` | `models.json` 的 `api_port`，只給 LiteLLM 連 |
| LiteLLM gateway | `http://127.0.0.1:4000/v1`（Compose 內網 `http://litellm:4000`） | `GET /v1/models`、`POST /v1/chat/completions` 等 OpenAI 相容端點 |

## 設定邊界

主 Campus-Cloud backend 使用兩條不同設定：

```env
VLLM_BASE_URL=http://localhost:8000/v1
AI_API_BASE_URL=http://litellm:4000
```

- `VLLM_BASE_URL` 指向單模型主服務，且包含 `/v1`。
- `AI_API_BASE_URL` 指向 LiteLLM gateway root，不包含 `/v1`；金鑰為受限的 service key `AI_API_API_KEY`。
- 多模型公開 alias、per-model port 與遠端模型由 `models.json` 管理，
  `tools/generate_litellm_config.py` 依它產生 `litellm/config.yaml`。

## 目錄

```text
vllm-service/
├── main.py                     # 啟動器：single / cluster
├── start_single_model.sh
├── start_multi_model_cluster.sh
├── model_deployment.py         # local / remote 部署判定與上游連線
├── config/                     # Settings 與 models.json 載入
├── core/                       # vLLM instance 與 cluster 生命週期
├── utils/                      # 日誌、啟動前健康檢查
├── litellm/                    # LiteLLM Compose 與 config template
├── tools/                      # LiteLLM 設定產生／部署工具、backend AI 整合測試
├── benchmark/                  # async / ShareGPT benchmark
├── run_sharegpt_benchmark.py
└── run_sharegpt_benchmark.sh
```

## 相關文件

- [README.md](../README.md)：安裝、啟動與 LiteLLM 部署流程
- [SHAREGPT_QUICKSTART.md](SHAREGPT_QUICKSTART.md)：ShareGPT benchmark 用法
- [AI API 使用手冊](../../docs/ai-api-user-manual.md)：LiteLLM 部署、金鑰與使用者 API 操作
