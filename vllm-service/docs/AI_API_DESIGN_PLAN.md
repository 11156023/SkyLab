# vLLM Interface AI API 設計建議計劃書

更新日期：2026-06-13

## 1. 背景與目的

`vllm-service/` 目前已具備：

- 多模型 Gateway 路由
- OpenAI-compatible `/v1/chat/completions`、`/v1/completions`
- 相容既有前端/工具的 `/api/chat*` 端點
- 多模態圖片、文件、影片串流流程

現階段的主要問題已不在「能不能用」，而在：

1. 高併發下，請求完成順序不穩定，無法保證互動型請求的公平性與尾延遲。
2. `reasoning`、`response_format`、`structured output`、`priority` 等 AI API 參數雖然上游 vLLM 已有能力，但本服務的暴露方式仍不完整。
3. `/v1/*` 原生代理與 `/api/chat*` 包裝端點之間的參數語義不夠一致，容易造成功能可用但行為不一致。

本文件目的：

- 記錄目前觀察到的設計缺口
- 提出高併發與 API 參數設計的優化方向
- 建立後續可分階段落地的實作計劃

## 2. 現況觀察

### 2.1 併發與排程

目前 Gateway 的主路徑位於 `gateway/main.py`，核心行為為：

- 依 `model` alias 找到對應上游 vLLM instance
- 使用單一 `gateway_semaphore` 控制總 inflight
- 將請求直接轉發給上游 vLLM OpenAI server

目前限制：

1. Gateway 只有「全域限流」，沒有「每模型限流」。
2. Gateway 沒有「等待佇列策略」，只有先進入協程、先搶到 semaphore 的請求先走。
3. Gateway 沒有「互動型短請求」與「長串流請求」的隔離策略。
4. Gateway 沒有 request priority、queue timeout、queue metrics。
5. 完成順序主要由 vLLM 非同步排程、chunked prefill、KV cache 狀態與 prompt 長度共同決定，天然不保證 FIFO。

### 2.2 上游 vLLM 行為

從目前日誌可觀察到：

- vLLM 0.22.1 已啟用 asynchronous scheduling。
- chunked prefill 已啟用。
- Qwen3-14B-FP8 在 `24576` token 長度下，實際最大併發僅約 `6.74x`。
- `gpt-oss-20B` 有 reasoning parser 邊界初始化失敗與 stream parsing error。
- Qwen3 的預設 sampling 可能被模型自帶的 `generation_config.json` 覆蓋。

這代表：

1. `max_num_seqs` 不是實際公平承載上限。
2. 長 prompt 與長回覆請求會拉低互動型請求體感。
3. reasoning 類功能若沒有正確 parser/template/config 組合，會在 stream 模式特別脆弱。

### 2.3 API 參數暴露現況

目前服務存在兩層 API：

- 原生代理層：`/v1/*`
- 相容包裝層：`/api/chat*`

設計上應該遵守：

1. `/v1/*` 儘量做透明代理，避免任意改寫語義。
2. `/api/chat*` 可做包裝，但不能破壞 OpenAI-compatible 參數模型。

在本次修正前，`/api/chat*` 對 `extra_body` 的處理與 direct HTTP payload 層級不一致，容易讓 vLLM 特有參數失效。這已先修正，但仍需要文件化設計規範。

## 3. 設計目標

### 3.1 主要目標

1. 改善高併發時的公平性與 tail latency。
2. 明確定義標準 OpenAI 參數與 vLLM 擴充參數的暴露方式。
3. 讓 reasoning、structured output、response format、tool use 能在不同模型下穩定可控。
4. 建立可觀測、可驗證、可回退的演進路徑。

### 3.2 非目標

以下不是本計劃第一階段重點：

- 重新設計前端 UI
- 改寫成非 vLLM 推論架構
- 引入複雜分散式排程系統

## 4. 設計原則

1. 原生 `/v1/*` 優先透明。
2. 相容 `/api/chat*` 必須語義穩定。
3. 公平性優先於表面最大吞吐。
4. 每個模型的能力差異需要顯式管理，不能假設所有模型都支援同一組參數。
5. 能在 Gateway 解決的先在 Gateway 解決；需要上游 vLLM 配合的再接入 server-side knobs。

## 5. 建議架構

### 5.1 API 分層

建議將 AI API 明確分成三層：

#### A. Transparent Proxy Layer

用途：

- 完整代理上游 vLLM OpenAI-compatible API
- 保留 `messages`、`response_format`、`reasoning`、`tools`、`stream` 等語義

端點：

- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/completions`
- `POST /v1/responses`

設計規則：

- `extra_body` 若由 SDK 送入，Gateway 可做攤平正規化
- 不主動刪除標準欄位
- request/response header 可透傳 `X-Request-Id`

#### B. Compatibility Wrapper Layer

用途：

- 服務既有前端、工具、內部系統
- 允許簡化輸入，例如單一 `message` 欄位

端點：

- `/api/chat`
- `/api/chat/stream`
- `/api/chat/vision/stream`
- `/api/chat/document/stream`
- `/api/chat/video/*`

設計規則：

- 包裝層只負責組 prompt、預設值、檔案處理
- 除保留欄位外，其餘 OpenAI-compatible 參數應可 passthrough
- 不允許外部覆寫 `messages`、`stream` 等核心包裝欄位

#### C. Capability Declaration Layer

建議未來在 `models.json` 增加能力標記，例如：

```json
{
  "alias": "Qwen/Qwen3-14B-FP8",
  "capabilities": {
    "chat": true,
    "stream": true,
    "reasoning": true,
    "response_format_json_schema": true,
    "tool_use": true,
    "vision": false,
    "priority_scheduling": true
  }
}
```

用途：

- 對外文件化每模型能力
- Gateway 可做參數檢查與友善錯誤訊息
- 避免 client 對不支援模型送出不相容參數

### 5.2 高併發公平性設計

#### 現況問題

單一 `gateway_semaphore` 只能限制總 inflight，不能回答以下問題：

- 哪個模型先吃滿？
- 串流長請求是否會拖住互動型短請求？
- 同模型內是否需要 priority？

#### 建議改造

第一層：保留全域 inflight 上限

- 保護 Gateway 自身資源
- 避免連線池、記憶體與 worker 被打爆

第二層：新增 per-model inflight semaphore

- 每個 alias 各自限制最大併發
- 避免單一熱門模型吃掉所有 Gateway 名額

第三層：引入 queue class

建議至少切成：

- `interactive`
- `stream`
- `batch`

說明：

- `interactive`：短文字、非串流、低延遲優先
- `stream`：長回答、SSE
- `batch`：批量或背景任務

第四層：支援 request priority

建議規則：

- 只有在模型啟用 `SCHEDULING_POLICY=priority` 時，才接受/轉送 `priority`
- 預設 priority 為 `0`
- 互動型請求可給較高 priority
- batch 任務可給較低 priority

第五層：加入 queue timeout

用途：

- 避免請求無限制等待
- 當系統處於高載時，快速回應 429/503 比長時間懸掛更可控

#### 建議採用的 server-side knobs

本次已接入設定層的建議欄位：

- `SCHEDULING_POLICY`
- `MAX_NUM_PARTIAL_PREFILLS`
- `MAX_LONG_PARTIAL_PREFILLS`
- `LONG_PREFILL_TOKEN_THRESHOLD`
- `ENABLE_REQUEST_ID_HEADERS`

建議初始方向：

```env
SCHEDULING_POLICY=priority
MAX_NUM_PARTIAL_PREFILLS=4
MAX_LONG_PARTIAL_PREFILLS=1
LONG_PREFILL_TOKEN_THRESHOLD=4096
ENABLE_REQUEST_ID_HEADERS=true
```

注意：

- 這組配置偏互動延遲與公平性，不是純吞吐最大化
- 實際數值仍需依模型大小、GPU 顯存與平均 prompt 長度做壓測微調

### 5.3 Reasoning / Response Format / 額外參數設計

#### 標準參數

以下欄位應視為標準 OpenAI-compatible 欄位：

- `model`
- `messages`
- `max_tokens`
- `temperature`
- `top_p`
- `stream`
- `stream_options`
- `response_format`
- `tools`
- `tool_choice`
- `reasoning_effort`
- `metadata`
- `modalities`

處理原則：

- 若 client 送到 `/v1/*`，Gateway 不應任意改寫
- 若 client 送到 `/api/chat*`，則在保留包裝語義前提下允許 passthrough

#### vLLM 擴充參數

以下屬於 vLLM 或特定模型的擴充參數：

- `top_k`
- `min_p`
- `repetition_penalty`
- `priority`
- `structured_outputs`
- `chat_template_kwargs`
- 其他由 vLLM 接受的 extra fields

處理原則：

1. SDK 風格 `extra_body` 可接受。
2. Gateway 應攤平成 direct HTTP payload 再轉送上游。
3. 包裝層不應把 `extra_body` 當巢狀 payload 原封不動傳給 upstream。

#### Responses API

建議 reasoning 與 structured output 優先支持 `/v1/responses`，理由：

1. 新版 OpenAI-compatible 能力通常先在 responses 模式收斂。
2. reasoning output、response schema、tool orchestration 的語義較完整。
3. 對未來擴充較有彈性。

## 6. 模型別建議

### 6.1 gpt-oss-20B

目前觀察：

- `reasoning_parser=openai_gptoss`
- 有 reasoning token boundary 初始化失敗
- stream 過程出現多次 `HarmonyError`

建議：

1. 補充 `REASONING_CONFIG`，不要只依賴 parser 自動推斷。
2. 確認 chat template 是否與 parser 預期一致。
3. 在 reasoning 功能穩定前，優先用 non-stream 或 `/v1/responses` 驗證。
4. 對 reasoning/structured output 建立專用 smoke test。

### 6.2 Qwen3-14B-FP8

目前觀察：

- 已啟用 `tool_call_parser=qwen3_coder`
- 已啟用 `reasoning_parser=qwen3`
- 預設 sampling 可能被模型自帶 `generation_config.json` 覆蓋

建議：

1. 若要統一服務層預設值，設定 `GENERATION_CONFIG=vllm`。
2. 壓測時重點觀察長 prompt 對互動 latency 的影響。
3. 因最大實際長上下文併發只有約 `6.74x`，不要只看 `max_num_seqs=24` 做容量估算。

## 7. 分階段落地計劃

### Phase 0：基礎修正

狀態：已完成

內容：

- 修正 stream request 的 semaphore 持有生命週期
- 正規化 `extra_body` 為 direct payload
- 新增 `/v1/responses` 代理
- 將 scheduling / reasoning / generation 相關 vLLM server knobs 接入設定層

### Phase 1：模型內公平性

目標：

- 讓同一模型下短請求不要被長請求嚴重拖慢

工作項目：

1. 啟用 `SCHEDULING_POLICY=priority`
2. 導入 `priority` request 欄位與驗證
3. 調整 partial prefill 相關參數
4. 建立 queue timeout 與 overload 回應策略

驗收：

- 同時混跑短請求與長串流時，短請求 P95 顯著下降
- 系統高載時不出現大量長時間懸掛請求

### Phase 2：Gateway 隔離與觀測

目標：

- 讓熱門模型不拖累其他模型

工作項目：

1. 新增 per-model semaphore
2. 新增 queue class：interactive / stream / batch
3. 新增 metrics：
   - queue wait time
   - in-flight per model
   - request duration
   - timeout / rejection count
   - stream active count
4. 在 log 中加入 `X-Request-Id`

驗收：

- 單一模型高載時，其它模型仍能維持可接受延遲
- 能從 metrics 直接判讀瓶頸在 Gateway 還是 vLLM upstream

### Phase 3：能力聲明與參數治理

目標：

- 讓 client 與模型能力對齊，減少隱性不相容

工作項目：

1. `models.json` 擴充 `capabilities`
2. Gateway 依模型能力檢查參數
3. 文件化每模型支援矩陣
4. 建立 API compatibility test matrix

驗收：

- 不支援的參數在入口層即可被攔截並回傳清楚錯誤
- 不同模型的 reasoning / response_format 行為差異可預期

## 8. 建議測試策略

### 壓測場景

1. 短 prompt + 短回覆，測互動型延遲
2. 長 prompt + 長回覆，測長任務佔用
3. 混合負載，測公平性
4. stream 與 non-stream 混跑
5. priority 高低混跑

### 功能測試

1. `response_format=json_schema`
2. `reasoning_effort`
3. `structured_outputs`
4. `tools/tool_choice`
5. `/v1/chat/completions` 與 `/v1/responses` 一致性
6. `/api/chat*` passthrough 一致性

### 失敗測試

1. queue timeout
2. upstream timeout
3. 模型不支援參數
4. stream 中途中斷/取消

## 9. 風險與回退策略

主要風險：

1. 啟用 priority scheduling 後，若 queue class 設計不當，可能造成低優先請求飢餓。
2. partial prefill 參數過度偏 fairness，可能降低總吞吐。
3. reasoning parser / chat template / structured output 若組合不完整，容易出現模型特定錯誤。

回退策略：

1. `SCHEDULING_POLICY` 可先保留 `fcfs`
2. partial prefill 相關設定可逐步打開
3. reasoning 功能先在單模型 smoke test 通過後再推到 Gateway
4. `/v1/responses` 可先標示為 beta 入口

## 10. 建議後續執行順序

優先順序建議如下：

1. 先做 Phase 1，優先改善同模型內公平性。
2. 再做 Phase 2，處理跨模型資源隔離與可觀測性。
3. 最後做 Phase 3，把能力聲明、參數治理與文件矩陣補齊。

## 11. 本文件對應實作檔案

- `vllm-service/gateway/main.py`
- `vllm-service/config/settings.py`
- `vllm-service/config/multi_model.py`
- `vllm-service/models.json`
- `vllm-service/.env.example`

本文件作為 `vllm-service` AI API 與高併發設計優化的正式紀錄與後續 roadmap 依據。
