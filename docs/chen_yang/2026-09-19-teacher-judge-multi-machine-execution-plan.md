# Teacher Judge 多機器腳本生成、執行與檢查項目對齊計畫

- 日期：2026-09-19
- 範圍：Teacher Judge rubric/proposal、managed script artifact、script run、SSH executor、結果投影與前端工作區
- 狀態：runtime 實作已完成，已通過 focused regression／靜態檢查；live vLLM、PVE/SSH 與瀏覽器 E2E 尚待部署環境驗收
- 基準分支：`刪除template.key+command.key`，分析時 HEAD `b68a8b0f`
- 前置文件：`docs/chen_yang/2026-09-16-teacher-judge-template-command-key-coupling-analysis.md`

本次已落地：request-scoped machine/peer contract、artifact set per-node generation、
peer-aware policy 與最小 runtime context、run batch／coverage item projection、
session API 及前端腳本集／整批執行工作流。舊 single-artifact／single-run API 保留為
legacy 路徑；未對未知資料庫執行 migration upgrade，也未宣稱 live runtime 已驗收。

---

## 1. 需求與本計畫結論

目標是讓同一張 Teacher Judge 檢查表可以描述多台邏輯機器，例如：

- P1 的 Web Server 檢查 nginx。
- P2 的 Database 檢查 PostgreSQL。
- P2 執行 `ping P1`，驗證 P2 到 P1 的連通性；此項仍歸類在 P2。
- AI proposal 明確記錄每個檢查項目應在哪個邏輯機器執行。
- 製作腳本時依機器拆分，不把 P1、P2 的命令混在同一份遠端腳本。
- 執行時，後端將每份腳本送到每位學生對應的正確 VM/LXC。
- 執行結果能由學生、機器與 rubric item 精準定位，不只顯示一串孤立的 `check_id`。

本計畫的核心決策如下：

1. `TeachingClassMachineNode.node_key` 是穩定的執行身份。
2. P1/P2/P3 只是由 `sort_order` 推導的顯示代號，不保存為執行 identity。
3. `target_node_key` 放在 rubric item，不放在 `command_key`，也不在每個
   `check_steps` 重複填寫。
4. `target_node_key` 同時是「執行節點」與「結果歸類節點」；一個 rubric item
   在 v1 只能有一個 target node，同一 item 的所有 step 繼承該 node。
5. 跨機器觀察使用可選的 `peer_node_key`；它只表示被觀察節點，不是第二個執行點。
6. v1 每個 item 最多一個 peer；P2 同時測 P1、P3 時拆成兩個 items，避免多目標
   結果無法一一對齊。
7. 一份 managed script artifact 仍只對應一個 target node；peer 不產生額外 artifact。
8. 多 node rubric 產生一個 artifact set；set 內每個 node 有一份獨立 child
   artifact。
9. 一次「執行全部」建立 run batch；batch 內每個 child run 沿用現有單 artifact
   executor。
10. 結果以 artifact coverage 將 runtime `check_id` 回投到原始 rubric item，並顯示
    `P2 -> P1` 的 executor/peer 關係。

腳本生成方式定案：proposal/檢查表仍可由一次 AI request 產生整份；後端先按
`target_node_key` deterministic partition，再對 P1、P2、P3 各自呼叫既有
generation/review pipeline。不要先生成一份含所有機器的 Python source 再由後端切
程式碼；後端只切結構化 rubric items，不解析或重寫模型生成的程式。多個 node 的
generation 未來可在同一 API request 內 bounded parallel，但產物與審查仍各自獨立。

這個方案保留既有安全與審查邊界，並補上目前缺少的多機器 orchestration 與受控
peer observation；不新增任意跨機器 command tool、跨節點 DAG、peer SSH/遠端執行，
也不讓模型看到 VMID/IP/SSH 資訊。

---

## 2. 目前已實作的能力

### 2.1 邏輯機器與 P 標籤

`backend/app/ai/teacher_judge/machine_context.py` 已提供：

- 依 `sort_order, node_key` 載入班級機器節點。
- 將 `sort_order + 1` 顯示成 P1/P2/P3。
- 給 AI 的 context 包含：
  - `display_label`
  - `node_key`
  - `name`
  - `role`
  - `resource_type`
  - `executor_capability`
- 不暴露 VMID、IP、SSH credential 或 Proxmox provider 細節。

目前資料語意是：

```text
P1 | node_key=web | name=Web Server | role=frontend | resource_type=lxc
P2 | node_key=db  | name=Database   | role=database | resource_type=lxc
```

`P1` 與 `P2` 可因老師重排節點而改變；`web` 與 `db` 才是既有 rubric、artifact
與 run 可以長期引用的值。

### 2.2 Proposal tool 與 rubric contract

目前新寫入的 `TeacherJudgeRubricCheckStep` 已收斂成：

```json
{
  "argv": ["systemctl", "is-active", "nginx"],
  "cwd": null,
  "timeout_seconds": 30
}
```

`template_key`、`command_key`、`command_label`、`parameters` 僅保留 legacy
read/convert 能力，不出現在新 proposal 的 tool schema。

`TeacherJudgeRubricItem` 已有：

```json
{
  "id": "service-nginx-status",
  "title": "確認 nginx 運行狀態",
  "target_node_key": "web",
  "detectable": "auto",
  "judgement_mode": "ai",
  "check_steps": [
    {
      "argv": ["systemctl", "is-active", "nginx"],
      "timeout_seconds": 30
    }
  ]
}
```

Session route 也已在 proposal 保存前驗證：

- `target_node_key` 必須存在於目前班級的 machine nodes。
- 班級有 machine node 時，auto item 不得缺少 `target_node_key`。
- 驗證使用 server-side class topology，而不是信任模型回覆文字。

### 2.3 單 node fan-out

目前 `create_script_run()` 已支援：

