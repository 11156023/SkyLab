# SkyLab — Frontend

SkyLab 前端：以 React 19 + TypeScript + Vite 7 打造的單頁應用，整合 TanStack Router/Query、Tailwind v4、shadcn/Radix UI、noVNC、xterm.js 與 React Flow，負責所有 VM/LXC 管理、申請工作流、防火牆拓撲與管理員後台介面。

## 技術棧

- **Framework**：React 19.1 + TypeScript 5.9（strict）
- **Build**：Vite 7 + @vitejs/plugin-react-swc
- **Routing**：TanStack Router v1（檔案式路由 + 自動 code-splitting）
- **Data Fetching**：TanStack React Query v5（含 react-query-devtools）
- **Tables**：TanStack React Table v8
- **UI Components**：Radix UI（dialog、dropdown、tabs、tooltip…）+ shadcn/ui 包裝
- **Styling**：Tailwind CSS 4.1（@tailwindcss/vite）+ tw-animate-css + tailwind-merge + class-variance-authority
- **Forms**：react-hook-form + @hookform/resolvers + Zod
- **VNC / Terminal**：react-vnc + @novnc/novnc、@xterm/xterm（fit / web-links / webgl addon）
- **Visualization**：@xyflow/react（防火牆拓撲圖）、Recharts、Monaco Editor
- **Theme / i18n**：next-themes、i18next、react-i18next、i18next-browser-languagedetector
- **API Client**：@hey-api/openapi-ts（自動生成）+ Axios
- **Lint / Format**：Biome 2
- **Tests**：Playwright 1.58（Chromium）
- **Package Manager**：Bun

## 目錄結構

```
frontend/
├── src/
│   ├── main.tsx              # React 入口、Router/Query 初始化、token 刷新
│   ├── client/               # OpenAPI 自動生成的 SDK（請勿手動編輯）
│   │   ├── core/             # OpenAPI runtime（Axios 包裝）
│   │   ├── sdk.gen.ts        # 服務 class
│   │   ├── schemas.gen.ts
│   │   ├── types.gen.ts
│   │   └── index.ts
│   ├── routes/               # 檔案式路由
│   ├── components/           # UI 與功能元件（依領域分組）
│   ├── hooks/                # useAuth / useCustomToast / useMobile / useCopyToClipboard
│   ├── services/             # 手動撰寫的 API 包裝（firewall、gateway、migrationJobs…）
│   ├── providers/            # LanguageProvider、ThemeProvider
│   ├── lib/                  # i18n 設定
│   ├── locales/              # en / zh-TW / ja 翻譯資源
│   ├── ../backend/app/ai/template_recommendation/catalog_json/
│   │                         # 靜態模板資料（透過 virtual:templates 載入）
│   ├── types/                # 自訂型別
│   ├── routeTree.gen.ts      # 自動生成的路由樹
│   └── utils.ts
├── tests/                    # Playwright e2e 測試
├── public/
├── playwright.config.ts
├── vite.config.ts
├── biome.json
├── openapi-ts.config.ts
└── package.json
```

## 路由

`src/routes/` 採 TanStack Router 檔案式路由，主要分為：

**未登入區（無 sidebar）**

- `/login`、`/signup`、`/recover-password`、`/reset-password`

**`_layout` 受保護區（會檢查 token，未登入導回 `/login`）**

- 共用：`/`（dashboard）、`/settings`
- 學生：`/applications`、`/applications-create`、`/approvals`、`/approvals/$requestId`、`/my-resources`、`/my-resources/$vmid`、`/ai-api`、`/ai-api-approvals`
- 一般 / 教師：`/groups`、`/groups/$groupId`、`/firewall`
- 管理員：`/admin`、`/admin/`（使用者管理）、`/admin/configuration`、`/admin/gateway`、`/admin/audit-logs`、`/admin/migration-jobs`、`/resources`、`/resources/$vmid`、`/resources-create`

## 主要元件分組

`src/components/` 依領域組織：

| 目錄 | 內容 |
| --- | --- |
| `ui/` | shadcn 風格的 Radix 包裝（button、dialog、table、tabs、sidebar、…） |
| `Common/` | AuthLayout、ErrorComponent、PageTransitionLoader、UserAvatar、Appearance、LanguageSwitcher、DataTable |
| `Sidebar/` | Main 導覽列、User 選單 |
| `Admin/` | AddUser / EditUser / DeleteUser / 表格欄位定義 |
| `Applications/` | 建立 VM 申請、AI 對話面板、申請列欄位 |
| `Resources/` | VM 列表、CreateResources、VMActions |
| `ResourceDetail/` | 規格 / 進階設定 / 快照 / Audit log 分頁 |
| `Firewall/` | FirewallTopology（@xyflow/react 拓撲圖）、規則面板、Connection edge/dialog |
| `VNC/` | VNCConsoleDialog + NoVNCDisplay |
| `Terminal/` | TerminalConsoleDialog + XTermDisplay |
| `UserSettings/` | 個人資訊、密碼變更、刪除帳號、操作紀錄 |
| `Pending/` | 載入骨架元件 |

