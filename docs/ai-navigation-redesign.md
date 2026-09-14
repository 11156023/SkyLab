# SkyLab 導覽 AI 設計優化

> 文件狀態：設計草案  
> 本次範圍：只定義設計，不修改現有程式  
> 核心決策：移除 AI 導覽、跳頁與多步驟帶路能力，將 AI 改造成畫面情境說明助手；
> 「找頁面」以非 AI 的檢索形式保留（§15）

## 1. 背景

目前導覽 AI 同時承擔多種責任：

- 從自然語言判斷使用者想做什麼。
- 決定要前往哪個頁面。
- 提供跨頁面的操作流程。
- 顯示目前步驟與後續步驟。
- 解釋欄位、狀態及錯誤。
- 在部分情境中協助產生申請配置。

這些責任混在同一個對話介面後，容易出現以下問題：

- 聊天內容變成另一套導覽列，與既有側欄、頁籤及按鈕重複。
- `NavigationStep.detail` 持續膨脹，操作指示、欄位知識與注意事項難以維護。
- 使用 `current_path` 推測 `active_step`，無法準確反映表單或後端的真實狀態。
- 模型可能描述不存在的按鈕、路徑或下一步。
- 每次帶入完整路由、流程、表單或畫面，增加 token、延遲與幻覺風險。
- 使用者真正卡住時，AI 反而不知道目前欄位值、驗證錯誤或按鈕停用原因。

本次設計不再強化多步驟導覽，而是移除 AI 導覽責任，讓 AI 專注於理解與解釋使用者當下看見的畫面。

## 2. 產品決策

### 2.1 移除的能力

AI 不再負責：

- 自動跳轉頁面。
- 推薦或產生路徑。
- 回傳 `navigate`、`suggest`、`clarify`、`guide` 等導覽動作。
- 顯示或管理 `NavigationStep`。
- 判斷目前位於流程的第幾步。
- 產生 Next、Back 或跨頁 walkthrough。
- 依對話自行決定下一個操作。
- 將「申請機器」、「發布服務」等完整流程塞入聊天內容。

### 2.2 保留的導覽

一般產品導覽仍由 UI 本身負責，包括：

- 側欄、頁籤、麵包屑及頁面按鈕。
- React Router 與既有權限控制。
- 頁面內的 inline hint、validation message 及必要的 tooltip。
- 由產品明確定義的入口與操作狀態。

這些能力不是 AI 功能，也不需要模型參與。

### 2.3 AI 的新定位

AI 改為「畫面情境說明助手」，只回答目前畫面相關問題：

- 這個欄位是什麼？
- 這裡應該填什麼？
- 為什麼按鈕不能按？
- 這個錯誤代表什麼？
- 這張圖片或圖表在表達什麼？
- 這一頁的用途是什麼？
- 目前畫面有哪些值得注意的狀態？

AI 可以理解、描述、解釋及診斷 UI，但不擁有工作流程的決定權。

## 3. 設計原則

1. **UI 是操作真相**：可用功能、按鈕狀態、欄位限制及權限由前後端決定。
2. **AI 只做解釋**：AI 不產生路徑、按鈕、完成條件或下一步。
3. **先取資料，再推理**：程式先選出最小必要情境，模型只負責整理與說明。
4. **預設不傳圖片**：只有視覺問題才擷取圖片，且優先擷取單一元素。
5. **不直接傳完整 DOM**：前端先把畫面轉成穩定、精簡的語意資料。
6. **不知道就明說**：情境不足時不得用常識補造產品行為。
7. **答案就地且簡短**：先回答使用者當下問題，不主動展開整套教學。

兩條必須寫入實作規範的原則：

> Never send the whole screen unless the question requires the whole screen.

> Retrieve first, reason second.

## 4. 目標體驗

### 欄位說明

使用者：「GPU 是什麼？」

AI：「GPU 是這台機器的圖形運算資源。若要執行 CUDA、PyTorch 或影像模型，可選擇支援 NVIDIA GPU 的規格。」

### 狀態說明

使用者：「為什麼送不出去？」

AI：「『送出申請』目前為停用狀態，因為申請理由至少需要 10 個字。你目前填寫的內容尚未達到限制。」

### 圖片說明

使用者：「這張圖在表示什麼？」