```json
{
  "target_scope": "all_students_on_node",
  "target_node_key": "web"
}
```

後端會：

1. 驗證 node 屬於 class。
2. 找出每位學生對應的 `TeachingClassStudentMachine`。
3. 解析 VMID、Resource、live status、IP 與 SSH key。
4. 對同一 logical node 下的所有學生機器執行同一份 approved artifact。
5. 以 `student_id + node_key` 保存 progress/result identity。
6. 對缺 VMID、未 running、缺 Resource、IP 或 SSH 的 target 保存逐機器失敗結果，
   不再以固定五台總量截斷全班執行。

### 2.4 Coverage 已具備 item 對照資料

腳本生成結果已要求：

```json
{
  "coverage": [
    {
      "check_id": "service.nginx.status",
      "rubric_item_ids": ["service-nginx-status"]
    }
  ]
}
```

`script_coverage_validator.py` 已驗證：

- `check_id` 確實存在於 script 的 `record_check()`。
- `rubric_item_ids` 確實存在於 artifact rubric snapshot。
- 每個 rubric item 至少有一個 check 提供證據。

所以缺口不是「沒有 mapping」，而是 executor/public API/UI 尚未將這份 mapping
物化成以 rubric item 為中心的結果投影。

---

## 3. 目前阻塞多機器的實際位置

### 3.1 Artifact 建立明確拒絕混合 node

`script_artifact_service.create_artifact()` 與 `regenerate_artifact()` 會收集 snapshot
內所有 `target_node_key`。只要超過一個就回：

```json
{
  "code": "teacher_judge_mixed_target_nodes",
  "message": "同一份 Teacher Judge 腳本目前只能對應一個 target_node_key。"
}
```

這個限制本身是正確的安全邊界：一份 artifact 不應暗中跨機器。要改的是在進入
單 artifact generator 前先依 node 分組，而不是移除限制後讓一份 script 同時包含
P1/P2 的命令。

### 3.2 Run 與 artifact 是一對一

`TeacherJudgeScriptRun` 只有一個 `artifact_id`。`create_script_run()` 也會拒絕：

- artifact snapshot 含多個 node key。
- request 的 target node 與 artifact node 不一致。
- legacy manual VMID 不屬於 artifact node。

這些檢查應保留。多機器執行需要的是「多個正確的 child run 共用一個 batch」，
不是讓單一 run 接受多個互不相干的 node。

### 3.3 前端仍讓老師重新選 node

目前執行頁會：

- 列出所有 machine nodes。
- 讓老師選 `selectedNodeKey`。
- 再以選擇結果呼叫單 artifact run。

但 artifact 本身已經包含 target node；老師若選到別的 node，只會在 server 收到
`teacher_judge_target_node_mismatch`。新主流程應直接從 artifact set 推導執行 nodes，
不再要求老師重選一次。

### 3.4 Runtime 結果未直接對齊 rubric item

Executor 目前保存：

```text
target
  -> parsed_result.checks[]
```

前端也直接列出 `checks[]`。Coverage mapping 位於 artifact
`policy_check_result_json.coverage.mappings`，尚未與 target result 合併，因此 UI
無法直接呈現：

```text
學生 A
  -> P1 Web Server
    -> rubric item：確認 nginx 運行狀態
      -> check：service.nginx.status
```

---

## 4. 排除的方案

### 4.1 不把機器身份放進 command_key

錯誤方向：

```text
linux.p1.systemctl
linux.p2.postgresql
```

原因：

- command 與 machine identity 是兩個獨立維度。
- 會重新引入本分支正在移除的 catalog coupling。
- 機器重排、改名或複製班級時會污染命令契約。
- runtime 最終仍需回到 class/student/node resolver，command key 無法取代授權。

正確模型：

```text
rubric_item.target_node_key = web
rubric_item.check_steps      = [{argv, cwd, timeout_seconds}]
```

### 4.2 不保存 P1/P2 作為 target identity

如果把 P1 寫入 rubric，老師把原 P2 拖到第一位後，舊 rubric 的 P1 會指向錯誤機器。

P label 只能作：

- 老師輸入的自然語言別名。
- AI prompt 與 UI 顯示。
- result display label。

持久化前必須 canonicalize 成 `node_key`。

### 4.3 不讓一份 Python script 自己 dispatch P1/P2

例如在 generated script 中加入：

```python
if TARGET_NODE == "web":
    ...
elif TARGET_NODE == "db":
    ...
```

這會造成：

- 每台機器都收到其他 node 的命令與檢查內容。
- policy、quality、coverage 與 AI reviewer 都要理解 dispatch path。
- runtime 必須注入可信 target context，增加新的腳本輸入契約。
- 一個分支錯誤可能拖累其他 node。
- 很難證明 check/item/node 三者一致。

因此維持「一個 child artifact = 一個 node」。

### 4.4 允許單跳 peer observation，不加入跨節點 DAG

「在 P2 測試 P1」收斂成：

```text
executor / 分類：P2 (target_node_key=db)
observed peer： P1 (peer_node_key=web)
實際動作：     P2 執行 ping，目的位址由後端解析同一學生的 P1
結果歸屬：     P2 的 rubric item，UI 顯示 P2 -> P1
```

P1 不會因此產生第二份腳本，也不會收到或執行任何命令。P2 只能讀取本項目宣告且
由 server 解析的 P1 連線資料，不能取得 P1 的 SSH credential，也不能登入 P1。

本次仍不處理：

- 先在 P1 執行，再把輸出傳給 P2。
- 從 P2 SSH 到 P1，或在 P1 遠端執行命令。
- 一個 item 依序操作多個 peers。
- 跨機器 transaction、rollback 或模型自行規劃執行順序。

