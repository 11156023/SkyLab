# SkyLab AI 導入簡報大綱

本文整理目前專案中 AI 相關功能的定位、流程與可報告重點，供講稿準備使用。

## 1. AI 導入總覽

SkyLab 的 AI 功能定位不是單一聊天機器人，而是「嵌入校園雲端管理流程的智慧輔助層」。它主要解決三類問題：第一，降低使用者理解平台功能與填寫申請表單的門檻；第二，協助老師把評分情境轉成可執行、可追蹤的檢查流程；第三，讓平台能安全地提供模型 API 能力，並保留審核、限流與用量治理。

從產品角度看，AI 在 SkyLab 裡扮演的是「助理」而不是「決策者」。AI 可以建議頁面、推薦模板、整理評分表、產生腳本與提供評分建議，但最後仍由系統規則、後端驗證與使用者操作來決定是否套用。這樣的定位可以避免 AI 直接控制基礎設施，同時保留自然語言互動帶來的效率。

從系統角度看，AI 被放在後端服務層統一管理。前端負責收集使用者情境與呈現結果；後端負責組 prompt、補充平台資料、呼叫 vLLM-compatible 模型、驗證模型輸出、寫入用量紀錄；模型服務則作為推理後端。這讓 AI 功能可以共用模型設定、API 金鑰與監控機制，也避免每個前端頁面各自直連模型造成安全風險。

從使用者角色看，AI 導入可以分成三種價值：

- 學生：用自然語言找到功能入口，並在申請 VM/LXC 時取得較合理的模板與資源建議。
- 老師：把作業評分規則轉成結構化項目，產生受管收集腳本，對學生 VM/LXC 執行後取得 AI 輔助評分說明。
- 管理者：審核 AI API 使用申請、管理金鑰、查看 token 用量與錯誤紀錄，掌握模型資源使用狀態。

因此，SkyLab 的 AI 模組可以用一句話概括：

> AI 不是取代平台操作，而是把「找功能、填申請、看評分、管模型用量」這些原本需要經驗的流程，轉成可被輔助、可被驗證、可被治理的工作流。

目前功能可拆成以下幾個貼近平台流程的輔助模組：

- AI 導覽：把使用者自然語言需求轉成可操作的前端頁面入口。
- 機器模板推薦：根據申請情境、服務需求、GPU/節點資訊，自動建議 VM/LXC 規格與模板。
- 老師情境評分：協助老師解析評分表、產生受管收集腳本、執行腳本後再用 AI 產生評分建議。
- AI API 金鑰：提供平台內的 OpenAI-compatible API proxy，讓通過審核的使用者取得金鑰並追蹤用量。
- AI 監控：管理者可查看 proxy 與模板推薦的 token、呼叫量、錯誤與使用者彙總。

共同模型後端以 vLLM / OpenAI-compatible API 為主，系統層設定集中在 `.env` 與 `backend/config/system-ai.json`。這代表未來若要替換模型或調整推理參數，可以從後端集中管理，而不是逐頁修改前端邏輯。

## 2. 系統設定與模型接線

核心設定分成兩層：

- `.env`：放服務位址與密鑰，例如 `VLLM_BASE_URL`、`VLLM_API_KEY`、`VLLM_MODEL_NAME`、`AI_API_BASE_URL`、`AI_API_API_KEY`。
- `backend/config/system-ai.json`：放各 AI 模組的模型參數，例如 timeout、temperature、max_tokens、top_p、是否啟用 thinking。

主要程式入口：

- `backend/app/ai/system_config.py`：讀取系統 AI 設定。
- `backend/app/features/ai/config.py`：讀取 AI API proxy 與金鑰相關設定。
- `backend/app/infrastructure/ai/vllm_client.py`：統一封裝 vLLM chat completions 呼叫。
- `backend/app/api/routes/ai.py`：統一掛載 AI 相關 API，包括 AI API、監控、proxy、PVE log、AI 導覽與模板推薦。

簡報說法：

> SkyLab 把模型服務包在後端，前端不直接持有上游模型金鑰。不同 AI 功能共用 vLLM-compatible client，但各自有自己的 prompt、輸出 schema 與安全邊界。

## 3. AI 導覽

功能定位：

使用者輸入像「我要申請機器」、「看 AI 用量」、「管理 GPU」這類自然語言，後端依使用者角色篩選可去的頁面，再請模型選擇最合適路徑。

流程：

1. 前端呼叫 `/api/v1/ai/navigation/resolve`。
2. 後端依角色建立允許頁面清單，學生、老師、管理員看到的候選路由不同。
3. LLM 回傳 strict JSON，包含 `intent`、`confidence`、`action`、`primary_path`、候選路徑與澄清問題。
4. 後端只接受 catalog 內的路徑，不讓模型發明不存在頁面。
5. 若模型未設定、回傳非 JSON 或呼叫失敗，使用關鍵字 fallback。