AI：「這是目前 GPU 使用率圖表。畫面顯示目前使用率為 82%；圖表旁的中繼資料將它標記為 GPU utilization。」

### 頁面說明

使用者：「這一頁在做什麼？」

AI：「這是機器申請頁，用來選擇資源類型、硬體配置、系統環境與使用時段，最後填寫理由並送出申請。」

### 不允許的回答

AI 不應回答：

> 下一步請前往防火牆頁面，再點擊右上角的新增規則。

除非 UI 已經直接顯示這段說明，否則 AI 不得自行決定下一步、路徑或按鈕位置。

## 5. 整體架構

```text
User Question
      │
      ▼
Intent Router
      │
      ▼
Context Resolver
      ├── Static Surface Registry
      ├── Dynamic State Registry
      └── Visual Capture（按需）
      │
      ▼
Context Compressor
      │
      ▼
Explanation Model
      │
      ▼
Answer
```

各元件責任如下：

| 元件 | 責任 | 是否使用大型模型 |
| --- | --- | --- |
| Intent Router | 判斷問題屬於欄位、狀態、視覺或頁面說明 | 預設規則式；模糊時才用小模型 |
| Surface Registry | 提供頁面與元件的靜態語意資料 | 否 |
| Dynamic State Registry | 提供目前值、錯誤、停用原因及後端狀態 | 否 |
| Visual Capture | 擷取指定元素、區域或 viewport | 否 |
| Context Resolver | 依 intent 與 target 選出必要資料 | 否 |
| Context Compressor | 移除無關及敏感資料，控制輸入大小 | 否 |
| Explanation Model | 依選定情境產生簡短說明 | 是 |

## 6. Intent 分類

第一版只支援四種 intent：

| Intent | 使用者問題 | 所需情境 | 是否需要圖片 |
| --- | --- | --- | --- |
| `field_help` | 「這格要填什麼？」 | target metadata、限制、選項 | 否 |
| `validation_help` | 「為什麼不能送？」 | errors、相關欄位、action state | 否 |
| `visual_help` | 「這張圖是什麼？」 | target metadata、附近文字、局部截圖 | 是 |
| `page_overview` | 「這頁在做什麼？」 | page summary、sections、少量動態狀態 | 預設否 |

可先使用關鍵詞與目前作用中的元件分類：

```text
「不能送、錯誤、紅色、失敗」 → validation_help
「圖片、圖表、icon、看起來」 → visual_help
「這格、欄位、怎麼填」       → field_help
「這頁、整頁、做什麼」       → page_overview
```

規則無法確定時才使用小模型分類；分類模型不得直接產生使用者答案。

## 7. Surface Registry

既有概念不應限制在表單，因此採用 `Surface Registry`，而不是 `Form Registry`。

Surface 可以代表：

- 表單。
- Dashboard。
- 圖片或圖表。
- 資源卡片。
- 表格。
- Modal 或 Dialog。
- Error banner。
- GPU topology。
- Reverse proxy 規則。

建議介面：

```ts
registerSurface(surfaceId, {
  getSummary,
  getVisibleElements,
  getElement,
  getErrors,
  getActionStates,
  getBackendState,
  getVisualContext,
})
```

Registry 回傳產品定義的語意資料，不回傳未處理的 HTML。

這個 registry 活在瀏覽器裡：`getErrors()`、`getActionStates()` 讀的是元件當下的
狀態，後端呼叫不到。因此 element 的靜態描述（`label`、`help`、`constraints`）
另有一份定義在後端，兩者以 element `id` 對應。兩半情境如何合併與把關見 §14.1。

## 8. Screen Context Schema

```json
{
  "surface": {
    "id": "request-form",
    "path": "/my-requests",
    "purpose": "申請新的運算資源",
    "sections": [
      "資源類型",
      "硬體配置",
      "系統環境",
      "使用時段",
      "申請理由"
    ]
  },
  "active_target": "request.gpu",
  "elements": [
    {
      "id": "request.gpu",
      "role": "select",
      "label": "GPU",
      "value": "RTX 4090",
      "disabled": false,
      "help": "選擇工作負載需要的 GPU",
      "constraints": [],
      "bounds": {
        "x": 420,
        "y": 215,
        "width": 280,
        "height": 42
      }
    },
    {
      "id": "request.reason",
      "role": "textarea",
      "label": "申請理由",
      "value": "跑 AI",
      "error": "申請理由至少 10 字"
    },
    {
      "id": "request.submit",
      "role": "button",
      "label": "送出申請",
      "disabled": true,
      "disabled_reason": "request.reason 未通過驗證"
    }
  ]
}
```