現有 `CourseEnvironmentEdge` 已有 `source_node_key`、`target_node_key`、direction、
protocol（包含 ICMP）與 port，可作預期連線關係的 context 與 UI 說明；但它目前是
環境／防火牆契約，不是 Teacher Judge 的 runtime binding，也不能單獨證明某個學生
的實際 peer 位址。v1 的安全邊界是：peer 必須屬於同一 class、同一 student、由
rubric 明確宣告，且執行器只注入該 peer 的必要連線值；不得接受模型或前端提供任意
IP/CIDR。若有對應 topology edge，proposal 可同時顯示預期方向；沒有 edge 仍可作
「預期不通」的負向檢查，不把 topology 當成測試結果。

---

## 5. 目標資料契約

### 5.1 Request-scoped machine context

給模型的資料使用結構化 entries，再由 prompt formatter 顯示：

```json
[
  {
    "display_label": "P1",
    "node_key": "web",
    "name": "Web Server",
    "role": "frontend",
    "resource_type": "lxc",
    "executor_capability": "linux_ssh_sftp_python3"
  },
  {
    "display_label": "P2",
    "node_key": "db",
    "name": "Database",
    "role": "database",
    "resource_type": "lxc",
    "executor_capability": "linux_ssh_sftp_python3"
  }
]
```

不要把 formatted prompt text 當成 validator 的資料來源。Tool schema、alias resolver
與 class validation 都應使用同一批 entries。

同一 request 可另外提供去識別化的 topology entries，讓模型知道 P2 是否預期可連
P1；只包含 logical node keys、direction、protocol、port，不包含 IP、VMID 或憑證。

### 5.2 動態 proposal tool schema

目前 `_PROPOSAL_FILL_PROPERTIES.target_node_key` 是任意 string。改成每個 request
依 class nodes 建立 tool schema：

```json
{
  "target_node_key": {
    "type": ["string", "null"],
    "enum": ["web", "db", null],
    "description": "執行此檢查項目的班級邏輯機器；P1 對應 web，P2 對應 db"
  },
  "peer_node_key": {
    "type": ["string", "null"],
    "enum": ["web", "db", null],
    "description": "選填；由執行節點觀察的同班級邏輯機器，例如 P2 ping P1 時填 web"
  }
}
```

Server validation 仍須保留；tool enum 只是降低模型產生無效值的機率，不是授權層。
`peer_node_key` 不得等於 `target_node_key`；本機檢查保持 `peer_node_key=null`。

### 5.3 P label canonicalization

新增單一 helper，例如：

```python
def canonicalize_machine_node_key(
    raw_value: str | None,
    machine_entries: list[dict[str, Any]],
) -> str | None:
    ...
```

規則：

1. 空值回 `None`。
2. 精確命中目前 class 的 `node_key` 時直接回傳。
3. 精確、大小寫不敏感命中一個 `display_label` 時轉成該 node key。
4. 不以 `name`、`role` 做模糊猜測；名稱可能重複或變更。
5. 無法唯一解析時回 validation error，不自動選第一台。
6. canonicalize 後只保存 node key。

同一 helper 分別套用到 `target_node_key` 與 `peer_node_key`。這層只處理 AI tool／
自然語言輸入的顯示 alias；Run、artifact 與 DB 不接受 P label。

### 5.4 Rubric item 的 executor/peer contract

跨節點項目仍維持單一 item、單一 executor：

```json
{
  "id": "db-can-reach-web",
  "title": "P2 可連通 P1",
  "target_node_key": "db",
  "peer_node_key": "web",
  "judgement_mode": "ai",
  "check_steps": [
    {
      "argv": ["ping", "-c", "4", "{{peer.ip}}"],
      "cwd": null,
      "timeout_seconds": 30
    }
  ]
}
```

`{{peer.ip}}` 是唯一新增的 server-defined reserved token，不是一般模板語言。規則：

1. 只有 item 已宣告 `peer_node_key` 時可使用。
2. 只可作為完整 argv element，不可拼接成 URL、shell expression 或 CIDR。
3. 宣告 peer 的 auto item 至少一個 step 必須使用 token；有 token 卻無 peer、或有
   peer 卻完全未使用 token，都不能進 Ready。
4. Server 在 proposal/readiness 階段驗證 token 與 peer 一致；AI 不能提供實際 IP。
5. 生成後的 approved script 保持 immutable，不在執行前改寫 source。
6. 執行時每位學生建立最小 runtime context，腳本由固定路徑讀取 peer IP，再以 argv
   list 執行；禁止 `shell=True`。
7. Runtime context 只含本 child artifact 明確需要的 peer，不含 SSH username、key、
   password、Proxmox node 或其他學生資料。

建議的內部 runtime context：

```json
{
  "schema_version": "teacher_judge_runtime_context.v1",
  "executor": {"node_key": "db"},
  "peers": {
    "web": {
      "ip_address": "resolved-per-student",
      "resolution_status": "ready",
      "reason_code": null
    }
  }
}
```

同一 P2 child 若有不同 items 分別觀察 P1、P3，`peers` 只收錄這些已宣告的 node
keys；每個 item 仍只有一個 peer。無法解析時保留 node key，`ip_address=null`、
`resolution_status=unavailable` 與穩定 reason code，讓該 check 記錄 unknown，而不
中止同 script 的本機 checks。

此檔與 approved script 一起上傳到 P2；public API 不回傳 `ip_address`。腳本只能用它
完成已核准的 peer probe，不能把 context 當成任意 inventory。

目前 policy 已允許 generic literal argv 的 `ping`，卻沒有把目的位址綁定到 rubric
宣告的 peer；HTTP client 僅允許 literal localhost，且禁止 direct socket。實作時不
能全面放寬外部網路；先加入能追蹤
`{{peer.ip}} -> runtime context -> ping argv` 的 peer-aware validator。若之後要支援
HTTP/TCP peer probe，再逐一新增明確 primitive 與目的 port 規則。