## API Client

### 自動生成（`src/client/`）

OpenAPI client 由 `@hey-api/openapi-ts` 從後端 `openapi.json` 產生，使用 Axios。設定檔在 `openapi-ts.config.ts`：

- 輸入：`./openapi.json`
- 輸出：`./src/client/`
- 風格：class-based service（`UsersService.readUserMe()` 等）

### 重新生成

確保後端正在執行後，從專案根目錄：

```bash
bash ./scripts/generate-client.sh
```

或手動：

```bash
# 從 backend 取出 openapi.json 並放到 frontend/openapi.json
bun run generate-client
```

> ⚠️ 不要手動修改 `src/client/` 內的檔案。若 endpoint 尚未納入 OpenAPI，可在 `src/services/` 撰寫手動包裝（目前 `firewall.ts`、`gateway.ts`、`migrationJobs.ts`、`vmRequestAvailability.ts`、`vmRequestReview.ts`、`aiApi.ts` 屬此類）。

## 認證流程

- Token 儲存於 `localStorage`：`access_token`、`refresh_token`
- `main.tsx` 內 `tryRefreshToken()` 攔截 401 並嘗試刷新；失敗則清除 token 並導回 `/login`
- `_layout` route 在 `beforeLoad` 檢查 `isLoggedIn()`
- 角色判斷：`user.role === "admin" || user.is_superuser` / `user.role === "student"`
- `useAuth` hook 提供 `loginMutation`、`signUpMutation`、`googleLoginMutation`、`logout`、`user`

## 國際化

- 命名空間：`common`、`auth`、`navigation`、`resources`、`resourceDetail`、`applications`、`approvals`、`settings`、`validation`、`messages`
- 支援語系：`en`、`zh-TW`、`ja`
- 偏好語言儲存於 `localStorage` key `SkyLab-language`

## 開發指令

```bash
bun install               # 安裝依賴
bun run dev               # Vite dev server (http://localhost:5173)
bun run build             # tsc 檢查 + 產出 production build
bun run preview           # 預覽 production build
bun run lint              # Biome 檢查並自動修復
bun run generate-client   # 從 openapi.json 生成 client
bun run test              # Playwright e2e
bun run test:ui           # Playwright UI mode
```

## 環境變數

`frontend/.env`（或 `.env.example`）：

```env
VITE_API_URL=http://localhost:8000
MAILCATCHER_HOST=http://localhost:1080   # 僅 e2e 測試使用
```

於程式碼中以 `import.meta.env.VITE_API_URL` 取用。

## 端對端測試

需先啟動後端：

```bash
docker compose up -d --wait backend
bunx playwright test          # 一般模式
bunx playwright test --ui     # UI 模式
```

設定（`playwright.config.ts`）：

- Base URL：`http://localhost:5173`
- Browser：Chromium
- 認證 storage：`playwright/.auth/user.json`
- 自動啟動 `bun run dev`

主要測試檔：`auth.setup.ts`、`login.spec.ts`、`sign-up.spec.ts`、`reset-password.spec.ts`、`admin.spec.ts`、`items.spec.ts`、`user-settings.spec.ts`

## Vite / TypeScript / Biome

- 路徑別名：`@/` → `./src/`
- Vite 自訂 `virtual:templates` plugin 載入 `../backend/app/ai/template_recommendation/catalog_json/`
- CORS header `Cross-Origin-Opener-Policy: same-origin-allow-popups`（VNC popup 需要）
- Biome 排除 `src/client/**`、`src/components/ui/**`、`routeTree.gen.ts`

## 主要功能

- VM/LXC 列表、規格調整、快照、刪除、Audit log 檢視
- 內嵌 VNC 與 LXC terminal
- VM 申請建立 / 審核（含 AI 對話協助）、自動排程結果追蹤
- 群組管理（建立、CSV 匯入、寄發初始密碼）
- 防火牆拓撲視覺化（React Flow）+ 規則 / NAT / Reverse Proxy 編輯
- Proxmox cluster 設定 + 連線測試 + cluster 統計
- AI API 憑證 / 申請 / 流量限制檢視
- 三語系切換（en / zh-TW / ja）+ 主題切換（亮 / 暗）

## 參考

- 主專案：[`../README.md`](../README.md)
- 後端：[`../backend/README.md`](../backend/README.md)
- 開發指引：[`../development.md`](../development.md)
