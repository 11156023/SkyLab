# SkyLab 文件網站

> [English](./README.md) | **繁體中文**

repo 的所有文件都放在這個目錄，以 [VitePress](https://vitepress.dev/) 建成網站，發布在 Cloudflare Pages（見下方「部署」）。目錄外只保留根目錄的 `README.md` 和 GitHub 本身會讀的檔案（`CONTRIBUTING`、`SECURITY`、`CODE_OF_CONDUCT`、`THIRD_PARTY_NOTICES`、`.github/` 的範本）。

## 結構

| 目錄 | 內容 |
| --- | --- |
| `index.md`、`getting-started.md`、`concepts.md`、`troubleshooting.md` | 首頁與入門頁 |
| `manual/` | 使用手冊，每個功能一頁；`manual/index.md` 依角色分組 |
| `guides/` | 維運、開發與設計指南，`guides/index.md` 是索引 |
| `operations/` | 升級與備份指南；`operations/environment.md` 是產生的 |
| `reference/` | REST API（`public/openapi.json` 以 Scalar 呈現）、WebSocket 端點、治理設定欄位、帳號命名政策 |
| `development/` | 多語系 |
| `project/` | 授權頁 |
| `changelog.md` | 從 git 歷史產生 |
| `zh-TW/` | 同一棵樹的繁體中文版 |
| `public/` | favicon、`openapi.json`、`public/screenshots/` 下的首頁截圖 |
| `.vitepress/` | 設定（導覽列與兩個語系的側欄在 `config.mts`）、主題元件（Scalar 與 mermaid 渲染） |
| `scripts/generate.mjs` | 每次建置前產生自動頁面 |
| `scripts/manual-shots/` | 拍攝使用手冊的步驟截圖（見下方） |
| `public/manual-shots/` | 這些截圖，每個小節一張：`<語系>/<頁面>/s<小節>.webp` |

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

使用手冊操作步驟的每個小節結尾都有一張圖，以真正的前端搭配 mock API 拍攝，語言跟頁面一致。圖中每個步驟對應的元素會框起來並標上步驟編號。小節 `n` 是「操作步驟」底下第 n 個 `###` 標題；步驟 `k` 是該小節內第 k 個編號項目（沒有編號步驟的小節算作步驟 0，圖上不標編號）。每一頁有一個場景檔 `scripts/manual-shots/scenes/<頁面>.mjs`，負責開啟頁面、準備 mock 資料，並對每個步驟呼叫 `shot(步驟, 元素)`。同一個畫面上的連續步驟共用一格；畫面換了（開啟對話框、頁面捲動）就在下方接一格。

```bash
cd frontend && bun run build                         # 截圖取自 frontend/dist
cd ../website
npx playwright-core install chromium                 # 只需一次（裝鎖定版本對應的瀏覽器）；或設定 CHROME_PATH
node scripts/manual-shots/shoot.mjs my-requests      # 拍一頁（不給頁名就全部）；--lang、--section 可縮小範圍
node scripts/manual-shots/preview.mjs my-requests    # 總覽圖輸出到 scripts/manual-shots/.debug/
node scripts/manual-shots/inject.mjs                 # 把圖片行插到各小節結尾
node scripts/manual-shots/check.mjs --strict         # 確認兩種語言的每個小節都有圖
```

瀏覽器時鐘固定在 2026-10-12 10:00（台北），所以重跑同一個場景會得到相同的圖。修改手冊某頁的步驟時，要一併更新它的場景、重拍該頁，再跑一次 `inject.mjs`；它只會移除並重新插入自己產生的圖片行。某頁的介面改了，就重拍那一頁。

撰寫或修正場景時要注意：

- `hasText` 與 `getByRole({ name })` 比對的是不分大小寫的子字串，所以「Name」也會比對到選單裡有「CNAME」選項的欄位。要從開頭比對時寫成 `` new RegExp(`^${escapeRegExp(text)}`) ``；`escapeRegExp` 在 `lib/run.mjs`。
- mock 的回應格式必須跟後端一致。尚未設定的 IP 網段是回 `200` 加 `null`，不是 `404`；格式不對時，圖上看起來會像前端壞掉（例如刪除後列表沒有更新）。
- 原生的日期、時間欄位跟著瀏覽器本身的語言，而不是頁面的語言。`launchBrowser(lang)` 會帶 `--lang`，`shoot.mjs` 每種語言各開一個瀏覽器。
- 畫面差異小於 4% 像素的連續步驟會共用一格。機器忙的時候，同一個畫面偶爾還是會被拆成兩格；一次少拍幾頁，或單獨重拍那一頁即可。
- 原生的 `title` 提示與原生 `<select>` 展開後的選單拍不到，改框住觸發它們的元素。
- 右下角的 AI 助手浮動按鈕可能落在某一格的角落。礙事的場景用 `s.page.addStyleTag({ content: '[class*="_fab_"]{visibility:hidden !important}' })` 把它藏起來。
- 提交前要逐張打開有變動的圖本身來看；`preview.mjs` 的總覽圖縮得太小，看不清框線與文字。

### 示範短片

`scripts/manual-shots/clips.mjs` 用同一份前端建置與 mock API，錄製 README 與官網展示區的短片：

```bash
cd website
FFMPEG=/path/to/ffmpeg node scripts/manual-shots/clips.mjs        # 全部短片，英文與繁中
node scripts/manual-shots/clips.mjs --lang en firewall            # 只錄一支、一種語言
```

- `scripts/manual-shots/clips/` 每個檔案是一段操作腳本。無頭 Chrome 不會畫滑鼠游標，所以每次點擊都由畫出來的游標滑過去（`rec.click`、`rec.type`、`rec.drag`），`rec.speed(n)` 讓等待的段落快轉 n 倍。
- 輸出：官網用的 `site/public/demo/<語言>/<短片>.mp4` 與封面 `.jpg`，英文另產生 README 用的 `.github/readme/<短片>.gif`。ffmpeg 需要支援 libx264。
- 短片以會走的時鐘錄製（`clockRuns`）：會輪詢進度的頁面比對資料到達的時間，手冊截圖那樣把時鐘凍結，這個時間就永遠不會變。
- 提交前每支都要實際看過。畫面重繪時才會送出影格，等錯東西的短片只會看起來像畫面卡住，不會報錯。

## 部署

Cloudflare Pages 專案 `skylab-docs` 直接連到 GitHub repo（Git integration），由 Cloudflare 自己建置與部署。推送到 `main` 成為正式部署；本 repo 的 pull request 會得到 `<branch>.skylab-docs.pages.dev` 的預覽；來自 fork 的 pull request 沒有預覽。建置指令是 `website/scripts/cloudflare-pages-build.sh`：

1. 抓完整 git 歷史。Pages 是淺層 clone，而更新紀錄頁由歷史產生。
2. 沒有 uv 就安裝，在 `backend/` 跑 `uv sync` 並匯出 `openapi.json`。
3. 在 `website/` 跑 `bun install --frozen-lockfile` 與 `bun run build`（建置前先產生環境變數與更新紀錄頁）。

`.github/workflows/docs-site.yml` 在 `main` 與 pull request（含來自 fork 的）上跑同一支腳本當檢查，不負責部署。

一次性設定，在 Workers & Pages → Create → Pages → Connect to Git：

1. 在 repo 安裝 Cloudflare Workers & Pages 的 GitHub App 並選 `SkyLab-tw/SkyLab`。要由組織 owner 或具 GitHub Apps Manager 角色的成員來做。
2. 專案名稱 `skylab-docs`，production branch `main`，framework preset 選 None。
3. Build command `bash website/scripts/cloudflare-pages-build.sh`，build output directory `website/.vitepress/dist`，root directory 留空。
4. 環境變數 `BUN_VERSION` = `1.4.4`，與鎖檔和 CI 用的 Bun 版本一致（建置環境的預設版本較舊）；`SKIP_DEPENDENCY_INSTALL` = `1`，因為腳本自己安裝套件。
5. 第一次部署後，到 Settings → Build → Build watch paths，include paths 設為 `website/*, backend/app/*, backend/scripts/export_openapi.py, .env.example`，只動到 repo 其他部分的 commit 就不會重建文件站。
6. 到 Custom domains 加上 `docs.skylab-tw.com`；zone 在 Cloudflare 上時 DNS 紀錄會自動建立。
7. 第一次正式部署後，把網站加進 Google Search Console 並提交 `https://docs.skylab-tw.com/sitemap.xml`。

用 Direct Upload 建立的 Pages 專案之後不能改成 Git integration；要刪掉，再用 Connect to Git 重新建立。repo 轉移到別的擁有者時也一樣（2026 年 10 月從 `ntubclass` 轉到 `SkyLab-tw`）：專案仍指著舊位置、不會再建置，要在新組織安裝 GitHub App、重新建立專案，再把自訂網域移過去。

### SEO

VitePress 會預先產生每一頁的 HTML。其餘由 `.vitepress/config.mts` 補上，絕對網址都以 `https://docs.skylab-tw.com` 為準（環境變數 `DOCS_URL` 可覆寫）：

- `sitemap.xml`，每頁都列出英文與繁體中文版本互為 hreflang 對應；`robots.txt` 指向它。
- 每頁都有 canonical 網址；中英兩版都存在的頁面有指向另一語言的 hreflang；Open Graph／Twitter card 標籤帶分享縮圖 `public/og-image.jpg` 或 `og-image-zh-TW.jpg`（1200×630 的首頁截圖，首頁改了要重拍）。404 頁設為 `noindex`。
- `public/_headers` 讓 `skylab-docs.pages.dev` 與預覽網址都送出 `X-Robots-Tag: noindex`，搜尋引擎只會收錄 `docs.skylab-tw.com`。
- 有繁體中文版的英文頁，`<head>` 裡有一小段腳本：瀏覽器偏好中文的讀者在頁面顯示前就會被換到中文版（`theme/languagePreference.ts`）。在語言選單選過的語言會記住，之後以它為準。繁體中文頁不會轉走，爬蟲也不會被轉，兩個版本都能被收錄。
- 每頁的描述：有 front matter `description` 就用它，首頁用 hero 的 tagline，其餘取該頁第一段文字轉成純文字，再截到搜尋結果顯示得下的長度。

## 慣例

- 先寫英文頁，再在 `zh-TW/` 的同一路徑放對應頁，逐句人工翻譯；指令、表格與標題結構一致。
- 新頁面要在 `.vitepress/config.mts` 的兩個語系側欄都加項目。
- Mermaid 圖（```` ```mermaid ```` 圍欄）與 API 參考都在瀏覽器端用 CDN 腳本渲染，網站建置不依賴這兩個套件。
- 看起來像 HTML 標籤的佔位符要放在反引號裡；程式碼以外的 `{{` 會被當成 Vue 運算式。
