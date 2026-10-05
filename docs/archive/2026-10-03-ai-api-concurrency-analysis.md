# Campus-Cloud AI API 併發故障分析與修正定案

> 專案：`young878787/Campus-Cloud`  
> 範圍：Campus AI API / FastAPI / SQLAlchemy / PgBouncer / Redis / LiteLLM / vLLM  
> 基準：`main`，commit `0ab1e1945e44a61b16b3bf30ad51d35d9eff39db`  
> 日期：2026-10-03  
> 狀態：**P0／P1／P2 程式碼與 regression tests 已完成；事故 Root Cause 尚待可控慢上游與實機監控資料完成驗證**

---

## 1. 定案結論

修正前，多人同時呼叫 Campus AI API 時，最可信的級聯故障鏈是：

```text
ccai_* Request burst
  -> AI API credential / user 查詢開啟 DB transaction
  -> transaction 未在查詢後結束
  -> Request 等待 Redis、LiteLLM、vLLM 與完整生成
  -> SQLAlchemy connection 長時間保持 checked out
  -> PgBouncer transaction pool 的 server connection 被長時間佔用
  -> 其他 Campus API 與 worker 開始等待 DB
  -> AI usage accounting 又在 async 路徑同步 commit
  -> 單一 FastAPI event loop 可能被 DB 等待阻塞
  -> Backend 全體延遲、timeout 或健康檢查失敗
```

這不是「多人共用同一把 API Key，導致 credential row lock」；目前認證只有普通
`SELECT`，沒有 `SELECT ... FOR UPDATE`，每次 usage 也是新增獨立資料列。

精確根因應描述為：

> **修正前，AI credential 認證所開啟的讀取 transaction 會跨越長時間 LLM I/O；推論延遲把 DB connection lifetime 一起拉長，再由同步 usage commit 與單一 event loop 放大為全站級聯故障。**

必須區分：

- `Session` 物件仍存在，不一定有問題。
- `Session` 仍在 transaction 且持有 connection，才是本事故的關鍵。
- 只要查詢後結束 read transaction，即使 request-scoped `Session` 稍後才 close，也不會在等待模型時佔住 DB connection。

---

## 2. 已修正、仍存在與待驗證項目

| 項目 | 判定 | 現況 |
| --- | --- | --- |
| 一般 JWT 認證後釋放 DB transaction | **已修正** | `get_current_user()` 已在線程池內呼叫 `end_read_transaction()` |
| `ccai_*` AI API 認證後釋放 transaction | **已修正** | 驗證完成後呼叫 `end_read_transaction()`，再回傳已載入 ORM 物件 |
| 同一把 Key 的 credential row lock | **未發現** | 沒有 `FOR UPDATE`，也沒有每請求更新 credential |
| 非串流生成期間持有認證 transaction | **已修正** | generation route 不再把 request session 傳入 relay；認證 transaction 在上游 I/O 前結束 |
| 串流期間持有認證 transaction | **已修正** | request-scoped session 仍存活，但已無 active transaction，也不持有 connection |
| Usage 使用獨立短 session | **已修正** | streaming、non-streaming 與 upstream error 共用同一個短 session helper |
| async route 直接執行同步 usage commit | **已修正** | 所有 proxy usage DB I/O 均經 `run_in_threadpool()` 執行 |
| AI in-flight admission queue | **已修正** | 每 process 固定 20 active、40 waiting、30 秒等待；滿載或逾時回 `503` |
| Credential limit 搭配 user bucket | **已修正** | Redis key 已收斂為 `credential:<credential_id>` |
| Relay 共用 HTTP connection pool | **已修正** | generation／models 共用 20 connections 的 lifespan client |
| Backend/LiteLLM/vLLM 基本監控 | **已存在** | Grafana 已有 request、latency、TTFT、queue、KV cache 等圖表 |
| PgBouncer waiting 與 idle transaction 儀表 | **未完整建立** | 仍需 `SHOW POOLS` 與 `pg_stat_activity` 現場資料 |
| 實機事故 Root Cause | **未完成驗證** | 尚無同一輪壓測的 DB、PgBouncer、Backend、LiteLLM、vLLM 對時資料 |

