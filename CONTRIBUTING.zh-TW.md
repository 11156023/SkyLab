# 參與 SkyLab 開發

> [English](./CONTRIBUTING.md) | **繁體中文**

感謝你願意投入時間貢獻。本頁說明專案的基本規範；開發環境的建置請見 [`docs/development.zh-TW.md`](docs/development.zh-TW.md)。

## 開始之前

- **錯誤回報與小修正**（錯字、lint 警告、附測試的可重現 bug）可以直接開 pull request。
- **較大的變更**（新功能、schema 變更、任何動到建置、網路或認證的改動）請先開 GitHub issue 說明問題與預計做法，先對設計取得共識再投入時間。
- 這套平台實際部署在教室裡。會改變既有行為的變更（預設值、API 回應、閘道規則）需要提供升級路徑，並在 `docs/` 對應文件補上說明。

## 開發流程

1. Fork 本 repo，從 `main` 開分支。
2. 依 [`docs/development.zh-TW.md`](docs/development.zh-TW.md) 建好環境。
3. 進行修改，並遵守架構規則：
   - 後端採 **Routes → Services → Infrastructure** 分層。Route 是薄控制器，業務邏輯放 `services/`，外部系統（Proxmox、SSH、Redis、LLM）只在 `infrastructure/` 呼叫。
   - DB 資料表放 `backend/app/models/`，API schema 放 `backend/app/schemas/`，不要混用。
   - Model 任何變更都要建 Alembic migration（`alembic revision --autogenerate`）。
   - 前端沒有自動生成的 API client。在 `frontend/src/services/*.js` 新增或更新對應函式，並附 Vitest mock 測試。
   - 面向使用者的文字一律走 react-i18next（`frontend/src/locales/`），三個語系（zh-TW、en、ja）都要加 key。
4. 開 PR 前先跑檢查：

   ```bash
   # backend
   cd backend
   uv run ruff check .
   uv run ruff format --check .
   bash ./scripts/test.sh

   # frontend
   cd frontend
   bun run test
   bun run build
   ```

   Pre-commit hook 由 prek 管理：執行一次 `uv run prek install -f`，之後每次 commit 會自動跑。

## Pull request

- 一個 PR 只做一件事。重構與行為變更分開送。
- 說明改了什麼、為什麼改，並連結相關 issue。
- 行為變更要補或更新測試。後端測試在 `backend/tests/`，前端 service 測試與 service 檔放在一起。
- 變更設定、部署步驟或維運流程時，同步更新 `docs/` 的文件。英文版是主要版本；有 `*.zh-TW.md` 對應檔時請一起更新。
- CI 必須通過（後端測試、前端測試、migration 檢查、CodeQL）。

## 貢獻的授權

SkyLab 以 AGPL-3.0 授權，維護者另外也提供商業授權。為了讓兩者都能成立，所有貢獻都依下列條件接受：

- 每個 commit 都要以 sign-off 表示你確認 [Developer Certificate of Origin](https://developercertificate.org/)（`git commit -s`，會加上 `Signed-off-by: 你的名字 <email>` 結尾）。含未簽署 commit 的 PR 會被要求補簽。
- 送出貢獻即表示你以 AGPL-3.0 授權該貢獻給本專案，**並且**授予 SkyLab 維護者永久、全球、免權利金的權利，將它作為 SkyLab 的一部分以其他授權條款（包括商業授權）散布。你保留自己作品的著作權。
- 只提交你有權貢獻的程式碼。從別處複製的程式碼必須有相容的授權，並在 `NOTICE` 註明出處。
- 新增或升級直接依賴時，請同步更新 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)（桌面端另有 `desktop-client/THIRD_PARTY_NOTICES.txt`）。避免使用與 AGPL-3.0 不相容的授權。

## Commit 訊息

- 沿用歷史中的慣例前綴：`feat(scope): …`、`fix(scope): …`、`docs: …`、`refactor(scope): …`、`chore: …`。
- 標題用祈使語氣，長度約 72 字元以內。中文或英文皆可。
- 每個 commit 都要 sign-off（`git commit -s`），見上一節。
- **嚴禁加入 AI 協作者署名或生成標記**（`Co-Authored-By: …`、「Generated with …」之類）。帶有這類標記的 commit 會被要求重寫。
- 絕不提交 `.env` 或任何含憑證的檔案，以 `.env.example` 為範本。

## 自動化與 AI 輔助的貢獻

歡迎使用任何能幫上忙的工具，包括 AI 助手。但貢獻仍必須反映你自己的理解：你應該能解釋 PR 裡的每一處變更，而且已在本機實際跑過。看起來是自動生成、作者又沒有自行檢視過的 PR 會被關閉。

## 有問題？

在本 repo 開 GitHub issue 或 discussion。
