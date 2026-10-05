# SkyLab 文件

> [English](./README.md) | **繁體中文**

英文是文件的主要語言。每份指南都有 `.zh-TW.md` 結尾的繁體中文版，從各檔案頂端的語言切換列互連。`archive/` 下是特定時間點的報告，只保留原文語言。

## 入門

| 文件 | 內容 |
| --- | --- |
| [`../README.zh-TW.md`](../README.zh-TW.md) | 專案總覽、系統組成、快速開始 |
| [`development.zh-TW.md`](development.zh-TW.md) | 本機跑整個 stack、服務與 port、測試、lint、常見坑 |
| [`deployment.zh-TW.md`](deployment.zh-TW.md) | 伺服器部署、環境變數、經 Gateway 的平台入口、HTTPS 憑證、LDAP over TLS、CI 部署 |
| [`../backend/README.zh-TW.md`](../backend/README.zh-TW.md) | 後端結構、API 與 WebSocket 概覽、背景迴圈、migration |
| [`../CONTRIBUTING.zh-TW.md`](../CONTRIBUTING.zh-TW.md) | 貢獻流程、貢獻的授權、commit 規範 |
| [`../SECURITY.zh-TW.md`](../SECURITY.zh-TW.md) | 回報漏洞 |
| [`../LICENSE`](../LICENSE)、[`../NOTICE`](../NOTICE)、[`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) | AGPL-3.0 授權全文、上游範本的 MIT 聲明，以及開源元件與其授權清單（英文） |

## 維運

| 文件 | 內容 |
| --- | --- |
| [`monitoring.zh-TW.md`](monitoring.zh-TW.md) | 內建健康檢查、心跳與系統告警；選用的 Prometheus / Grafana / Loki / InfluxDB stack；Proxmox Metric Server、Gateway 與 AI 監控 |
| [`ai-api-user-manual.zh-TW.md`](ai-api-user-manual.zh-TW.md) | LiteLLM 部署、模型路由、金鑰、接管獨立 gateway，以及一般使用者如何呼叫 AI API |
| [`wireguard-desktop-architecture.zh-TW.md`](wireguard-desktop-architecture.zh-TW.md) | SkyLab Connect：桌面端到 Gateway VM 的 WireGuard 隧道、ACL、Gateway 安裝 |
| [`../vllm-service/README.zh-TW.md`](../vllm-service/README.zh-TW.md) | vLLM 單模型服務與多模型 cluster；另見 [`PROJECT_OVERVIEW.zh-TW.md`](../vllm-service/docs/PROJECT_OVERVIEW.zh-TW.md)、[`SHAREGPT_QUICKSTART.zh-TW.md`](../vllm-service/docs/SHAREGPT_QUICKSTART.zh-TW.md) 與 [LiteLLM README](../vllm-service/litellm/README.md)（英文） |
| [`../monitoring/prometheus/targets/README.md`](../monitoring/prometheus/targets/README.md) | vLLM 抓取目標檔如何產生（英文） |

## 產品與設計

| 文件 | 內容 |
| --- | --- |
| [`multi-machine-environment-sop.zh-TW.md`](multi-machine-environment-sop.zh-TW.md) | 多機教學環境建構、發布與學生使用的正式 SOP（v1.0，2026-08-31）：角色、資料模型、端到端流程 |
| [`ai-navigation-teaching-workflows.zh-TW.md`](ai-navigation-teaching-workflows.zh-TW.md) | 建立班級與環境編輯精靈中的導覽 AI 設計筆記 |
| [`frontend-style-guide.zh-TW.md`](frontend-style-guide.zh-TW.md) | 前端樣式規範：SCSS 架構、設計 token、元件、表單、表格、深色模式 |
| [`database-design.zh-TW.md`](database-design.zh-TW.md) | 資料庫正規化（1NF–3NF）、刻意保留的冗餘與一致性控管、schema 變更檢查清單 |

## 歷史報告

為特定日期寫的報告，當歷史看，不是現行規格。

| 文件 | 內容 |
| --- | --- |
| [`archive/2026-09-09-ui-consistency-audit.md`](archive/2026-09-09-ui-consistency-audit.md) | 28 項 UI 一致性與可用性回饋的稽核，含根因與工作量 |
| [`archive/2026-10-03-ai-api-concurrency-analysis.md`](archive/2026-10-03-ai-api-concurrency-analysis.md) | AI API 併發故障（DB 連線在等待上游期間被抱住）的根因分析與修正定案 |

## 撰寫文件的慣例

- 新指南以英文 `name.md` 建立，配一份 `name.zh-TW.md`，並在 H1 下放語言切換列。
- 檔名用純描述、不加日期前綴；有日期的報告放 `archive/`。
- 程式註解與設定檔（`.env.example`、`docker-compose.yml`、Prometheus 設定）一律連到英文檔，翻譯更新時引用才不會失效。
- 指令、路徑、變數名稱與表格在兩種語言中保持一致。