---

## 3. 已完成的相關修正

### 3.1 一般 JWT 認證已提前歸還連線

`backend/app/core/db.py` 已有：

```python
def end_read_transaction(session: Session) -> None:
    ...
```

它會在 session 沒有 pending write 時暫時關閉 `expire_on_commit`，完成 commit，讓
transaction 結束並把 connection 還回 pool，同時保留已載入 ORM 欄位可讀。

一般登入後的 dependency 已使用這個 helper：

```text
backend/app/api/deps/auth.py
  -> _load_user_and_release()
  -> session.get(User, ...)
  -> end_read_transaction(session)
```

同步 DB 查詢與 commit 也透過 `run_in_threadpool()` 執行，避免在 connection pool
壅塞時直接卡住 event loop。

### 3.2 AI API Key 已套用相同 transaction boundary

AI API 使用不同 dependency：

```text
backend/app/api/deps/ai_api_key.py
  -> get_current_user_by_ai_api_key()
  -> SELECT AIAPICredential
  -> session.get(User, ...)
  -> return user, credential
```

目前已在完整驗證 credential 與 user 後執行：

```python
end_read_transaction(session)
```

`end_read_transaction()` 執行時暫時停用 `expire_on_commit`，因此 route 後續仍可讀取
`user.id`、`credential.id` 與 `credential.rate_limit`，但查詢 connection 已歸還 pool。
驗證失敗路徑仍交由 request dependency cleanup rollback／close，不會誤 commit pending write。

### 3.3 監控基礎已經存在

目前 Grafana / Prometheus 已涵蓋：

- Backend request rate、5xx、P50/P95/P99、最慢 route、in-progress requests。
- Campus AI request rate、結果類別、端到端 latency、streaming TTFT、token throughput。
- vLLM running/waiting requests、KV cache、TTFT、inter-token latency、preemption。
- LiteLLM HTTP status、deployment success/failure、upstream latency、gateway overhead。
- PostgreSQL connection count、transaction rate、deadlock、cache hit rate。

下一輪不需要重新建立整套監控；只需補齊 PgBouncer pool 狀態、PostgreSQL
`idle in transaction` 與各層相同時間軸的壓測標記。

---

## 4. 目前真實 Request 與 Session 生命週期

Generation routes 現在只直接宣告：

```python
user_and_credential: AIAPIUserDep
```

`AIAPIUserDep` 內部仍依賴 request-scoped `SessionDep`，但 credential/user 查詢完成後會
立即結束 read transaction。Relay 不再接收 session；usage helper 只接收已載入的
`user_id` 與 `credential_id`，不會跨 thread 使用 ORM session。

### 4.1 非串流

```text
Request start
  -> get_db(): Session A
  -> credential SELECT             # transaction A 開始
  -> user SELECT
  -> end_read_transaction()        # transaction A 結束、connection 歸還
  -> Redis rate limit
  -> bounded admission queue
  -> shared httpx.AsyncClient
  -> await LiteLLM / vLLM
  -> await 完整 response body
  -> run_in_threadpool()
       -> Session B INSERT usage / commit / close
  -> response
  -> dependency cleanup
```

因此模型耗時不再延長 credential transaction；request-scoped Session A 雖仍存在，等待
上游期間沒有 active transaction，也不持有 DB connection。

### 4.2 串流

目前 FastAPI 0.141.1 的 request-scoped `yield` dependency 流程是：

```text
建立 request dependency exit stack
  -> 執行 route，取得 StreamingResponse
  -> 完整 await response(scope, receive, send)
  -> stream 結束
  -> 最後才退出 request dependency stack
```

修正後流程是：

