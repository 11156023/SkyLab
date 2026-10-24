# 官網

對外的介紹網站放在 `site/`，發布在 `https://skylab-tw.com`。它跟平台分開：各校的 SkyLab（例如 `ntub.skylab-tw.com`）一打開就是登入頁，想試用 SkyLab 的學校則在官網上找到聯絡信箱與 GitHub 連結。

## 內容

- 原本是平台 `/` 頁面的捲動動畫介紹（React + GSAP，繁體中文／English／日本語，日夜模式）。
- 「聯繫我們」按鈕會開信寄到 `site/site.config.js` 裡的信箱，另有 GitHub repo 與文件站部署指南的連結。
- 「實際操作畫面」展示區，在瀏覽器視窗框裡輪播四段真實介面的短片。短片由 `website/scripts/manual-shots/clips.mjs` 以 mock 資料錄製（見 `website/README.zh-TW.md`「示範短片」），放在 `site/public/demo/<語言>/`；繁體中文頁播繁中版，英文與日文頁播英文版。展示區接近畫面才載入，偏好減少動態效果的訪客不會自動播放。
- 頁面上的數字（節點、教室座位、防火牆規則等）是示意用，來自 `site/src/stats.json`。

| 路徑 | 用途 |
| --- | --- |
| `site/src/landing/` | 頁面元件與樣式 |
| `site/src/locales/{zh-TW,en,ja}.json` | 文字，含 SEO 標題與描述 |
| `site/site.config.js` | 官網網址、文件站網址、聯絡信箱、GitHub 網址（單一來源） |
| `site/src/seo.js` | head 標籤、`sitemap.xml`、`robots.txt` |
| `site/src/entry-server.jsx`、`site/scripts/prerender.mjs` | 預渲染三個語言頁 |
| `site/public/` | favicon、分享縮圖、`404.html`、Cloudflare `_headers` |

樣式透過 Sass 的 `loadPaths` 沿用前端的設計 token（`frontend/src/assets/styles` 的 variables、mixins、themes、reset），所以改到那裡也會重建官網。

## 在本機執行

```bash
cd site
bun install
bun run dev        # http://127.0.0.1:5180，在瀏覽器端渲染；/en/ 與 /ja/ 也能開
bun run test       # Vitest
bun run build      # dist/：三個預渲染頁、sitemap.xml、robots.txt
bun run preview    # 提供 dist/；每次重建後要重啟
```

## SEO

- 每種語言都是預渲染好的頁面，完整文字直接在 HTML 裡：`/`（繁體中文）、`/en/`、`/ja/`。`bun run build` 依序跑前端建置、`src/entry-server.jsx` 的 SSR 建置，再由 `scripts/prerender.mjs` 把渲染好的頁面與 head 標籤寫進各自的 `index.html`。瀏覽器接手（hydrate）預渲染的 HTML。
- 每頁有自己的標題與描述、canonical 網址、指向三種語言與 `x-default` 的 `hreflang`、帶該語言分享縮圖的 Open Graph 與 Twitter card 標籤，以及 JSON-LD（`WebSite`、`SoftwareApplication`）。
- `sitemap.xml` 列出三個頁面與彼此的對應網址；`robots.txt` 全站開放並指向 sitemap。
- 所有絕對網址都來自 `SITE_URL`，預設 `https://skylab-tw.com`。要用別的網址建置，設定環境變數 `SITE_URL`（在 Cloudflare Pages 上是專案的環境變數）。
- 分享縮圖 `public/og-image.jpg`、`og-image-en.jpg`、`og-image-ja.jpg` 是 1200×630、拿掉頂列的開場畫面截圖。開場畫面改了就要重拍；少了任何一張測試會失敗。
- 瀏覽器的第一次渲染必須跟預渲染的 HTML 一致，而預渲染畫的是桌機、有運鏡、夜間模式的版本。依瀏覽器而定的東西（視窗寬度、減少動態效果、記住的日間模式）都在 hydrate 之後的 layout effect 才套用。`LandingPage.test.jsx` 會 hydrate 預渲染的 HTML，有任何不一致就失敗。
- 不存在的路徑由 Cloudflare Pages 回 `404.html`，狀態碼 404。

## 部署到 Cloudflare Pages

Cloudflare Pages 專案 `skylab-site` 直接連到 GitHub repo（Git integration），由 Cloudflare 自己跑測試、建置與部署。推送到 `main` 成為正式部署；本 repo 的 pull request 會得到 `<branch>.skylab-site.pages.dev` 的預覽（預覽會送出 `X-Robots-Tag: noindex`）；來自 fork 的 pull request 沒有預覽。`.github/workflows/site.yml` 跑測試與建置當檢查，來自 fork 的 pull request 也會跑，但不負責部署。

一次性設定，在 Workers & Pages → Create → Pages → Connect to Git：

1. 選 `ntubclass/SkyLab`。repo 要裝好 Cloudflare Workers & Pages 的 GitHub App，跟文件站用的是同一個。
2. 專案名稱 `skylab-site`，production branch `main`，framework preset 選 None。
3. Build command `cd site && bun install --frozen-lockfile && bun run test && bun run build`，build output directory `site/dist`，root directory 留空（建置會讀 `site/` 外面的 `frontend/src/assets/styles`）。
4. 環境變數 `BUN_VERSION` = `1.4.4` 與 `SKIP_DEPENDENCY_INSTALL` = `1`。選用：網址不是 `https://skylab-tw.com` 時設 `SITE_URL`；文件站不在 `https://docs.skylab-tw.com` 時設 `DOCS_URL`。
5. 第一次部署後，到 Settings → Build → Build watch paths，include paths 設為 `site/*, frontend/src/assets/styles/*`。
6. 到 Custom domains 加上 `skylab-tw.com`。根網域要求 `skylab-tw.com` 這個 zone 在 Cloudflare 上。如果也加了 `www.skylab-tw.com`，用 redirect rule 把它轉到 `https://skylab-tw.com`，讓搜尋引擎只收錄一個網址。
7. 第一次正式部署後，把網站加進 Google Search Console 並提交 `https://skylab-tw.com/sitemap.xml`。
8. 用 Cloudflare Email Routing 設定聯絡信箱 `contact@skylab-tw.com`：在 `skylab-tw.com` 這個 zone 打開 Email → Email Routing 並啟用（會加上 MX 與 TXT 紀錄），在 Destination addresses 加入團隊的收件信箱並點驗證信確認，再建立一條把 `contact@skylab-tw.com` 轉寄過去的 routing rule。Email Routing 只負責收信：從收件信箱回信時，對方看到的是收件信箱本身的地址，除非另外設定以 `contact@skylab-tw.com` 寄出。

用 Direct Upload 建立的 Pages 專案之後不能改成 Git integration；要刪掉，再用 Connect to Git 重新建立。

學校的平台（例如 `ntub.skylab-tw.com`）不從這裡部署；那是一套經由平台入口對外的 SkyLab（見[部署指南](/zh-TW/guides/deployment)）。

## 修改聯絡信箱或連結

改 `site/site.config.js`。按鈕、頁尾與 JSON-LD 的聯絡信箱和 GitHub 網址都從這裡讀，`SITE_URL` 與 `DOCS_URL` 的預設值也在這裡。這裡要放角色信箱，不放個人信箱：網頁與 git 紀錄都是公開的。要換人收信，改 Cloudflare 的轉寄規則即可，網站不用改。

## 相關

- [多語系](/zh-TW/development/i18n)：官網的語系檔
- [部署指南](/zh-TW/guides/deployment)：安裝 SkyLab 本身