主要檔案：

- `backend/app/api/routes/ai_navigation.py`
- `backend/app/ai/navigation/catalog.py`
- `backend/app/ai/navigation/service.py`
- `backend/app/ai/navigation/prompt.py`
- `frontend/src/services/aiNavigation.ts`

可強調重點：

- 導覽結果有三種：直接跳轉、提供建議、要求澄清。
- 導覽會尊重權限，不會把學生導到 admin-only 頁面。
- 有 fallback，所以模型異常時仍可提供基本導覽。

## 4. 機器模板推薦

功能定位：

在資源申請情境中，AI 協助把需求轉成可提交的 VM/LXC 表單，例如服務模板、CPU、RAM、Disk、GPU、OS 與申請理由。

流程：

1. 前端在申請頁收集對話、快速入門情境與表單上下文。
2. `/api/v1/ai/template-recommendation/chat` 提供需求討論。
3. `/api/v1/ai/template-recommendation/recommend` 先抽取意圖，再生成推薦方案。
4. 後端會合併：
   - 使用者對話與角色情境。
   - 模板 catalog，來源預設為 `frontend/src/json`。
   - 支援的能力表，目前聚焦 `wordpress`、`n8n`、`postgresql`、`openwebui`。
   - 節點與 GPU 即時資訊，含短 TTL cache。
   - 使用者已填的起訖時間、資源類型與 GPU 選擇。
5. AI 回傳 JSON plan，後端再正規化，過濾不支援模板並補齊安全預設值。
6. 成功或失敗都會寫入 `ai_template_call_logs`，供個人用量與 admin 監控查詢。

主要檔案：

- `backend/app/api/routes/ai_template_recommendation.py`
- `backend/app/ai/template_recommendation/recommendation_service.py`
- `backend/app/ai/template_recommendation/catalog_service.py`
- `backend/app/ai/template_recommendation/capability_catalog.py`
- `frontend/src/components/Applications/AiChatPanel.tsx`
- `frontend/src/services/aiTemplateRecommendation.ts`

可強調重點：

- 不是只叫模型「猜規格」，而是把模板清單、資源選項、GPU 可用量與節點容量一起放進 prompt。
- 後端會驗證與正規化模型輸出，避免不存在的模板或不合理資源值直接進表單。
- AI 輸出會轉成 `form_prefill`，降低使用者填 VM/LXC 申請單的門檻。

## 5. 老師情境評分

功能定位：

這是給老師在群組課程內使用的 AI 評分輔助。它不直接任意操作學生機器，而是先把評分表變成結構化項目，再產生受管的只讀收集腳本，最後根據腳本結果產生老師可讀的評分建議。

前端操作分頁：

- 評分表：上傳 `.docx` / `.pdf`，AI 解析成評分項目，可聊天修正並匯出 Excel。
- 收集腳本：由目前評分表產生 Python 收集腳本，經政策與 AI reviewer 檢查後才能核准。
- 腳本執行：選擇群組 VM/LXC，執行已核准腳本，顯示執行狀態與 AI 分析分數。

後端流程：

1. 老師上傳評分表，後端解析文字並呼叫 `analyze_rubric`。
2. AI 回傳結構化 rubric items，包含是否可自動檢查、偵測方式與 fallback。
3. 老師可用 `/rubric/chat` 與 AI 討論或修正評分項目。
4. 老師建立收集腳本時，AI 產生只讀 Python 腳本。
5. 腳本必須通過 `script_policy`、`script_quality_validator` 與 AI reviewer。
6. 核准後才能建立 script run，背景任務對目標 VM/LXC 執行。
7. 執行結果通過 JSON 驗證後，AI 將 evidence 對齊 rubric item，產生 5 分制評分建議與項目說明。

主要 API：

- `/api/v1/rubric/upload`
- `/api/v1/rubric/chat`
- `/api/v1/rubric/download-excel`
- `/api/v1/groups/{group_id}/judge/files/`
- `/api/v1/groups/{group_id}/judge/scripts/`
- `/api/v1/groups/{group_id}/judge/scripts/{script_id}/runs`

主要檔案：

- `backend/app/api/routes/rubric.py`
- `backend/app/api/routes/teacher_judge_files.py`
- `backend/app/api/routes/teacher_judge_scripts.py`
- `backend/app/ai/teacher_judge/service.py`
- `backend/app/ai/teacher_judge/script_artifact_service.py`
- `backend/app/ai/teacher_judge/script_executor_service.py`
- `backend/app/ai/teacher_judge/script_result_analysis_service.py`
- `frontend/src/features/ai-judge/components/AiJudgeManagementContent.tsx`