```text
Session A credential/user SELECT   # transaction A 開始
  -> end_read_transaction()        # transaction A 結束、connection 歸還
  -> route 回傳 StreamingResponse
  -> Session A 仍由 request dependency 持有，但沒有 active transaction
  -> stream 數十秒
  -> stream finally
       -> run_in_threadpool()
            -> 建立 Session B
            -> INSERT usage / commit
            -> 關閉 Session B
  -> 完整 response 結束
  -> 才關閉 Session A
```

串流期間不再佔用 credential query 的 connection，stream 結尾只有短生命週期的 usage
transaction。若 pool 本身已壅塞，usage worker 仍可能等待 connection，但不會直接阻塞
FastAPI event loop，且 accounting failure 不會改寫已成功的模型 response。

---

## 5. Connection pool 邊界

### 5.1 SQLAlchemy client pool

目前單一 backend process 的 engine：

```text
pool_size = 10
max_overflow = 40
最大 checked-out client connections = 50
```

這可降低應用程式太早出現 `QueuePool limit ... reached`，但沒有解決長 transaction。

### 5.2 PgBouncer server pool

目前設定：

```text
POOL_MODE = transaction
DEFAULT_POOL_SIZE = 25
RESERVE_POOL_SIZE = 10
RESERVE_POOL_TIMEOUT = 3
MAX_CLIENT_CONN = 500
MAX_DB_CONNECTIONS = 150
```

`MAX_CLIENT_CONN=500` 只代表 PgBouncer 可接受較多 client connections，不等於有 500
條 PostgreSQL server connections。正常 server pool 仍以 25 為主，等待超過門檻才
使用 reserve 10。

Backend 與 worker 都透過 PgBouncer，且使用同一 DB/user 時會競爭同一 pool。因此修正前
AI request 長時間持有 transaction，不只影響其他 HTTP API，也可能影響 background worker。

### 5.3 為何不是先調大 pool

如果架構仍然是：

```text
LLM request lifetime = DB transaction lifetime
```

調大 SQLAlchemy、PgBouncer 或 PostgreSQL 連線數只會提高下一次出現 starvation 的
門檻，並增加資料庫同時承受的工作量。應先縮短 transaction，再依量測調整容量。

---

## 6. 放大因素與修正狀態

### 6.1 同步 usage commit 阻塞 event loop（已修正）

`relay_generation()` 與 `stream_upstream_response()` 都是 async 路徑；usage 現在統一經：

```python
await run_in_threadpool(record_usage_safely, ...)
```

同步 helper 會自行建立 `Session(engine)`、寫入、commit 並關閉。若 PgBouncer 或
PostgreSQL 正在等待，等待發生在 worker thread；event loop 仍可處理 liveness、stream
forwarding 與其他 async request。

### 6.2 RPM 不是 in-flight limit（已補 admission queue）

目前 Campus 預設是 20 requests/minute，但 20 個 request 在同一毫秒進場仍可能全部
通過 sliding-window check，形成 concurrency 20。

LiteLLM model deployment 的 `rpm` 與 vLLM batching 仍只控制上游。Campus Backend 現在
另有 process-local 有界 admission queue：20 個 active slots、最多 40 個等待者、等待
30 秒；只有 queue 已滿或等待逾時才快速回 `503 server_busy` 與 `Retry-After: 5`。

### 6.3 Rate-limit identity 語意不一致（已修正）

目前已改為：

```text
limit value = credential.rate_limit
Redis bucket = credential.id
```

同一 user 的不同 Key 現在各自使用 `rate_limit:credential:<credential_id>`；舊的 user
bucket 不需 migration，會依原 TTL 自然淘汰。

### 6.4 每個 request 新建 AsyncClient（已修正）

generation 與 models 現在共用單一 `httpx.AsyncClient`，connection／keepalive 上限都與
active slots 同為 20。每個 request 只關閉 upstream response，application shutdown 才
由 lifespan 關閉 shared client。

