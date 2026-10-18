# SkyLab 文件網站

> [English](./README.md) | **繁體中文**

repo 的所有文件都放在這個目錄，以 [VitePress](https://vitepress.dev/) 建成網站，由 `.github/workflows/docs-site.yml` 部署到 Cloudflare Pages。目錄外只保留根目錄的 `README.md` 和 GitHub 本身會讀的檔案（`CONTRIBUTING`、`SECURITY`、`CODE_OF_CONDUCT`、`THIRD_PARTY_NOTICES`、`.github/` 的範本）。

## 結構

| 目錄 | 內容 |
| --- | --- |
| `index.md`、`getting-started.md`、`concepts.md`、`troubleshooting.md` | 首頁與入門頁 |
| `manual/` | 使用手冊，每個功能一頁；`manual/index.md` 依角色分組 |
| `guides/` | 維運、開發與設計指南（原本的 `docs/`），`guides/index.md` 是索引，`guides/archive/` 放有日期的報告 |
| `operations/` | 升級與備份指南；`operations/environment.md` 是產生的 |
| `reference/` | REST API（`public/openapi.json` 以 Scalar 呈現）、WebSocket 端點、治理設定欄位、帳號命名政策 |
| `development/` | 多語系 |
| `project/` | 授權頁 |
| `changelog.md` | 從 git 歷史產生 |
| `zh-TW/` | 同一棵樹的繁體中文版 |
| `public/` | favicon、`openapi.json`、`public/demos/` 下的 SkyLab Connect demo |
| `.vitepress/` | 設定（導覽列與兩個語系的側欄在 `config.mts`）、主題元件（Scalar 與 mermaid 渲染） |
| `scripts/generate.mjs` | 每次建置前產生自動頁面 |

產生的檔案（`operations/environment.md`、`changelog.md`、`public/openapi.json` 與它們的 `zh-TW/` 版本）已 gitignore。

## 本機作業

```bash
# 選用：API 參考（沒有的話頁面會說明怎麼產生）
cd backend
uv run --no-sync python scripts/export_openapi.py ../website/public/openapi.json

cd ../website
bun install
bun run dev        # http://localhost:5173
bun run build      # 輸出到 .vitepress/dist
bun run preview    # 每次重建後要重啟：它會快取檔案清單
```

`bun run generate` 只產生自動頁面。匯出腳本只匯入 FastAPI app 而不啟動它，需要專案的 `.env`（或等效的環境變數），但不需要資料庫、Redis 或 Proxmox。

## 部署

workflow 在推送到 `main`、以及動到 `website/`、`backend/app/`、`.env.example` 或匯出腳本的 pull request 時執行：

1. 在 `backend/` 跑 `uv sync` 並匯出 `openapi.json`。
2. 在 `website/` 跑 `bun install --frozen-lockfile` 與 `bun run build`（建置前先產生環境變數與更新紀錄頁；checkout 會抓完整 git 歷史給更新紀錄用）。
3. 以 `wrangler pages deploy` 部署到 Cloudflare Pages 專案 `skylab-docs`。推送到 `main` 成為正式部署；本 repo 的 pull request 會得到 `<branch>.skylab-docs.pages.dev` 的預覽。來自 fork 的 pull request 只建置不部署。

一次性設定：

1. 在 Cloudflare 後台建立一個具「Cloudflare Pages: Edit」權限的 API token，並記下 account id。
2. 把 `CLOUDFLARE_API_TOKEN` 與 `CLOUDFLARE_ACCOUNT_ID` 加為 repo secrets。沒有這兩個值時 workflow 只建置、略過部署。
3. 第一次部署會自動建立專案。之後到 Workers & Pages → `skylab-docs` → Custom domains 加上文件網域；zone 在 Cloudflare 上時 DNS 紀錄會自動建立。
4. 把專案的 production branch 設成 `main`，預覽與正式才不會互換。

要改專案名稱，修改 workflow 裡的 `CLOUDFLARE_PAGES_PROJECT`。

## 慣例

- 先寫英文頁，再在 `zh-TW/` 的同一路徑放對應頁，逐句人工翻譯；指令、表格與標題結構一致。
- 新頁面要在 `.vitepress/config.mts` 的兩個語系側欄都加項目。
- Mermaid 圖（```` ```mermaid ```` 圍欄）與 API 參考都在瀏覽器端用 CDN 腳本渲染，網站建置不依賴這兩個套件。
- 看起來像 HTML 標籤的佔位符要放在反引號裡；程式碼以外的 `{{` 會被當成 Vue 運算式。
