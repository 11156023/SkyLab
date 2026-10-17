# SkyLab 文件網站

> [English](./README.md) | **繁體中文**

對外的文件網站，以 [VitePress](https://vitepress.dev/) 建置，由 `.github/workflows/docs-site.yml` 部署到 Cloudflare Pages。

## 網站內容

| 區塊 | 來源 |
| --- | --- |
| 首頁、快速開始、概念與架構、疑難排解、WebSocket 參考 | 直接寫在這裡：`index.md`、`getting-started.md`、`concepts.md`、`troubleshooting.md`、`reference/websocket.md`，以及 `zh-TW/` 下的對應檔 |
| 指南 | 由 `scripts/sync-docs.mjs` 從 [`../docs/`](../docs/README.zh-TW.md) 複製到 `guides/` 與 `zh-TW/guides/`（已 gitignore）。只改 `docs/` 下的檔案，不要改複本。 |
| REST API | `reference/api.md` 用 [Scalar](https://github.com/scalar/scalar) 呈現 `public/openapi.json`。schema 由 `backend/scripts/export_openapi.py` 從後端匯出（已 gitignore）。 |

`.vitepress/config.mts` 放導覽列與兩個語系的側欄；`docs/` 新增指南時要在那裡加一筆。Mermaid 圖與 API 參考都是在瀏覽器端用 CDN 腳本渲染，網站建置不依賴這兩個套件。

## 本機作業

```bash
# 選用：API 參考（沒有的話頁面會說明怎麼產生）
cd backend
uv run --no-sync python scripts/export_openapi.py ../website/public/openapi.json

cd ../website
bun install
bun run dev        # http://localhost:5173，啟動時會重新同步 docs/
bun run build      # 輸出到 .vitepress/dist
bun run preview
```

`bun run sync` 只複製指南不啟動任何東西。匯出腳本只匯入 FastAPI app 而不啟動它，需要專案的 `.env`（或等效的環境變數），但不需要資料庫、Redis 或 Proxmox。

## 部署

workflow 在推送到 `main`、以及動到 `docs/`、`website/`、`backend/app/` 或匯出腳本的 pull request 時執行：

1. 在 `backend/` 跑 `uv sync` 並匯出 `openapi.json`。
2. 在 `website/` 跑 `bun install --frozen-lockfile` 與 `bun run build`。
3. 以 `wrangler pages deploy` 部署到 Cloudflare Pages 專案 `skylab-docs`。推送到 `main` 成為正式部署；本 repo 的 pull request 會得到 `<branch>.skylab-docs.pages.dev` 的預覽。來自 fork 的 pull request 只建置不部署。

一次性設定：

1. 在 Cloudflare 後台建立一個具「Cloudflare Pages: Edit」權限的 API token，並記下 account id。
2. 把 `CLOUDFLARE_API_TOKEN` 與 `CLOUDFLARE_ACCOUNT_ID` 加為 repo secrets。沒有這兩個值時 workflow 只建置、略過部署。
3. 第一次部署會自動建立專案。之後到 Workers & Pages → `skylab-docs` → Custom domains 加上文件網域；zone 在 Cloudflare 上時 DNS 紀錄會自動建立。
4. 把專案的 production branch 設成 `main`，預覽與正式才不會互換。

要改專案名稱，修改 workflow 裡的 `CLOUDFLARE_PAGES_PROJECT`。

## 慣例

- 這裡的頁面遵守和 `docs/` 一樣的規則：英文為主、`zh-TW/` 下逐句人工翻譯的對應檔、指令與表格兩邊一致。
- `config.mts` 的側欄順序與 `docs/README.md` 保持一致。
- 同步進來的指南，其「編輯此頁」連結會經由同步腳本寫入的 `source` frontmatter 指回 `docs/` 下的檔案。