### 欄位規則

- `id` 必須穩定，不可使用 CSS class 或動態 DOM selector。
- `label`、`help` 與 `constraints` 由產品定義，不由模型猜測。
- `value` 僅提供回答問題所需的內容。
- `error` 使用實際驗證結果。
- `disabled_reason` 由 UI 或後端規則提供。
- `bounds` 只用於局部圖片擷取或在畫面標示目標。
- 密碼、token、API key、Cookie 及不相關個資不得進入 context。

`bounds` 由前端在送出當下量測版面產生，捲動、resize、縮放或版面重排後即失效。
它與 `context_version` 綁在一起（§14.2）：版本不符時不得用於擷取或標示，必須
重新取得。後端不保存 `bounds`。

## 9. 圖片與圖表

圖片不能只依賴 OCR 或整頁 screenshot，應同時提供結構化中繼資料：

```json
{
  "id": "dashboard.gpu-utilization",
  "type": "chart",
  "label": "GPU 使用率",
  "semantic_role": "gpu_utilization",
  "context": {
    "current_percent": 82,
    "time_range": "最近 30 分鐘"
  },
  "bounds": {
    "x": 380,
    "y": 180,
    "width": 640,
    "height": 320
  }
}
```

視覺擷取順序：

1. Element crop。
2. Region crop。
3. Viewport screenshot。
4. Full-page screenshot。

只有前一層不足以回答時才逐步擴大。完整頁面截圖是最後手段。

AI 回答時應區分：

- **畫面直接看見的內容**：例如顏色、文字、圖形走勢。
- **應用程式提供的資料**：例如實際數值、時間範圍、資源 ID。

不得單憑外觀推測隱藏狀態、權限或後端結果。

擷取本身是前端工作，而且不是零成本：元素與區域裁切需要瀏覽器端的繪製方案，
`bounds` 也會因捲動而失效。因此 `visual_help` 排在實作順序的最後（§19），
先讓兩種純文字 intent 走完再評估要不要投入。

## 10. Active Target

「這個」、「那張圖」、「這裡」必須依賴穩定的 `active_target`，不能把整頁交給模型猜。

可接受的 target 來源：

- 使用者點擊的元件。
- 目前 focus 的表單欄位。
- 使用者選取的圖片或圖表。
- 對話按鈕明確綁定的元件。
- 目前開啟的 Modal 或錯誤訊息。

```json
{
  "surface_id": "request-form",
  "active_target": "request.gpu",
  "question": "這個是什麼？"
}
```

不建議只依賴 mouse hover，因為觸控裝置沒有穩定的 hover，游標移動也容易造成 target 漂移。

## 11. Progressive Context Expansion

Context Resolver 依以下層級找資料：

```text
Level 0：active target
Level 1：current component
Level 2：current section
Level 3：current viewport
Level 4：full surface
```

每一層都先判斷資料是否足夠；只有不足時才擴大下一層。

範例：

```py
if intent == "field_help":
    context = get_element(active_target)

elif intent == "validation_help":
    context = {
        "errors": get_errors(),
        "relevant_elements": get_error_elements(),
        "action_states": get_action_states(),
    }

elif intent == "visual_help":
    context = {
        "element": get_element(active_target),
        "nearby_text": get_nearby_text(active_target),
        "image": capture_element(active_target),
    }

elif intent == "page_overview":
    context = {
        "summary": get_surface_summary(),
        "dynamic_state": get_relevant_surface_state(),
    }
```

## 12. Prompt 架構

Prompt 拆成四個部分：

```text
System Prompt
+ Task Prompt
+ Selected Context
+ User Question
```

### 12.1 System Prompt

```text
You are the contextual help assistant for SkyLab.

Rules:
- Explain only information present in the supplied UI context.
- Do not invent buttons, fields, paths, states, permissions, or workflow steps.
- Do not navigate the user or decide what their next action should be.
- Treat structured application state as authoritative.
- For visual input, distinguish visible observations from application metadata.
- If the information is insufficient, state what cannot be determined.
- Answer concisely in the user's language.
```