### 5.5 Artifact set

建議在 `TeacherJudgeScriptArtifact` 新增：

| 欄位 | 型別 | 用途 |
| --- | --- | --- |
| `artifact_set_id` | nullable UUID，indexed | 將同一次 rubric 生成的各 node artifacts 分組 |
| `target_node_key` | nullable string(80)，indexed | 新 artifact 的正式執行 node；legacy row 可為 null |
| `source_analysis_revision` | nullable integer | 證明同一 set 來自相同 rubric revision |

不先新增 artifact-set parent table。理由：

- set 狀態可由 child artifacts 推導。
- session、source file、revision 與建立者已存在於 children。
- 同一建立請求可先完成所有 generation/review，再在一個 DB transaction 寫入 children。
- 若未來需要背景生成、取消、重試與長期 partial state，再依實際需求加入 parent job。

新 set 的 invariant：

- 每個 `artifact_set_id + target_node_key` 至多一個 active child version。
- set 內 children 的 class、session、source file、source revision 必須一致。
- 每個 child snapshot 只含該 target node 的 rubric items。
- child coverage 不得引用其他 node 的 item id。
- `peer_node_key` 不參與 partition；P2 測 P1 只進入 P2 child，不能再複製到 P1 child。
- child snapshot 保存該 item 的 canonical peer node key，供 run-time resolution 與
  result projection 使用。

### 5.6 Run batch

在 `TeacherJudgeScriptRun` 新增：

| 欄位 | 型別 | 用途 |
| --- | --- | --- |
| `run_batch_id` | nullable UUID，indexed | 將同一次「執行全部」的 child runs 分組 |

每個 child run 繼續只有一個 `artifact_id`。Batch API 建立時：

1. 載入 artifact set。
2. 驗證所有預期 child artifacts 已 approved。
3. 逐 child 以其 `target_node_key` 解析學生的 executor。
4. 對含 peer item 的 child，以 `(class_id, student_id, peer_node_key)` 解析同一學生的
   peer，建立最小 runtime context；禁止以全域 node key、前端 IP 或任意 VMID 查找。
5. 在同一 transaction 建立全部 child runs，共用 `run_batch_id`。
6. 提交一個 batch background task。
7. Batch task 初版依 node 順序執行 children；每個 child 內保留現有
   `MAX_SSH_CONCURRENCY=5`。

依 node 順序執行不是語意上的依賴，只是初版避免總 SSH 併發變成
`node_count × 5`。未來若需要跨 node 併發，先建立真正的 batch-wide limiter。

### 5.7 Public batch result

資料庫仍保存每個 child run 的 raw target result。Public API 動態投影成：

```json
{
  "run_batch_id": "...",
  "status": "completed_with_failures",
  "summary": {
    "nodes": 2,
    "students": 30,
    "targets": 60,
    "completed": 58,
    "failed": 2
  },
  "nodes": [
    {
      "target_node_key": "web",
      "display_label": "P1",
      "artifact_id": "...",
      "run_id": "...",
      "status": "completed"
    }
  ],
  "students": [
    {
      "student_id": "student-01",
      "nodes": [
        {
          "node_key": "web",
          "display_label": "P1",
          "execution_status": "completed",
          "items": [
            {
              "rubric_item_id": "service-nginx-status",
              "title": "確認 nginx 運行狀態",
              "judgement_mode": "ai",
              "checks": [
                {
                  "check_id": "service.nginx.status",
                  "status": "pass",
                  "evidence": "active",
                  "raw": "..."
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

跨節點 item 額外投影邏輯身份，不公開實際 IP：

```json
{
  "rubric_item_id": "db-can-reach-web",
  "title": "P2 可連通 P1",
  "executor": {"node_key": "db", "display_label": "P2"},
  "peer": {"node_key": "web", "display_label": "P1"},
  "evidence_state": "available",
  "checks": [{"check_id": "network.peer.ping", "status": "pass"}]
}
```

Public projection 不需把同一份完整 payload 再存成第三份 JSON；避免來源資料、
runtime result 與 UI summary 重複保存。

---

## 6. 檢查點與 rubric item 對齊規則

### 6.1 Source of truth

三層資料各自負責：

| 層 | Source of truth | 不負責 |
| --- | --- | --- |
| Rubric item | item id、title、executor/target node、optional peer node、judgement mode | runtime check output |
| Artifact coverage | `check_id -> rubric_item_ids` | runtime status 判定 |
| Runtime result | `checks[].status/evidence/raw` | 自行猜測 rubric item |

### 6.2 後端投影演算法

對每個 child artifact/run：

1. 從 artifact snapshot 建立 `rubric_item_id -> item/executor/peer` index。
2. 從 coverage mappings 建立 `check_id -> rubric_item_ids` index。
3. 對每個 target 的 `parsed_result.checks[]` 依 check id 查 mapping。
4. 將 check reference 放入對應 item 的 `checks[]`。
5. 沒有 mapping 的 runtime check 放入 `unmapped_checks` 診斷區，不算 rubric 證據。
6. coverage 宣稱存在但 runtime 缺少的 check 放入 item 的
   `missing_runtime_checks`，item evidence state 為 unknown。
7. 依原始 rubric item 順序輸出，不依 node/check 字典排序改變老師看到的順序。
8. Executor/peer 顯示名稱由目前 class machine entries 投影；缺少 peer mapping 時保留
   snapshot 中的 node key 與明確 reason，不把 item 移到 peer 節點下。

### 6.3 Item 狀態

本次不新增第二套 AI runtime judgement。Item status 僅做可預測的投影：

- target preflight/executor/JSON validation 失敗：`unknown`，理由是無法取得證據。
- peer 缺少 VMID/IP、未運行或不屬於同一學生：只有依賴該 peer 的 items 為
  `unknown`，reason code 為 `peer_unavailable`；同一 P2 script 的本機 items 仍可執行。
- `judgement_mode=teacher`：顯示所有 evidence，但 item 保持「待導師核查」。
- `judgement_mode=ai`：保留腳本 checks 的 status；若一個 item 有多個 checks，UI
  先完整顯示各 checks，不在沒有正式規則前新增不透明的總分或單一合成結論。
- 若產品確定需要 item-level badge，再以明確、可測試的 precedence 規則另行加入，
  不由前端臨時猜測。

### 6.4 從初始檢查表到最終結果的 trace key

同一項目全程不靠標題或 P label 對照：

| 階段 | 保存／使用的 identity | P2 測 P1 範例 |
| --- | --- | --- |
| 初始 rubric | `rubric_item.id`、target、peer | `db-can-reach-web`、`db`、`web` |
| P2 child snapshot | 原 item id、target、peer | item 原樣進 db child |
| Script coverage | `check_id -> rubric_item_ids` | `network.peer.ping -> [db-can-reach-web]` |
| Runtime output | `check_id` | `network.peer.ping` |
| Public projection | student + executor + item + optional peer | `student-01 / P2 / db-can-reach-web / P1` |

因此初始檢查表、腳本顯示與最後結果可以穩定對齊；P label 重排只改顯示，不改
`rubric_item.id`、node key 或 coverage mapping。

---

## 7. 修正後完整資料流

```text
老師：
  「P1 檢查 nginx，P2 檢查 PostgreSQL，並從 P2 ping P1」

  -> session route 載入 class machine entries
  -> prompt 顯示 P1/P2/node_key/name/role/capability
  -> request-scoped proposal tools 僅允許有效 node_key
  -> model tool calls
       item-nginx.target_node_key = web
       item-pg.target_node_key    = db
       item-peer.target_node_key  = db
       item-peer.peer_node_key    = web
  -> canonicalize P label fallback，保存 node_key
  -> route 驗證 class membership + analysis revision
  -> teacher Apply
  -> analysis_json 保存兩個 items

