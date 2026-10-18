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
| `scripts/manual-shots/` | 拍攝使用手冊的步驟截圖（見下方） |
| `public/manual-shots/` | 這些截圖，`<語系>/<頁面>/s<小節>-<步驟>.webp` |

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

## 手冊截圖

使用手冊的每個步驟下方都有一張截圖，以真正的前端搭配 mock API 拍攝，語言跟頁面一致。小節 `n` 是「操作步驟」底下第 n 個 `###` 標題；步驟 `k` 是該小節內第 k 個編號項目（沒有編號步驟的小節配一張圖，算作步驟 0）。每一頁有一個場景檔 `scripts/manual-shots/scenes/<頁面>.mjs`，負責開啟頁面、準備 mock 資料，並對每個步驟呼叫 `shot(步驟, 元素)`；該元素會在圖上框出來。

```bash
cd frontend && bun run build                         # 截圖取自 frontend/dist
cd ../website
npx playwright install chromium                      # 只需一次；或把 CHROME_PATH 設成 Chrome／Chromium 的路徑
node scripts/manual-shots/shoot.mjs my-requests      # 拍一頁（不給頁名就全部）；--lang、--section 可縮小範圍
node scripts/manual-shots/preview.mjs my-requests    # 總覽圖輸出到 scripts/manual-shots/.debug/
node scripts/manual-shots/inject.mjs                 # 把圖片行插到各步驟下方
node scripts/manual-shots/check.mjs --strict         # 確認兩種語言的每個步驟都有圖
```

瀏覽器時鐘固定在 2026-10-12 10:00（台北），所以重跑同一個場景會得到相同的圖。修改手冊某頁的步驟時，要一併更新它的場景、重拍該頁，再跑一次 `inject.mjs`；它只會移除並重新插入自己產生的圖片行。某頁的介面改了，就重拍那一頁。

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