LiteLLM / vLLM 在本事故中仍較像放大器：修正前模型 queue 越長，Campus request 越久，
未結束的 DB transaction 就越多；目前沒有證據顯示它們是第一個直接破壞 Campus DB 的元件。

---

## 7. 修正方案定案

### P0-A：AI credential 查詢後立即結束 read transaction（已完成）

在 `get_current_user_by_ai_api_key()` 完成 credential/user 驗證後，呼叫現有：

```python
end_read_transaction(session)
```

決策：

- 重用既有 helper，不新增另一套 session abstraction。
- 保留現有 public API、response schema、credential 驗證與 rate-limit 行為。
- 本階段不為形式上的純化新增 `AIIdentity` DTO；現有 helper 已能保留必要 ORM 欄位。
- invalid/revoked/expired/inactive 等失敗路徑仍由 dependency cleanup rollback/close。

目前生命週期：

```text
credential/user SELECT
  -> end_read_transaction()
  -> connection returned
  -> Redis / LiteLLM / vLLM
```

### P0-B：Usage 使用獨立短 session，並移出 event loop（已完成）

非串流與串流共用一個同步 helper：

```text
threadpool
  -> with Session(engine) as usage_session
  -> INSERT AIAPIUsage
  -> commit
  -> close
```

決策：

- 不再把 request session 傳進長時間 relay 後才寫 usage。
- async 路徑使用 `run_in_threadpool()` 或等價的既有 threadpool helper。
- 仍保留「usage 失敗不得覆蓋已成功模型回應」的現有契約。
- 第一階段不導入 ARQ queue，避免尚未定義 delivery、retry、duplicate 與 shutdown 語意時增加範圍。
- 若後續確認 response latency 仍受 usage commit 明顯影響，再另案定義 durable queue 契約。

### P0-C：補齊針對根因的 regression tests（已完成）

目前 regression coverage：

1. AI key credential/user 查詢後 connection 已歸還 pool。
2. 已載入的 user/credential ID 與 rate limit 在 transaction 結束後仍可使用。
3. 有 pending writes 時 helper 不會誤 commit。
4. 模擬慢 usage commit 時，event loop 仍可排程其他 coroutine。
5. Streaming usage 使用獨立 session，且不依賴已結束的 request transaction。
6. Accounting failure 不改寫成功模型 response。

### P1-A：後端有界等待分流（已完成）

單一 backend process 採固定 admission contract：

```text
active slots = 20
max waiting = 40
wait timeout = 30 seconds
Retry-After = 5 seconds
```

決策：

- 不新增 `.env` 或 runtime mode；容量是後端固定契約。
- active 滿載時依 FIFO 等待，不立即拒絕；只有 40 個 waiters 已滿或等待超過 30 秒才回 `503`。
- `503 server_busy` 與配額型 `429` 分開，並附 `Retry-After: 5`。
- streaming 持有 permit 到完整 stream 結束；成功、錯誤、timeout 與 client cancellation 都會釋放。
- 若未來真的改為多 backend replicas，再依真實需求重審 distributed admission；本次不預建。

### P1-B：Rate limit 回到 credential scope（已完成）

既然限額欄位屬於 `AIAPICredential`，最小一致方案是：

```text
  rate_limit:credential:<credential_id>
```

不要同時預先加入 user/global 多層 RPM；只有產品明確需要跨 Key 的 account budget 時再增加。

### P2：共用 relay HTTP client（已完成）

由 application lifespan 管理 shared `httpx.AsyncClient`，connections 與 keepalive 都固定為
20，並在 shutdown 關閉。這是 transport 效率改善，不取代 P0 transaction 修正。

---

## 8. 實機驗證定案

驗證分兩階段，避免 GPU queue、LiteLLM RPM 與 DB transaction 混在一起。目前已完成
P0／P1／P2 的本機 regression tests；以下壓測均尚未執行。

### 8.1 階段 A：可控慢上游