### 12.2 `field_help`

```text
Explain the selected UI element.

Include only:
1. What it means.
2. What value the user may enter or select.
3. Constraints explicitly supplied in context.

Do not explain unrelated parts of the page.
```

### 12.3 `validation_help`

```text
Explain why the user's current action is blocked.

Use only supplied validation errors and UI state.
State:
1. What is blocking the action.
2. Which element is affected.
3. How the supplied constraint can be satisfied.
```

### 12.4 `visual_help`

```text
Explain the selected visual element using the image and its metadata.

Clearly distinguish:
- what is visibly shown;
- what application metadata states.

Do not infer hidden application state from appearance alone.
```

### 12.5 `page_overview`

```text
Briefly explain the purpose of the current page.

Describe its main sections and relevant current state.
Do not generate navigation or a workflow.
```

## 13. Token 與延遲控制

一般文字說明的建議輸入量：

| 區塊 | 建議大小 |
| --- | ---: |
| System rules | 100–250 tokens |
| Task instruction | 50–150 tokens |
| Selected UI context | 100–500 tokens |
| User question | 10–100 tokens |
| 合計 | 通常低於 1,000 tokens |

控制方式：

- 頁面用途與 section 清單使用靜態定義並快取。
- 動態情境只放與問題直接相關的欄位及狀態。
- 不傳完整 DOM、完整表單、完整路由表或歷史 workflow。
- 對話歷史先摘要，只保留解決指代所需的最近內容。
- 圖片採 lazy load；非視覺問題不呼叫 vision model。
- 回答設定短輸出上限，預設不產生長篇教學。
- 若結構化資料已可直接產生固定答案，可不呼叫模型。

## 14. API 草案

### 14.1 情境從哪裡來

Surface Registry（§7）活在瀏覽器裡，後端呼叫不到。因此情境必然分成兩半：

| 來源 | 內容 | 誰持有 |
| --- | --- | --- |
| 靜態 | surface 用途、section 清單、element 的 `role` / `label` / `help` / `constraints` | 後端，按 `surface_id` 定義並快取 |
| 動態 | `value`、`error`、`disabled`、`disabled_reason`、`bounds` | 前端，只有瀏覽器知道 |

動態的一半只能由前端送上來。`validation_help` 要用的 `request.reason.error` 與
`request.submit.disabled_reason` 都只存在於元件狀態，後端手上只有規則，沒有
「這個使用者此刻填了什麼」。

所以安全性不靠「拒收前端資料」達成——在這個架構下做不到——而是靠**後端按
`surface_id` 對照已知 schema 做白名單驗證**：

- 只接受該 surface 已宣告的 element `id`，未宣告的一律丟棄。
- 每個欄位只接受宣告過的型別與長度上限。
- `role`、`label`、`help`、`constraints` 一律以**後端定義為準**，忽略前端送來的
  同名欄位，避免這些會左右模型的描述被竄改。
- 宣告為敏感的 element（密碼、金鑰、憑證）連 `value` 都不收。

一句話：前端能決定「填了什麼」，不能決定「這格是什麼」。

### 14.2 Request

```json
POST /api/v1/ai/contextual-help/explain

{
  "question": "為什麼不能送出？",
  "surface_id": "request-form",
  "active_target": "request.submit",
  "context_version": 12,
  "state": {
    "request.reason": {
      "value": "跑 AI",
      "error": "申請理由至少 10 字"
    },
    "request.submit": {
      "disabled": true,
      "disabled_reason": "request.reason 未通過驗證"
    }
  }
}
```

`state` 只帶與問題相關的 element，由前端的 Context Resolver（§11）依 intent 挑選，
不是整張表單。後端仍會再過濾一次：前端負責減量、後端負責把關，兩者目的不同，
不能互相取代。

`context_version` 由前端在每次畫面狀態改變時遞增。後端不保存它，只原樣回傳，
讓前端能丟棄過期的回應——使用者在等待期間又改了欄位時，舊答案不該蓋上去。

### 14.3 Response

```json
{
  "intent": "validation_help",
  "answer": "送出按鈕目前停用，因為申請理由至少需要 10 個字。",
  "target": "request.reason",
  "grounded_in": [
    "request.reason.error",
    "request.submit.disabled_reason"
  ],
  "used_visual": false,
  "context_level": 1,
  "context_version": 12
}
```