可強調重點：

- 老師仍保有主控權：AI 先做分析與建議，老師可修改、核准腳本、選擇執行目標。
- 腳本設計是只讀資料收集，限制不得刪除、修改、安裝、重啟、讀取敏感檔或對外傳送資料。
- AI 評分依據來自 script result 的 checks、errors、summary 與 metadata，不能憑空編造證據。

## 6. AI API 金鑰與 Proxy

功能定位：

讓使用者申請平台提供的 AI API 金鑰，審核通過後使用 `ccai_` 開頭的 key 呼叫 OpenAI-compatible chat completions。

申請與審核流程：

1. 使用者送出用途、金鑰名稱與使用期限。
2. Reviewer / Admin 查看申請並核准或拒絕。
3. 核准後後端產生 `ccai_` key，使用加密欄位儲存，只保留 prefix 做查詢。
4. 使用者可查看自己的 credentials、旋轉金鑰、刪除金鑰。
5. Admin 可查看全站 credentials，包含 active、expired、revoked 狀態。

Proxy 呼叫流程：

1. 使用者用 `Authorization: Bearer ccai_xxx` 呼叫 `/api/v1/ai-proxy/chat/completions`。
2. 後端解密比對 key、檢查過期與使用者狀態。
3. Redis sliding window 做 rate limit。
4. 後端用系統上游 key 呼叫 vLLM Gateway。
5. 支援非串流與 streaming response。
6. 寫入 `ai_api_usage`，記錄模型、tokens、耗時、狀態與錯誤。

主要檔案：

- `backend/app/api/routes/ai_api.py`
- `backend/app/api/routes/ai_proxy.py`
- `backend/app/api/deps/ai_api_key.py`
- `backend/app/services/llm_gateway/ai_gateway_service.py`
- `backend/app/models/ai_api_request.py`
- `backend/app/models/ai_api_credential.py`
- `backend/app/models/ai_api_usage.py`
- `frontend/src/services/aiApi.ts`

可強調重點：

- 前端一般 JWT 與外部 AI API key 是兩種不同身份邊界。
- 使用者拿到的是平台代理 key，不是 vLLM 上游服務 key。
- 金鑰有審核、期限、撤銷、旋轉、限流與用量紀錄。

## 7. AI 監控與治理

管理端可查看：

- Proxy 呼叫量、tokens、成功/失敗、平均延遲。
- Template 推薦呼叫量與 tokens。
- 活躍使用者數與使用模型列表。
- 依使用者、模型、狀態、日期篩選呼叫紀錄。

主要檔案：

- `backend/app/api/routes/ai_monitoring.py`
- `backend/app/services/llm_gateway/ai_gateway_service.py`
- `backend/app/models/ai_template_call_log.py`
- `frontend/src/services/aiMonitoring.ts`
- `frontend/src/routes/_layout/admin.ai-monitoring.tsx`
- `frontend/src/routes/_layout/admin.ai-management.tsx`

可強調重點：

- 監控把「外部 API proxy 用量」與「內建模板推薦用量」分開記錄，再做全局彙總。
- 可以支援日後成本控管、濫用追蹤、模型效能觀察。

## 8. 報告講稿建議順序

1. 先講 AI 導入目的：降低操作門檻、協助申請決策、支援老師評分、控管模型資源。
2. 再講共同架構：前端不直接碰模型，後端統一代理 vLLM，設定集中管理。
3. 接著用三個使用者故事：
   - 學生不知道去哪裡，使用 AI 導覽。
   - 學生要申請服務，使用模板推薦自動預填。
   - 老師要批改情境作業，使用 AI 評分管理產生腳本與評分建議。
4. 最後講治理：金鑰申請審核、限流、用量紀錄、admin 監控。

## 9. 待確認整合點

- `frontend/src/services/aiMonitoring.ts` 的 proxy 個人用量路徑寫為 `/api/v1/ai-api/usage/proxy/my`，但目前後端可見的 proxy 個人用量 API 是 `/api/v1/ai-proxy/usage/my`，且該路由走 AI API key 驗證。若報告要展示個人 proxy 用量頁，建議先確認此路徑是否已有其他未掃到的轉接或仍待整合。
- `backend/config/system-ai.example.json` 目前只列出 `template_recommendation` 與 `pve_advisor`，實際 `system-ai.json` 另有 `pve_log` 與 `teacher_judge`。若要把設定當正式交付文件，建議後續同步 example。