使用固定延遲的 OpenAI-compatible stub，讓每個 request 穩定等待，例如 20 或 30 秒：

- 不使用真 GPU。
- 不受 LiteLLM model RPM 干擾。
- 固定 response body、token usage、stream chunk 間隔。
- 分別測 streaming 與 non-streaming。

測試矩陣：

```text
Concurrency: 1, 2, 4, 8, 12, 16, 20
Prompt、response tokens、delay、model name 全部固定
```

比較：

```text
A = 修正前
B = P0-A + P0-B
```

階段 A 應直接回答：慢 upstream 是否仍讓 AI request 保持 DB transaction，以及同步 usage
commit 是否會拖慢 event loop。這仍需要隔離的 PostgreSQL／PgBouncer test stack；不得以
目前的 unit regression tests 取代壓測證據。

### 8.2 階段 B：真實 LiteLLM / vLLM

階段 A 通過後，再用真實模型觀察：

- Campus AI success/error/429/503。
- P50/P95/P99 與 TTFT。
- Backend liveness、readiness 與一般 DB API latency。
- LiteLLM upstream latency、HTTP status、deployment state。
- vLLM running/waiting requests、KV cache、preemption、TTFT。
- GPU utilization 與 memory。

真實環境的 model deployment 可能有獨立 RPM；預期 upstream 429 必須和 Campus DB/event-loop
故障分開判讀，不能把所有失敗都算成同一類 concurrency failure。

### 8.3 PostgreSQL 查驗

壓測期間查：

```sql
SELECT
    pid,
    state,
    now() - xact_start AS xact_age,
    wait_event_type,
    wait_event,
    left(query, 160) AS query
FROM pg_stat_activity
WHERE datname = current_database()
ORDER BY xact_start NULLS LAST;
```

關鍵判讀：

```text
修正前：credential SELECT 的 idle in transaction 數量／xact_age 隨 AI concurrency 上升
修正後：credential transaction 在進入上游前結束，不再跟隨模型 latency 成長
```

### 8.4 PgBouncer 查驗

同步記錄：

```sql
SHOW POOLS;
SHOW STATS;
SHOW CLIENTS;
SHOW SERVERS;
```

重點欄位：

```text
cl_active
cl_waiting
sv_active
sv_idle
maxwait
```

### 8.5 Backend 查驗

同時探測：

```text
/api/v1/utils/health-check/        # liveness，不查 DB
/api/v1/utils/health-check/ready   # readiness，查 DB / Redis
```

| 現象 | 判讀 |
| --- | --- |
| Liveness 快、readiness 慢 | DB / PgBouncer 壅塞 |
| Liveness 也變慢 | event loop / process 被 blocking call 拖住 |
| Backend 正常、AI upstream 429 | LiteLLM deployment / rate limit |
| Backend/DB 正常、AI latency 高 | vLLM queue / GPU saturation |
| AI latency 上升後 idle transaction 同步上升 | 支持原事故鏈 |

---

## 9. 驗收條件

程式碼層 P0 已符合：

- AI credential dependency 在第一個慢速 `await` 前已結束 read transaction。
- Streaming 與 non-streaming 都不讓 credential transaction 跟隨模型生成時間。
- Usage DB I/O 不在 event loop 上直接執行。
- Usage 寫入失敗不改寫成功模型 response。
- Focused tests、全 backend `app/`／`tests/` Ruff 與本次三個核心模組的 mypy 通過。

事故驗證仍待符合：
- 可控慢上游 A/B 顯示 `idle in transaction` 不再隨 AI concurrency 累積。
- Backend liveness 在慢 usage DB 情境下仍可回應。
- 沒有 `QueuePool limit ... reached` 或 PgBouncer `cl_waiting` 持續累積。
- 真實 LiteLLM/vLLM 壓測能把 upstream capacity error 與 Campus backend failure 分開報告。