Response 不包含路徑、跳頁動作、流程 ID 或下一步。

## 15. 頁面檢索（非 AI 能力）

移除 AI 導覽之後，「這個功能在哪一頁」會退回給側欄與麵包屑。以目前 28 條路由、
側欄分成七八個群組的規模，這對第一次使用的人並不自明。把它整個丟掉是有代價的，
文件不應假裝沒有。

現有 `catalog.py` 的路由資料，加上 `service.py` 裡的 `_keyword_fallback`，正好是
這個問題的答案，而且**不呼叫模型**：純關鍵字比對，帶權限過濾，直接回連結。延遲
近乎零，沒有幻覺風險，程式已經寫好而且有測試。

因此本設計把它們從導覽模組**移出並保留為獨立的檢索端點**，而不是連同 AI 導覽
一起退場：

- 定位是搜尋，不是 AI 能力，不進 contextual-help 的 intent 分類。
- 不呼叫模型，不讀畫面情境，不產生步驟。
- 輸入是關鍵字，輸出是候選頁面連結，由使用者自己點。
- 沿用既有權限分級（`all` / `staff` / `admin`），使用者看不到沒權限的頁面。

這條界線讓兩件事互不相欠：檢索不需要理解，解釋不需要指路。

> 若產品決定「找頁面」完全交給側欄，本節可整節刪除，`catalog.py` 一併退場。
> 這是產品取捨，不是技術限制。

## 16. 配置推薦的銜接

機器配置推薦維持為獨立功能（§18），但它現在**不是**獨立的——`intake.py` 建立在
`flows.py` 之上：

- 依賴 `INTAKE_FLOW_ID`、`all_flows`、`find_flow_by_id` 與 `public_steps`。
- `IntakeState` 每一輪都回傳 `flow_id`、`flow_title` 與 `steps`。
- 前端據此把「規劃配置」定位成「申請一台機器」流程中的一步，配置產生後才接得回
  後續。

`NavigationFlow` 退場後，配置推薦會從「流程中的一步」變成一次孤立的問答：問完給
一份配置，然後沒有下文。這會改變這個功能的使用感受，不是留到實作期再說的細節。

本設計採取的方向是**把銜接交還給表單本身，而不是交給聊天**：

- `intake.py` 移除對 `flows` 的依賴，`IntakeState` 不再回傳 `flow_id`、`flow_title`
  或 `steps`。
- 問答結束後助手只做一件事：把配置填進申請表單，並說明填了什麼。
- 「填完之後還要做什麼」由表單自己表達——送出按鈕、驗證訊息、送出後的狀態頁，
  這些本來就是 UI 的責任（§2.2）。
- 使用者在表單上不確定時，改用 contextual-help 問「這格是什麼」「為什麼不能送」。

也就是原本靠聊天維持的流程感，改由「配置已經填好、按鈕就在那裡」承接。

`flows.py` 裡另有一類知識不屬於任何單一畫面，例如「要對外公開服務，得先設反向
代理再開防火牆，否則網址連不上」。這種跨頁順序 Surface Registry 裝不下，退場時
不應直接刪除，而應轉寫成相關頁面上的靜態 inline hint。

## 17. 安全與隱私

- 後端對 context 欄位採 allowlist，按 `surface_id` 對照已宣告的 schema 驗證，
  不直接信任前端送入的 DOM 或文字（§14.1）。
- 自動遮蔽密碼、API key、token、Cookie、連線憑證及個資。
- 圖片擷取前排除密碼欄、金鑰區塊及非必要的使用者資料。
- 模型輸出只作說明文字，不直接執行 UI action 或後端 mutation。
- 權限、可用狀態及錯誤原因以後端與 UI 結構化資料為準。
- 記錄 intent、context 欄位名稱、token、延遲及結果狀態；避免記錄敏感欄位值或原始截圖。
- 情境版本過期時要求重新取得，不用舊畫面資料解釋新狀態。

## 18. 舊功能調整範圍

未來實作時，現有導覽模組中的以下概念應退場：

