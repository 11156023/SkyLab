# 文件索引

SkyLab repo 的所有文件都在本站，位於 repo 的 `website/` 目錄。英文頁是主要版本；除非另外標示，每一頁在 `website/zh-TW/` 下都有繁體中文版。側欄依用途分組，本頁依檔案列出。

## 入門

| 頁面 | 內容 |
| --- | --- |
| [專案總覽](/zh-TW/overview)（英文：[README](https://github.com/ntubclass/SkyLab#readme)） | 專案總覽、系統組成、快速開始 |
| [快速開始](/zh-TW/getting-started) | 從 `docker compose up` 到初始化精靈與第一次登入 |
| [概念與架構](/zh-TW/concepts) | 元件、角色、詞彙、背景工作、資料放在哪裡 |
| [使用手冊](/zh-TW/manual/) | 每個功能一頁，依角色分組 |
| [疑難排解](/zh-TW/troubleshooting) | 已知故障、原因與解法 |

## 維運

| 頁面 | 內容 |
| --- | --- |
| [部署指南](/zh-TW/guides/deployment) | 伺服器部署、環境變數、經閘道的平台入口、HTTPS 憑證、LDAP over TLS、CI 部署 |
| [升級與 migration](/zh-TW/operations/upgrade) | 標準升級、migration 狀態、回退、重大變更 |
| [備份與還原](/zh-TW/operations/backup) | 要備份什麼、資料庫匯出與還原、災難復原 |
| [環境變數對照表](/zh-TW/operations/environment) | `.env.example` 的每個變數，建置時產生 |
| [官網](/zh-TW/operations/site) | skylab-tw.com 介紹網站：預渲染、SEO、聯繫方式、Cloudflare Pages 部署 |
| [系統監控](/zh-TW/guides/monitoring) | 內建健康檢查、心跳與系統告警；選用的 Prometheus／Grafana／Loki／InfluxDB stack；Proxmox Metric Server、閘道與 AI 監控 |
| [註冊設定與 Google 登入](/zh-TW/guides/registration-and-google-login) | 自助註冊與管理員審核開關、Google 登入與 Google Cloud 的「Audience」設定 |
| [AI API 使用與部署手冊](/zh-TW/guides/ai-api-user-manual) | LiteLLM 部署、模型路由、金鑰、接管獨立閘道、呼叫對外 AI API |
| [WireGuard 與 SkyLab Connect](/zh-TW/guides/wireguard-desktop-architecture) | 桌面端到閘道 VM 的 WireGuard 隧道、ACL、閘道安裝 |
| [vLLM 服務](/zh-TW/guides/vllm-service)、[ShareGPT 基準測試](/zh-TW/guides/vllm-sharegpt-quickstart)、[LiteLLM 閘道](/zh-TW/guides/litellm) | 自架模型服務與對外的閘道 |

## 參考

| 頁面 | 內容 |
| --- | --- |
| [REST API](/zh-TW/reference/api) | 由後端 OpenAPI schema 產生 |
| [WebSocket 端點](/zh-TW/reference/websocket) | 主控台與即時更新 |
| [治理設定欄位](/zh-TW/reference/governance-config) | 治理設定的每個欄位 |
| [登入帳號命名政策](/zh-TW/reference/username-policy) | Linux 登入帳號的規則 |

## 開發

| 頁面 | 內容 |
| --- | --- |
| [開發環境](/zh-TW/guides/development) | 本機跑整個 stack、服務與 port、測試、lint、常見坑 |
| [後端架構](/zh-TW/guides/backend) | 後端結構、API 與 WebSocket 概覽、背景迴圈、migration |
| [資料庫設計](/zh-TW/guides/database-design) | 正規化（1NF–3NF）、刻意保留的冗餘、改 schema 的檢查清單 |
| [前端樣式規範](/zh-TW/guides/frontend-style-guide) | SCSS 架構、token、元件、表單、表格、深色模式 |
| [多語系](/zh-TW/development/i18n) | 前端、後端與桌面端語系檔；翻譯規則 |
| [貢獻指南](https://github.com/ntubclass/SkyLab/blob/main/CONTRIBUTING.zh-TW.md)、[安全政策](https://github.com/ntubclass/SkyLab/blob/main/SECURITY.zh-TW.md)、[行為準則](https://github.com/ntubclass/SkyLab/blob/main/CODE_OF_CONDUCT.zh-TW.md) | 留在 repo 根目錄讓 GitHub 顯示 |
| [授權](/zh-TW/project/license)、[`LICENSE`](https://github.com/ntubclass/SkyLab/blob/main/LICENSE)、[`NOTICE`](https://github.com/ntubclass/SkyLab/blob/main/NOTICE)、[`THIRD_PARTY_NOTICES.md`](https://github.com/ntubclass/SkyLab/blob/main/THIRD_PARTY_NOTICES.md) | AGPL-3.0、上游範本的 MIT 聲明、開源元件清單 |

## 產品與設計

| 頁面 | 內容 |
| --- | --- |
| [多機教學環境 SOP](/zh-TW/guides/multi-machine-environment-sop) | 建構、發布與使用多機教學環境的正式 SOP |
| [導覽 AI 與教學流程](/zh-TW/guides/ai-navigation-teaching-workflows) | 建班與環境編輯精靈中導覽 AI 的設計筆記 |
| [AI 導師檢查流程與不可破壞契約](/zh-TW/guides/ai-teacher-judge-contract)（僅中文） | 提案、AI 編輯、全表核對、保存與腳本編譯的標準流程 |
| [頁面導覽文案盤點](/zh-TW/guides/user-guide-content-audit)（僅中文） | 應用內頁面導覽文案的改寫建議（尚未套用） |

## 撰寫文件的慣例

- 先寫英文頁（`website/<section>/<name>.md`），再在 `website/zh-TW/` 的同一路徑放繁體中文頁，逐句翻譯；只有單一語言的頁面在側欄標示。
- 檔名用純描述、不加日期前綴。一次性的報告或稽核處理完就從網站移除，原文留在 git 歷史。
- 程式註解與設定檔（`.env.example`、`docker-compose.yml`、Prometheus 設定）以 repo 路徑連到英文檔，例如 `website/guides/monitoring.md`。
- 指令、路徑、變數名稱與表格在兩種語言中保持一致。
- 新頁面要在 `website/.vitepress/config.mts` 的側欄加項目，兩種語言都要。