在階段 A 與真實 LiteLLM/vLLM 證據完成前，只能稱為「程式碼根因已修正」，不能聲稱
「正式環境事故已完全解決」。

---

## 10. 本次不採用的解法

- 不先提高 PostgreSQL `max_connections`。
- 不先把 PgBouncer pool 從 25 大幅放大。
- 不直接把 FastAPI workers 從 1 改為多 worker。
- 不在第一階段建立新的 queue framework；若需要 durable accounting，重用現有 Redis/ARQ。
- 不同時新增 credential/user/global 多層 rate limit。
- 不為未存在的多 backend replica 預建 distributed concurrency protocol。
- 不修改 public AI API、usage schema 或無關 frontend。

---

## 11. 已執行的靜態與測試驗證

已確認：

- FastAPI 版本：`0.141.1`。
- generation route 不再直接宣告 `SessionDep`；其 request session 只由 `AIAPIUserDep` 建立。
- FastAPI request-scoped dependency 在完整 streaming response 後 cleanup。
- 一般 JWT auth 與 AI API key auth 均已使用 `end_read_transaction()`。
- generation 與 models relay 共用 application-lifetime `httpx.AsyncClient`，並由 lifespan 關閉。
- usage 的 `session.commit()` 仍是同步 DB API，但已移至 worker thread，並使用獨立短 session。
- 已有固定、有界的 process-local admission queue；未新增 `AI_API_MAX_INFLIGHT` 環境變數。

已執行：

```text
uv run python -m pytest \
  tests/test_ai_proxy_relay_helpers.py \
  tests/api/test_db_connection_release.py \
  tests/test_ai_proxy_json_payload.py \
  tests/services/test_ai_platform_monitoring.py \
  tests/services/test_rate_limiter_delegation.py \
  tests/test_import_app_main.py \
  tests/test_metrics.py -q
```

結果：

```text
73 passed
```

另已執行：

```text
uv run ruff check app tests
uv run mypy \
  app/services/llm_gateway/relay_service.py \
  app/api/routes/ai_proxy.py \
  app/api/deps/ai_api_key.py \
  app/services/monitoring/ai_metrics.py
```

結果皆通過；另以 `pytest --collect-only -q` 確認目前 backend suite 共 3770 tests 可完整
收集，與 `.github/workflows/backend-tests.yml` 的 `uv run coverage run -m pytest tests/`
入口一致。CI 會提供 PostgreSQL 16 `app_test` 與 Redis 7；本機 focused tests 則涵蓋 AI Key
dependency 結束 read transaction、identity 欄位可用、pending write 不被誤 commit、usage
獨立 session、event loop 不阻塞、admission queue 的 wait/full/timeout/cancel/release、shared
client lifecycle，以及 credential-scope rate limit。這些仍不是 PostgreSQL／PgBouncer 實機
concurrency 證據。

擴大 DB-backed suite 因目前 `.env` 指向非 test-like database，被 pytest safety guard 正確
拒絕；未設定 `PYTEST_ALLOW_NON_TEST_DB=1` 規避保護。

本次未完成 live 驗證：

- Docker Desktop Linux engine 目前不可用，因此未啟動隔離的 Compose test stack。
- 未執行 PostgreSQL、PgBouncer 或可控慢上游壓測。
- 依本次範圍，未執行真實 LiteLLM、vLLM、GPU probe 或壓測。

---

## 12. 目前 P0 與後續目標生命週期

```text
Client
  -> Campus AI route
  -> short credential/user lookup
  -> end read transaction / return DB connection       # P0 已完成
  -> Redis RPM check
  -> bounded in-flight admission queue                 # P1 已完成
  -> shared HTTP client                                # P2 已完成
  -> LiteLLM
  -> vLLM
  -> response / stream
  -> short usage session in threadpool                 # P0 已完成
  -> DB
```

核心原則：

> **長時間 LLM inference 不得持有 Campus Database transaction；同步資料庫操作不得直接阻塞 FastAPI event loop。**