製作腳本：
  -> readiness gate 驗證每個 auto item 的 node + steps
  -> partition items by target_node_key
       web -> [item-nginx]
       db  -> [item-pg, item-peer]
  -> 每組獨立 generate / policy / quality / coverage / AI review
  -> 同一 artifact_set_id 寫入兩個 child artifacts
  -> UI 以 set 顯示 P1/P2 scripts 與審查狀態

執行：
  -> POST artifact-set run
  -> 建立共同 run_batch_id
       web artifact -> web child run
       db artifact  -> db child run
  -> server 依 class + student + node 解析實際 VM/LXC
  -> 對 db child 解析同一學生的 web peer，建立最小 runtime context
  -> approved script 只送到對應 node
       db script 在 P2 執行 ping，P1 不執行任何 script
  -> 逐 target 執行並保存 raw results
  -> coverage 將 checks 投影回 rubric items
  -> UI 顯示 student -> P1/P2 -> item -> checks
       跨節點 item 顯示 executor P2 -> peer P1
```

---

## 8. 分階段實作計畫

### 階段 A：收斂 proposal machine contract

主要檔案：

- `backend/app/ai/teacher_judge/machine_context.py`
- `backend/app/ai/teacher_judge/service.py`
- `backend/app/ai/teacher_judge/schemas.py`
- `backend/app/api/routes/teacher_judge_sessions.py`
- Teacher Judge prompt/tool tests

工作：

1. 讓 `machine_context_entries()` 同時供 prompt、tool schema 與 alias resolver 使用。
2. 將 proposal tool definitions 改成 request-scoped build，不再共用任意 string 的靜態
   `target_node_key` schema。
3. 新增 P label canonicalization，`target_node_key` 與 `peer_node_key` 輸出都只能是
   node key。
4. 保留 route-level class membership 驗證與 Apply/revision gate。
5. 確認 add/edit/refine/attachment itemwise 四條路徑使用相同 helper。
6. `check_steps` 保持 flat argv contract；只增加完整 argv element
   `{{peer.ip}}`，不新增 step-level machine 欄位或一般模板語言。

完成條件：

- AI tool 只能選目前 class nodes。
- 老師說 P1 時能寫入對應 node key。
- 老師說「在 P2 測 P1」時能寫入 `target_node_key=db`、`peer_node_key=web`。
- 重排後舊 rubric 不會改指向。
- 不存在、相同 executor/peer、跨班級或模糊 alias 無法進 Ready proposal。

### 階段 B：Artifact set 與 per-node generation

主要檔案：

- `backend/app/models/teacher_judge_script_artifact.py`
- 新 Alembic migration
- `backend/app/ai/teacher_judge/script_artifact_service.py`
- `backend/app/ai/teacher_judge/script_coverage_validator.py`
- `backend/app/api/routes/teacher_judge_sessions.py`
- `backend/app/api/routes/teacher_judge_scripts.py`
- artifact/generation tests

工作：

1. 新增 artifact set/target/revision 欄位與 index。
2. 建立 deterministic `partition_analysis_by_target_node()`。
3. 每個 partition 建立只含該 node items 的 analysis snapshot。
4. Peer item 只依 executor 分組，snapshot 保留 peer identity 與 reserved token。
5. Generation prompt 明示腳本由固定 runtime context 讀 peer IP；coverage validator
   仍以 `check_steps` 為唯一執行真相。
6. 增加 peer-aware policy/quality validation，只允許核准的 ping argv dataflow。
7. 對每個 partition 執行現有 generation/review pipeline。
8. 只在所有 partitions 已取得可保存結果後，以一個 transaction 寫入 child rows。
9. List/get API 加入 set projection；UI 使用的 session create route 回傳 set。
10. Regenerate 初版以整個 set 為單位，避免混用不同 rubric revisions。

完成條件：

- web/web/db 三個 items 產生兩份 child artifacts。
- db executor + web peer 的 item 只出現在 db child，不產生第三份 artifact。
- child coverage 不會因另一 node 的 item 被判 uncovered。
- Script policy 能證明 peer IP 只流入已核准的 `ping` argv，不可流入 shell、任意
  URL、檔案寫入或輸出 inventory。
- 任一 node generation/review 失敗時，錯誤能指出 node 與相關 items。
- 未完整 approved 的 set 不能執行全部。

### 階段 C：Run batch 與 executor orchestration

主要檔案：

- `backend/app/models/teacher_judge_script_run.py`
- 新 Alembic migration
- `backend/app/ai/teacher_judge/script_run_service.py`
- `backend/app/ai/teacher_judge/script_executor_service.py`
- `backend/app/api/routes/teacher_judge_sessions.py`
- `backend/app/api/routes/teacher_judge_scripts.py`
- run/executor tests

工作：

1. 新增 `run_batch_id`。
2. 建立 `create_script_run_batch()`，一個 child artifact 對應一個 child run。
3. Artifact 決定 node；一般教師 API 不再收自由 `target_node_key` 或 VMID。
4. 同一 transaction 建立全部 child runs 與 immutable executor target snapshots。
5. 逐學生解析 artifact 所需的 peers，建立最小 runtime context snapshot；public
   serializer 移除實際 IP。
6. Script 與 runtime context 一起傳到 executor node；不向 peer 建立 SSH session。
7. 建立 batch worker，初版依 node 順序呼叫既有 child executor。
8. 提供 batch polling projection；batch status 由 child statuses 推導。
9. 保留逐 target preflight failure；peer failure 只標記依賴它的 items，不讓一台失敗
   覆蓋其他機器或同 script 的本機檢查。

完成條件：

- N 位學生 × M nodes 產生正確的 N×M targets。
- 每台 target 只收到對應 node artifact。
- P2 ping P1 時只有 P2 建立 executor SSH；P1 只被解析為同學生 peer IP。
- 缺少 P1 時，P2 的本機 checks 照常執行，peer item 回 `peer_unavailable`。
- batch 總 SSH concurrency 不超過 5。
- child failure 不阻止其他 child 保存結果。

### 階段 D：Item-aligned result projection

主要檔案：

- `backend/app/ai/teacher_judge/script_executor_service.py` 或新的純投影 helper
- `backend/app/ai/teacher_judge/script_run_service.py`
- `backend/app/services/course/ai_assignment_service.py`
- schemas 與 result projection tests

工作：

1. 實作 coverage + runtime check + rubric item 的純函式 projection。
2. 保留 raw `target_results_json`，public response 才組 student/node/item 結構。
3. 在 item projection 加入 logical executor/peer metadata，不輸出 peer IP。
4. 標示 unmapped/missing runtime checks 與 `peer_unavailable`。
5. Teacher judgement 保持 evidence-only，不重新加入 runtime AI judgement。
6. 確認學生端只收到自己與允許範圍內的結果。

完成條件：

- 每個 item 只顯示 coverage 明確映射的 checks。
- item/node/student 三層 identity 不依標題或陣列 index 猜測。
- `P2 -> P1` item 仍位於 P2 群組，且能顯示被觀察的 P1。
- 執行器失敗呈現 unknown evidence，不偽造 rubric fail。

### 階段 E：前端 artifact set 與 run batch UX

主要檔案：

- `frontend/src/services/aiJudge.js`
- `frontend/src/pages/course-operations/class-workspace/AiJudgePanel.jsx`
- `AiJudgePanel.module.scss`
- frontend tests/locales

工作：

1. Rubric row 顯示 `P1 · Web Server`；跨節點 row 顯示
   `執行 P2 · Database -> 觀察 P1 · Web Server`，必要時以小字補 node key。
2. Script 頁依 set 分組，展開顯示各 node child script/review 狀態。
3. 主流程提供單一「執行全部對應機器」動作。
4. 移除主流程重新選 node 的欄位；manual VMID 降為 legacy/admin 路徑。
5. 結果表改為 student -> executor node -> item -> checks；peer 是 item metadata，
   不另外複製一份到 peer node。
6. 顯示部分成功、node preflight failure、排隊與 teacher-review 狀態。
7. Polling 以 batch id 為主，切換 session/class 時保留現有 stale-response guard。

完成條件：

- 老師不需要理解 VMID 或重選 artifact 已指定的 node。
- P1/P2 每個項目、腳本、執行進度與結果能沿同一視覺脈絡追蹤。
- 老師可從初始 rubric row 一路對照到 P2 child script 的 coverage 與 `P2 -> P1`
  runtime result。
- 部分失敗不會只剩一個模糊的整批錯誤 toast。

### 階段 F：Migration、回歸與 live 驗收

工作：

1. Legacy artifact/run 的新欄位保持 nullable，可繼續讀取。
2. 新資料必須寫入 artifact target/set 與 run batch identity。
3. 在隔離 PostgreSQL 驗證 Alembic upgrade/head/downgrade；不可使用 production DB。
4. 執行 focused pytest、Ruff、targeted mypy、frontend Vitest/build 與
   `git diff --check`。
5. 使用 configured vLLM 做兩個 node 的小型 generation/review smoke。
6. 使用測試班級與至少兩種 logical nodes 做 PVE/SSH execution acceptance，包含
   P2 執行 ping P1、peer 缺失與連線失敗案例。
7. 最後做瀏覽器 E2E：proposal -> Apply -> generate set -> review/approve -> run batch
   -> item-aligned results。

---

## 9. 測試矩陣

### 9.1 Tool、normalization 與授權

- P1 唯一對應 web，tool 最終保存 `target_node_key=web`。
- P2 唯一對應 db。
- 「P2 測 P1」保存 `target_node_key=db`、`peer_node_key=web`。
- 直接輸入 web/db 可用。
- `P0`、`P99`、其他班級 node、空字串被拒絕。
- `target_node_key == peer_node_key` 被拒絕；本機檢查應使用 `peer_node_key=null`。
- 沒有 peer 的 item 不得使用 `{{peer.ip}}`；有 peer 的 ping step 必須只在完整 argv
  element 使用該 token。
- 同名 machine names 不作模糊解析。
- P label 重排後既有 rubric 的 node key 不變。
- add/edit/refine/attachment itemwise 都使用相同 canonicalizer。
- auto item 缺 node 仍是 item-level blocker，不影響其他合法 proposal 的呈現。

### 9.2 Artifact partition 與 coverage

- 兩個 web items 只產生一個 web child。
- web + db 產生兩個 children，共用 set id/revision。
- 每個 child snapshot 只含自己的 items。
- db executor + web peer item 只進 db child；peer 不增加 artifact 數量。
- coverage 引用另一 node item 時失敗。
- 同 item 不允許同時指定多個 node。
- manual/partial/unresolved item 維持目前 script readiness 規則，不被偷偷略過。
- set regeneration 不混用不同 source revisions。

### 9.3 Target resolution 與 batch execution

- 3 students × web/db = 6 target snapshots。
- web artifact 只出現在 web targets。
- db artifact 只出現在 db targets。
- P2 ping P1 只對 P2 建立 SSH session；runtime context 的 peer 必須解析成同一學生
  的 P1，不能拿到另一位學生或另一班的 P1。
- Peer context 只提供 declared peer 的必要 IP，不含 SSH credential 或全班 inventory。
- Peer IP 只能流入核准的 ping argv；shell、任意目的 IP、CIDR 與未宣告 peer 被拒絕。
- 一名學生缺 db VMID：web 仍執行，db 保存 `missing_vmid`。
- 一名學生有 db 但缺 web VMID/IP：db 本機 checks 仍執行，peer item 保存
  `peer_unavailable` 與 unknown evidence。
- Resource owner mismatch、not running、unsupported OS、missing IP、missing SSH
  各自保存 reason code。
- executor 開始前再次驗證 artifact approved、owner、class/node 與 runtime resource。
- batch-wide 最大 SSH concurrency 為 5。
- background failure 後 child/batch 進入可診斷終態，不永久 pending。

### 9.4 結果對齊

- 一個 check -> 一個 item。
- 多個 checks -> 一個 item。
- 一個 check -> 多個 items。
- runtime 多出未 mapping check -> `unmapped_checks`。
- coverage mapping 的 check 未出現在 runtime -> `missing_runtime_checks`。
- teacher mode 保留 evidence 並顯示待導師核查。
- executor/JSON failure 不直接變成 rubric fail。
- Peer resolution failure 只影響引用該 peer 的 items，不污染同 executor 的其他 items。
- 跨節點 item 投影含 executor P2 與 peer P1，但只歸在 P2 群組。
- item 輸出順序與原 rubric 相同。

### 9.5 Public/sensitive-data boundary

- AI prompt/tool 不含 VMID、IP、SSH key、Proxmox node 或 password。
- Teacher public result 以 student/node identity 為主；連線 credential 永不回傳。
- Public item 可顯示 peer 的 logical node key/P label/name，但不回傳 runtime IP。
- Student API 只能看到自己對應的 target results。
- Run snapshot 可保存 executor 需要的內部值，但 public serializer 必須移除。

### 9.6 Frontend

- Rubric 顯示 P label/name/node 正確。
- 跨節點 rubric/script/result 都以相同文字呈現 `執行 P2 -> 觀察 P1`。
- Set 顯示每個 node child 狀態。
- 未完整 approved 的 set 禁止執行。
- Batch partial failure 可展開到特定學生與 node。
- Result check 正確巢狀在 rubric item 下。
- session/class 快速切換時舊 polling response 不覆蓋新頁面。
- 390×844、鍵盤操作、focus、ARIA 與長文字 overflow 做實際 browser QA。

---

## 10. API 建議

在確認現有 API consumer 後，session 主流程建議收斂為：

```text
POST /teaching-classes/{class_id}/judge/sessions/{session_id}/script-sets
GET  /teaching-classes/{class_id}/judge/sessions/{session_id}/script-sets
GET  /teaching-classes/{class_id}/judge/sessions/{session_id}/script-sets/{set_id}
POST /teaching-classes/{class_id}/judge/sessions/{session_id}/script-sets/{set_id}/regenerate
POST /teaching-classes/{class_id}/judge/sessions/{session_id}/script-sets/{set_id}/runs
GET  /teaching-classes/{class_id}/judge/sessions/{session_id}/run-batches/{batch_id}
```

是否改現有 `/scripts` response，先以 repo 內 consumer 與 OpenAPI 使用情況決定：

- 若只有現有 frontend 使用，直接遷移並刪除舊重複主流程。
- 若已有外部 consumer，保留有期限、明確標記 legacy 的單 artifact route。
- 不使用 union response、primary artifact + hidden siblings 等難以理解的相容包裝。

一般 set-run request 不再要求老師指定 node：

```json
{
  "target_scope": "all_students_in_set"
}
```

Set 中的 child artifacts 是執行 node source of truth。未來若要只執行單一學生，新增
穩定的 `student_id` scope，由 server 解析該學生在 set 中所有 nodes；不要重新開放
任意 VMID 給一般教師流程。

---

## 11. Migration 與相容策略

### 11.1 Artifact

- 舊 artifacts：`artifact_set_id=NULL`、`target_node_key=NULL`。
- 讀取舊資料時可從 `rubric_snapshot_json` 推導單一 node；若沒有 node，仍走原
  legacy manual run contract，不猜測。
- 新 artifacts：target node 與 set id 必填，由 service 建立，不接受 frontend 自填。
- 不要求以 Alembic JSON expression 大量回填舊 rows，避免跨 PostgreSQL/SQLite
  方言與不完整 legacy snapshot 造成錯誤遷移。

### 11.2 Run

- 舊 runs：`run_batch_id=NULL`，仍以單 run API 讀取。
- 新 set execution：每個 child run 必須有同一 batch id。
- 不把舊 run 人工包成虛構 batch；public adapter 可把單 run 顯示成一個 legacy
  單節點 execution。

### 11.3 command/template legacy

本計畫不擴張 legacy key：

- 新 proposal 不寫 `command_key/template_key`。
- 舊 step 仍先轉成 flat `argv/cwd/timeout_seconds`。
- Machine target 永遠不從 command key 推導。
- Artifact partition 只看 canonical `target_node_key`。

### 11.4 Peer contract

- `peer_node_key` 保存於 rubric `analysis_json` 與 child `rubric_snapshot_json`，初版不為
  它新增獨立 DB 欄位；它不是 artifact partition key。
- 舊 rubric 沒有 `peer_node_key` 時視為 `null`，不需要資料庫回填。
- Runtime peer context 是每位學生、每次 child run 的內部執行輸入，不回寫 rubric，
  也不覆蓋 approved artifact source。
- 若未來查詢量證明需要依 peer 篩選，再依真實需求增加欄位或 index；本次不預先
  正規化一套多節點 dependency tables。

---

## 12. 風險與對策

| 風險 | 對策 |
| --- | --- |
| P1/P2 重排導致執行錯機 | 只保存 node key，P label 僅 request-time alias/display |
| AI 發明 node | request-scoped enum + server membership validation |
| 同一腳本混入不同 node 命令 | 生成前 deterministic partition，child snapshot 只含單 node items |
| Peer 被誤當第二執行點 | `target_node_key` 是唯一 executor/partition key；peer 不建 artifact 或 SSH session |
| P2 拿到別人的 P1 | 只以 `(class_id, student_id, peer_node_key)` server-side 解析並驗證 owner |
| 動態 IP 使 approved script 失真 | Script source 不改寫；每次 run 注入最小、有版本的 runtime context |
| 放寬 ping 後可掃描任意網段 | 只接受完整 `{{peer.ip}}` token與 declared peer；禁止 literal external IP、CIDR、shell |
| Peer 缺失拖垮整份 P2 script | 對依賴 peer 的 checks 回 unknown/`peer_unavailable`，本機 checks 繼續 |
| 多 child generation 拉長 request | 初版量測兩到三 node；必要時 bounded concurrency 2，不先建背景框架 |
| 多 child runs 造成 SSH 併發倍增 | 單 batch worker 初版依 node 執行，child 內上限 5 |
| 某 node generation/review 失敗仍被執行 | set completeness/approved gate，禁止 silent partial execution |
| UI 自行猜 check/item 關係 | server 使用 stored coverage 投影 |
| Target 失敗被誤判為學生項目 fail | execution failure 投影為 unknown/no evidence |
| 新 JSON 重複保存大量結果 | raw child result 持久化一次，batch/item 結構動態投影 |
| API 相容層永久存在 | 先盤 consumer；只在有真實外部使用者時保留期限明確的 legacy route |

---

## 13. 驗收條件

完成必須同時符合：

1. 老師以自然語言指定 P1/P2，保存後只有 canonical node key。
2. P label 重排或機器改名不改變既有 rubric 的實際目標。
3. 「P2 測 P1」保存為唯一 executor/歸類 P2 與唯一 observed peer P1。
4. 一張含 web/db items 的 rubric 可建立同一 artifact set 下的兩份 child scripts。
5. P2 ping P1 item 只出現在 P2 child，不為 P1 多建或多跑一份 script。
6. 每份 child script 只包含該 executor node 的 items 與 commands。
7. 每份 child script 各自通過 policy、quality、coverage、AI review 與核准閘門。
8. 一次 run batch 能對全班每位學生的對應 web/db 機器執行正確腳本。
9. P2 執行時只取得同一學生、rubric 已宣告的 P1 peer IP，不取得 P1 credential。
10. Peer 缺失或 ping 失敗能精準落在原 P2 rubric item，不取消 P2 其他本機 checks。
11. 單一機器失敗不會遮蔽或取消其他 target results。
12. Batch result 能由 `(student_id, executor_node_key, rubric_item_id)` 唯一定位，並在
    item metadata 顯示 optional peer node。
13. 初始 rubric、P2 script coverage 與最終結果使用同一 item id/check id 映射，不靠
    標題、P label 或陣列位置猜測。
14. UI 不要求老師手動選與 artifact 重複的 node，也不以 VMID 作主要操作身份。
15. AI prompt/tool/public result 不暴露 VMID、IP、SSH credential 或 provider secret；
    runtime IP 只存在內部 per-run context。
16. 新流程不重新產生或依賴 `command_key/template_key` 選擇機器。
17. Legacy single artifact/run 可讀；新 set/batch contract 有 focused migration tests。
18. Focused tests、Ruff、targeted mypy、frontend tests/build、diff check 通過。
19. 真正完成宣稱前，另有 live vLLM、PVE/SSH 與瀏覽器 E2E 證據；靜態與 mock
    tests 不替代這些 runtime 驗收。

---

## 14. 建議實作順序

```text
A. executor/peer tool contract
  -> B. artifact partition + peer-aware generation/policy
  -> C. run batch + runtime peer context + bounded executor
  -> D. item-aligned result projection
  -> E. frontend set/batch UX
  -> F. migration + live acceptance
```

不要先從前端增加多選 P1/P2，也不要先移除 mixed-node blocker。必須先讓 backend
能夠 deterministic partition、保存 set identity、建立 batch 與正確投影結果，之後
前端才有穩定契約可以呈現。