- `NavigationAction` 的 `navigate`、`suggest`、`clarify`、`guide`。
- `NavigationTarget`。
- `NavigationStepPublic`。
- `NavigationFlow` 與 `active_step`。
- 以 `current_path` 推算使用者進度。
- 導覽 prompt：route catalog 與 flow catalog 都不再進入模型輸入。
- 前端依模型回傳值自動跳頁或顯示流程步驟。

不隨之退場的兩項：

- `catalog.py` 的路由資料與 `_keyword_fallback` 的比對邏輯改列為頁面檢索（§15），
  定位是搜尋而非 AI 能力。
- 機器配置推薦不是畫面說明能力，維持獨立功能，不併入 contextual-help API；它與
  流程的解耦見 §16。

影響範圍約 2,700 行：後端導覽模組 1,310 行、對應測試 488 行、前端約 900 行
（`AiFloatingChat.jsx` 與導覽服務及其測試，其中聊天殼層會保留）。既有測試涵蓋
路由目錄與 `App.jsx` 的一致性、流程展開與關鍵字後備；檢索保留下來的部分需要
接手對應的測試，不應一併刪除。

此次只記錄設計決策；是否刪除舊 API、保留相容期或進行資料遷移，另開實作計畫處理。

## 19. 建議實作順序

1. 定義 `ScreenContext`、`Surface` 與 `Element` schema，以及後端的 per-surface
   白名單（§14.1）。
2. 為一個頁面建立最小 `Surface Registry`。
3. 完成 `active_target` 與敏感資料過濾。
4. 實作 `field_help` 與 `validation_help`，先不加入圖片。
5. 加入 Context Resolver、大小限制及記錄指標。
6. 實作 `page_overview` 與靜態 page summary cache。
7. 把 `catalog.py` 與關鍵字比對移出導覽模組，改成頁面檢索端點（§15）。
8. 解除 `intake.py` 對 `flows` 的依賴，改由表單承接後續（§16）。
9. 最後加入 element crop 與 `visual_help`。
10. 驗證新助手後，再規劃舊導覽 API 的退場方式。

## 20. 第一版驗收標準

### 功能

- 能解釋指定欄位的用途與限制。
- 能根據真實 validation error 解釋按鈕停用原因。
- 能以靜態 page summary 解釋目前頁面。
- 能針對指定圖片或圖表進行局部視覺說明。
- 沒有 target 時能要求使用者選取元件，而不是猜測。

### 邊界

- Response 不回傳任何導覽 action、path、flow 或 step。
- AI 不自行創造按鈕、欄位、權限、狀態或工作流程。
- 非視覺問題不傳圖片。
- 不傳完整 DOM。
- 敏感欄位不進入 prompt 或 log。

### 效能

- 一般文字請求的模型輸入目標低於 1,000 tokens。
- `field_help` 與 `validation_help` 不呼叫 vision model。
- 可從紀錄中辨識 intent、context level、是否使用圖片、token 與延遲。

### 品質

- 回答內容可追溯至 `grounded_in` 指定的 context 欄位。
- context 不足時明確說明缺少什麼。
- 答案先處理目前問題，不主動輸出完整教學或下一步。
- 以固定的離線題組驗證：每題標註預期的 `grounded_in` 欄位與可接受答案，改動時
  比對回歸，不靠人工觀感判斷有沒有變差。
- 題組須包含「情境不足」的案例，確認助手會說不知道，而不是用常識補造產品行為。

## 21. 不在本次範圍

- 自動操作 UI。
- 自動填寫或送出表單。
- 跨頁 workflow engine。
- 新手導覽、tour、coach mark 與串成序列的 spotlight。
- 語音控制。
- 全站搜尋（跨資源內容）。§15 的頁面檢索只比對路由目錄，不屬此列。
- 模型自行讀取完整 DOM。
- 長時間保存畫面截圖或完整對話。

把這次回答對應的單一元素做視覺標示不在排除之列——那是 §8 `bounds` 的用途，範圍
限於「指出這個答案在講畫面的哪裡」，不串成序列，也不引導下一步。

## 22. 最終定義

這次優化不是把原有導覽 AI 做得更複雜，而是縮小它的權限與責任：

```text
原本：AI 找路、帶流程、解釋畫面

改為：UI 負責導覽與操作
      AI 只負責理解並解釋目前畫面
```

最終產品不再是「聊天機器人帶我操作」，而是「我對目前畫面有疑問時，能得到有依據、低延遲且不越權的說明」。
