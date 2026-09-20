# AI Teacher Judge 多機器檢查與腳本產出定案

> 版本：2026-09-20
>
> 狀態：架構定案、尚待實作；目前分支已具備 Artifact Set、依 node 分組與 Run Batch，但仍使用 flat `check_steps`，且每個 node 都會分別呼叫 AI 生成／審查／修補腳本。typed Check Plan、deterministic compiler 與 result v2 是本定案的目標，不是目前既有能力。
>
> 範圍：保留目前老師詢問與聊天提案互動；收斂「儲存並製作腳本」之後的全表 Finalizer、Check Plan 驗證、按機器分割、腳本產出、執行、結果投影與導師核查。

---

## 1. 一句話定案

> **老師照常在 Chat 描述與調整需求；按下「儲存並製作腳本」時，沿用既有全表重新核對呼叫作為唯一的 Check Plan Finalizer AI。Finalizer 一次整理完整 rubric 的 typed Collector／Assertion，後端驗證後依 canonical `target_node_key` 分割成 child Artifacts，再由固定 compiler 產生自足 `script.py`。不再對每個 node 呼叫 AI 生成、審查或修補 Python。**

核心決策：

- 不新增頂層 `nodes[]`，避免與既有 `items[].target_node_key` 形成第二份 source of truth。
- 不改老師目前的詢問方式、Chat proposal lifecycle、Apply 與自然語言缺口回覆。
- 平常 Chat 的提案提供需求方向與 item-level 執行線索；真正進入腳本生成的 canonical Check Plan，由 Save/Create 時的一次全表 Finalizer 產生並經後端驗證。
- Finalizer 可以沿用現有 `is_refine`／全表重新核對流程；不新增每台機器各自的 planner request，也不新增第二套聊天 UI。
- `target_node_key` 是執行、Artifact、Run 與結果歸屬的唯一機器身分；P1/P2/P3 只用於輸入別名與顯示。
- `peer_node_key` 只表示 executor 要觀察的同一位學生之 peer，不參與 Artifact 分組，也不建立 peer SSH。
- 一個 node 對應一個 child Artifact；同組共用 `artifact_set_id`，同批執行共用 `run_batch_id`。
- 第一階段仍產生自足 `script.py`，沿用現有 SSH/SFTP/python3 Executor；不先引入共用 Runner 部署與版本管理。
- `judgement_mode=system` 使用固定 Assertion 產生 `pass/fail`；`judgement_mode=teacher` 不帶 Assertion，成功取證後回 `collected`。
- 新的 Save/Create 流程只接受 Finalizer 完成且通過驗證的完整 typed plan；舊 Artifact/result 保持可讀，不讓新流程靜默退回 AI Python generation。

---

## 2. 目前程式實況

以下是目前分支的實際狀態，不是文件推測。

| 邊界 | 已存在的行為 | 定案判讀 |
|---|---|---|
| Chat proposal | `prompt.py` 與 `service.py` 已讓老師以原本方式逐項提案，保存 item-level target、peer、`judgement_mode=ai/teacher` 與 flat `check_steps`（`argv/cwd/timeout_seconds`） | **互動架構保留**；平常 Chat 不負責產生最終 Python |
| Save/Create 全表核對 | `handleSaveAndCreate()` 已先送出 `RUBRIC_POLISH_PROMPT`，取得全表重新核對結果、建立 candidate、保存最新 revision | 沿用這個既有 AI request，擴充並正式定義成唯一 Finalizer；由 server 將 proposal operations 套回 base rubric，形成完整 canonical plan |
| Proposal validation | 現有流程已有欄位 normalization、readiness 與安全檢查，但契約仍是 flat step | 保留早期回饋；新增 typed schema 後，Finalizer 的完整 candidate 與各 node partition 都要再做 deterministic validation |
| Script creation call | Finalizer 保存後仍呼叫 `AiJudgeService.createSessionScript()` → `POST /sessions/{id}/scripts` | **目前主要斷點**；應改接 `/script-sets`，不能建立單一 mixed-node Artifact |
| Script Set API | 已有 `POST /sessions/{id}/script-sets` 與 `create_artifact_set()` | 應成為新的唯一 Save/Create 產出入口 |
| Partition | `partition_analysis_by_target_node()` 已依 `target_node_key` 分組 | 保留，不在 AI 生成後切 Python |
| Script generation | `create_artifact_set()` 目前在每個 node partition 內呼叫 `_build_reviewed_script_for_artifact()`，各自進行 AI generation、review，必要時 repair | **本次要替換的核心**；分組保留，迴圈內改成本地 deterministic compile |
| Compiler | 目前分支沒有 `compile_check_plan()` 或等價 deterministic compiler | 依本文件的 typed Collector／Assertion 契約新增；新流程不得回退 AI Python generation |
| Result | 目前仍以既有 result／evidence 契約處理執行結果 | `teacher_judge_result.v2` 與 `pass/fail/unknown/collected/skipped` 是配合 compiler 的目標契約 |
| Execution | Artifact Set 可建立 child runs，共用 `run_batch_id`，依 node 執行所有學生目標 | 保留現有 server-owned target、VM、SSH 與上限檢查 |
| Projection | coverage 將 `checks[].id` 投影成 `student -> node -> rubric item -> checks` | 保留；peer 只作 item metadata |
| Frontend review | 已有多機器結果與導師 review 的既有 UI／資料流 | 延續既有 hierarchy；配合 result v2 補齊 `unknown/collected` 與長 evidence 顯示 |

因此，這次不是重做整套多機器流程，而是保留既有外框、替換其中的腳本產生核心：

1. 把既有全表重新核對正式收斂為一次 Finalizer，明確產出完整 canonical Check Plan。
2. 定義 typed Check Plan，實作後端 deterministic compiler 與 result v2。
3. 把 Save/Create 主入口改接 Artifact Set，並以 compiler 取代每個 node 的 AI generation/review/repair。
4. 關閉新流程回退 AI 產 Python 的可能性，補齊安全、輸出上限與 evidence UI 驗收。

---

## 3. 最終資料流

```text
老師自然語言需求
  -> 現有 Chat checklist tools
  -> item-centric proposal / 需求方向
  -> 老師 Apply
  -> 老師按「儲存並製作腳本」
  -> 一次全表 Finalizer AI request
  -> server 套用 proposal operations，形成完整 candidate plan
  -> 完整 candidate analysis / per-node plan 驗證
  -> POST /sessions/{session_id}/script-sets
  -> 依 canonical target_node_key 分組
  -> 每 node deterministic compile 成 child script.py
  -> artifact_set
  -> 老師觸發 execute all
  -> run_batch / child runs
  -> 每位學生的 executor SSH/SFTP/python3
  -> Collector 取得 observation
  -> Assertion 固定判定，或 teacher mode 回 collected
  -> result v2 validation
  -> coverage projection
  -> student -> executor node -> rubric item -> checks
  -> 老師查看證據或核查 collected/unknown 項目
```

狀態邊界不可混用：

- Chat proposal ready：代表有可供老師檢查與 Apply 的需求提案，不保證已是最終 canonical plan。
- Apply：代表檢查表需求已保存，不代表已產生腳本。
- Finalizer complete：代表完整 rubric 已被整理成 typed plan 並通過後端驗證，不代表已執行學生機器。
- Artifact approved：代表 typed plan、compiler 與 deterministic gates 已通過，不代表學生機器已執行。
- Target `status=completed`：只代表遠端程式結束且 result JSON 有效，不代表所有 checks 通過。
- Teacher decision：是保存在 `teacher_review.decisions` 的人工核查，不覆寫原始 machine status/evidence。

---

## 4. Canonical Check Plan（定案目標）

### 4.1 Finalizer 邊界

Finalizer 是 Save/Create 內的一次全表 AI request，不是每個 node 一次，也不是 Python generation request。

輸入只需要：

- 目前已保存的完整 rubric items。
- 最新 `analysis_revision` 與 source identity。
- 班級 logical machine topology 與 canonical node keys。
- 檢查環境、必要附件摘要與最新仍未解決的 bounded conversation focus。
- Collector／Assertion JSON schema 與安全提示。

AI 可以繼續回傳既有 proposal operations，不必為了形式完整而重複輸出所有未變更 item。後端負責把 operations 套用到 base rubric，得到完整 candidate analysis，再以完整 candidate 作為唯一 Check Plan source of truth。

Finalizer 的責任是：

- 一次檢查所有 item 的 target、peer、取證方式與判定方式是否一致。
- 把 Chat 階段留下的方向整理成完整 typed Collector／Assertion。
- 對缺少資訊、unsupported 或內部 validation error 保留逐 item 結果；不得猜值湊成 ready。
- 不產生 Python、不分 node 呼叫模型、不讀取任何學生 runtime evidence。

Finalizer 回傳後，後端必須重新驗證；模型文字或 `proposal_status=ready` 不能直接授權腳本。

這個分工不要求平常發起提案的 AI 同時完成所有腳本細節。Chat request 只需把當下需求收斂到 item；Finalizer 才在 Save/Create 時看完整 rubric 與固定 schema，一次補齊跨 item／跨 node 一致性。若某一 item 契約對不上，回報該 item 的 validation issue，最多做一次 bounded contract repair；其他已合法 item 不必重新生成 Python。

目前資料仍是 flat `check_steps`；以下 typed 結構是新 Save/Create 的目標寫入契約。既有 rubric／Artifact 只保留讀取與執行相容，不應被誤寫成目前已完成。

### 4.2 唯一結構仍是 `items[]`

不採用：

```json
{
  "nodes": [
    {"target_node_key": "P1", "checks": []}
  ]
}
```

採用：

```json
{
  "schema_version": "teacher_judge_check_plan.v1",
  "items": [
    {
      "id": "nginx-service",
      "title": "Nginx 必須啟動",
      "detectable": "auto",
      "judgement_mode": "system",
      "target_node_key": "web",
      "peer_node_key": null,
      "check_steps": [
        {
          "id": "service.nginx.active",
          "title": "取得 Nginx 服務狀態",
          "collector": {
            "type": "command",
            "argv": ["systemctl", "is-active", "nginx"],
            "timeout_seconds": 10
          },
          "assertion": {
            "type": "text_equals",
            "expected": "active",
            "normalize": "strip"
          }
        }
      ]
    }
  ]
}
```

規則：

- `rubric item.id` 是評分項目身分。
- `target_node_key` 必須是目前班級實際存在的 class-local `node_key`。
- `peer_node_key` 選填，且不可等於 `target_node_key`。
- Step 巢狀於單一 item，不重複保存 `rubric_item_ids`、target 或 peer。
- `step.id` 在同一 child/node plan 內唯一；coverage 由 parent `item.id -> step.id` 確定性產生。
- Compiler 只能讀 validated typed fields；不得從 `title`、`detection_method` 或對話 prose 再猜命令、路徑或答案。
- `total_items`、`auto_count` 等摘要只能由 `items[]` 重算，不得成為執行 truth。

### 4.3 `system` 與 `teacher`

System 判定：

```json
{
  "judgement_mode": "system",
  "check_steps": [{
    "id": "python.main.exit",
    "collector": {
      "type": "command",
      "argv": ["python3", "main.py"],
      "cwd": "/home/student/project",
      "timeout_seconds": 30
    },
    "assertion": {
      "type": "returncode_equals",
      "expected": 0
    }
  }]
}
```

導師核查：

```json
{
  "judgement_mode": "teacher",
  "check_steps": [{
    "id": "postgres.log.tail",
    "collector": {
      "type": "file_text",
      "path": "/var/log/postgresql/postgresql.log",
      "read_mode": "tail",
      "lines": 50,
      "max_chars": 12000,
      "encoding": "utf-8"
    }
  }]
}
```

`collect_only` 不作為 Assertion type。它沒有真假條件；同一語意已由 `judgement_mode=teacher + assertion 缺省` 完整表達。Collector 成功時 runtime 回 `collected`。

舊 `judgement_mode=ai` 只作 read compatibility；新 typed plan 正規化成 `system`。

### 4.4 不建立情勢模式矩陣

不新增「執行程式模式」「撈 log 模式」「硬判斷模式」等互斥分類。Finalizer 只組合兩個正交軸：

```text
Collector：怎麼取得資料
Judgement：固定 Assertion 或交給 teacher
```

| 老師需求 | Collector | Judgement |
|---|---|---|
| 執行 `main.py`，成功即可 | `command` | `returncode_equals: 0` |
| 執行程式，輸出包含 `Ready` | `command` | `text_contains: Ready` |
| 撈回 log 最後 100 行 | `file_text/tail` | `teacher`，不帶 Assertion |
| log 必須包含 `Server started` | `file_text/tail` | `text_contains` |
| 指定檔案必須存在 | `file_stat` | `exists: true` |
| P2 必須能連到 P1 | `peer_ping` | `returncode_equals: 0` |

這樣新增需求通常只是新的 Collector／Assertion 組合，不需要持續增加 workflow mode、CLI flag 或平行 schema。

---

## 5. Collector 與 Assertion v1（待實作）

### 5.1 Collector

第一版只實作完成本次需求所需的五種：

| Type | 必要欄位 | 用途 |
|---|---|---|
| `command` | `argv`、`timeout_seconds`；`cwd` 選填 | CLI、版本、服務狀態、程式入口 |
| `file_text` | `path`、`read_mode`、`max_chars`；head/tail 另需 `lines` | 文字設定、結果檔、log |
| `file_stat` | `path` | 是否存在、類型、大小 |
| `localhost_http` | `method`、`url`、`timeout_seconds`、`max_chars` | localhost Web/API |
| `peer_ping` | `timeout_seconds`，parent item 必須有 `peer_node_key` | executor 對 peer 的單跳連通性 |

第一版不新增 `system_info`。能安全表達的項目先使用受控 `command`；只有出現穩定且重複的需求時才升成專屬 Collector。

### 5.2 Assertion

| Type | 必要欄位 | 語意 |
|---|---|---|
| `returncode_equals` | integer `expected` | process/ping return code 相等 |
| `text_equals` | string `expected` | canonical text 完整相等 |
| `text_contains` | string `expected` | canonical text 包含固定字串 |
| `number_compare` | numeric `expected`、`operator` | `eq/ne/gt/gte/lt/lte` |
| `json_path_equals` | `path`、`expected` | 受限 JSON path 相等 |
| `exists` | boolean `expected` | `file_stat.exists` 相等 |

第一版不支援 regex、任意 expression、Python callback、跨 step reference 或自由 `eval`。`line_exists` 可先用 `text_contains` 表達；需要真正逐行語意時再增加明確 primitive。

每種 Assertion 必須在 schema/validator 層驗證自己的欄位型別，不能把不合法型別拖到學生機器上才得到 `fail/unknown`。

---

## 6. 多機器責任邊界

本節描述保留既有 Artifact Set／Run Batch 外框後，新 compiler 應遵守的目標行為。

### 6.1 Partition

```text
items[target_node_key=web] -> child Artifact(web)
items[target_node_key=db]  -> child Artifact(db)
```

- P1/P2/P3 只在 prompt 輸入正規化與 public UI 顯示使用。
- Artifact、Run、coverage 與 result identity 一律保存 canonical `node_key`。
- 所有 partitions 必須先在記憶體完成 plan、安全與 compile 驗證；任一 child 失敗時整組零寫入。
- 全部成功後才在單一 transaction 保存 child Artifacts。

### 6.2 P2 測 P1

若 P2/P1 的 canonical keys 是 `db/web`：

```json
{
  "id": "db-to-web",
  "target_node_key": "db",
  "peer_node_key": "web",
  "judgement_mode": "system",
  "check_steps": [{
    "id": "network.web.reachable",
    "collector": {"type": "peer_ping", "timeout_seconds": 10},
    "assertion": {"type": "returncode_equals", "expected": 0}
  }]
}
```

執行與結果規則：

- 只建立 `db` child Artifact，只在該學生的 `db` executor 執行。
- 後端依 `(class_id, student_id, peer_node_key)` 解析該學生的 `web` IP。
- peer IP 只放入 per-run `runtime_context.json`，不寫進 plan、Artifact source 或 public result。
- 不讀 peer SSH credential，不向 peer 建立 SSH session。
- peer unavailable 只使相依 item 回 `unknown/peer_unavailable`；同一 child 的本機 checks 繼續。
- 前端將結果歸在 P2/db，額外顯示「觀察 P1/web」；不得複製到 P1 群組。

---

## 7. 腳本產出策略

### 7.1 第一階段採自足 `script.py`

```text
validated node plan
  -> deterministic compiler
  -> self-contained script.py
  -> existing script_content
  -> existing SSH/SFTP/python3 executor
```

完成後即可移除目前最大的變動性與 Token 成本：現況是每個 node 都呼叫 AI 生成 helper、判定邏輯與 JSON footer，再進行 AI review／可能的 repair；目標則是每個 node 只做本地 compile。

Compiler 必須具備：

- 固定 runtime template。
- canonical plan serialization。
- 相同 compiler version + 相同 plan 產生 byte-for-byte 相同 script。
- server-produced coverage。
- fixed error code、timeout 與 output bounds。
- defense-in-depth static policy/quality validation。

### 7.2 暫不採共用 Runner

`runner.py + check_plan.json` 會同時改變遠端部署、版本協商、快取、清理與 Executor lifecycle。自足 `script.py` 已能先達成 deterministic generation；只有量測顯示 Artifact 體積或傳輸成本真的成為問題時再另案處理。

### 7.3 新流程不得回退 AI 產 Python

定案後，新的 `/script-sets` Save/Create 流程：

- 先執行一次全表 Finalizer，後端套用結果並重建完整 candidate analysis。
- 全部 item 都成為 valid typed plan：依 node partition 後 compile。
- 出現 mixed typed/legacy：回精準 validation error。
- 全部是 legacy flat step：要求重新核查／轉成 typed plan，不自動呼叫舊 AI generation。
- Finalizer 契約錯誤可以有一次 bounded repair，只傳 validation issues 與失敗 item；不得按 node 各自重試，也不得產生 Python repair。

舊的已保存 Artifact 可繼續執行其 immutable `script_content`；這是 read/runtime compatibility，不是新寫入雙軌。

### 7.4 Token 與 request 數量

舊流程近似：

```text
node 數 N ×（AI Python generation + AI review + 可能的 AI repair）
```

新流程固定為：

```text
1 次全表 Finalizer AI
+ 最多 1 次整體／失敗 item 的契約 repair
+ N 次後端本地 deterministic compile
+ 0 次 per-node AI Python request
```

因此 node 數增加只增加後端分組、編譯與執行成本，不再讓 AI Token 隨 node 數近似線性成長。平常老師在 Chat 的對話 request 屬於需求釐清，不應混算成腳本 generation/review/repair request。

---

## 8. Result v2 與 Evidence（待實作）

```json
{
  "schema_version": "teacher_judge_result.v2",
  "metadata": {
    "timestamp": "2026-09-20T00:00:00Z",
    "platform": "linux",
    "plan_version": "teacher_judge_check_plan.v1"
  },
  "checks": [{
    "id": "service.nginx.active",
    "title": "取得 Nginx 服務狀態",
    "status": "pass",
    "judgement_mode": "system",
    "evidence": {
      "kind": "command",
      "summary": "active",
      "content": "active\n",
      "truncated": false
    },
    "raw": {
      "returncode": 0,
      "stdout": "active\n",
      "stderr": "",
      "error_code": null
    }
  }],
  "errors": []
}
```

Status 定義：

| Status | 條件 |
|---|---|
| `pass` | Collector 成功且 system Assertion 成立 |
| `fail` | Collector 成功且 system Assertion 明確不成立 |
| `unknown` | timeout、permission、command missing、parse error、peer unavailable 或 runtime exception |
| `collected` | teacher mode Collector 成功，等待老師查看／判定 |
| `skipped` | 明確前置條件不成立或不適用；v2 先保留 schema，未定義前不由 compiler 自行產生 |

聚合規則：

- system item：任一必要 check `fail` 則 item `fail`；沒有 fail 但有 `unknown`／缺少 check 則 `unknown`；全部 `pass` 才是 `pass`。
- teacher item：全部成功取證時為 `collected`；任一必要取證失敗時為 `unknown`。
- 額外且無 coverage 的 check 放入 `unmapped_checks`，不得猜測歸屬。
- `raw` 是執行除錯資料；`evidence` 是老師要閱讀的受控證據。
- 每個欄位、check 與完整 result 都要有 server-owned 上限；超限應在讀取時停止，而不是讀完整份後才拒絕保存。
- `truncated=true` 必須顯示；若截斷使 Assertion 無法可靠判定，結果只能是 `unknown`。
- peer IP、credential、token 與其他敏感內容必須在 persistence/public projection 前遮蔽。

---

## 9. 安全定案

Chat 與 Finalizer 產生的內容都是不可信 plan；老師 Apply 或按下 Save/Create 也不代表可放寬平台安全政策。安全邊界由後端控制。

### 9.1 Command

保留 `collector.type=command`，但不能只靠關鍵字 denylist 宣稱唯讀。最終 validator 必須採 server-owned allow-by-shape：

- 明確辨識 executable、subcommand 與會改變狀態的參數。
- 拒絕 shell launcher、`-c`/eval 類任意程式碼入口、pipe、redirect、substitution。
- 拒絕寫檔、安裝、修復、服務啟停、權限修改、資料庫寫入與外部網路 side effect。
- 新的能力可由後端擴充 policy/adapter，但不把 `template_key`、`command_key` 重新塞回 plan 契約。

目前 `_dangerous_command_issue()` 主要是 deny pattern，仍不足以證明任意 argv 唯讀；在此安全 gate 完成前，不把 generic command Collector 視為 production-ready。

### 9.2 File

- 只讀、bounded bytes、bounded decode。
- 拒絕 traversal、credential/key、secret 類檔案、device/pseudo filesystem 與 symlink escape。
- `full/head/tail` 必須有真實語意；tail 必須從檔尾 bounded read，不能把「前 N bytes 的最後幾行」當作檔案尾端。
- 不寫入、不刪除、不 chmod、不解壓縮、不執行 binary。

### 9.3 Localhost HTTP

- 只允許 literal `GET/HEAD`。
- hostname 只允許 `localhost`、`127.0.0.1`、`::1`。
- 禁止 credential-in-URL。
- redirect 不跟隨；body 在讀取時即套用上限。

### 9.4 Remote result

Executor 讀取 `result.json`、stderr 與 evidence 時必須使用 bounded read。現況 `_read_remote_text()` 先 `read()` 全檔再檢查 256 KiB，仍有記憶體與傳輸放大風險；需要改成最多讀上限加一個 byte/char 並回明確 `result_too_large`。

---

## 10. 目前尚未完成的收斂項目

### P0：建立 deterministic 核心並接通主流程

1. 新增 typed Collector／Assertion schema、完整 plan validator、deterministic compiler 與 result v2；先用 focused tests 固定契約。
2. 保留 `handleSaveAndCreate()` 現有的全表重新核對 request，明確命名／定位為 Finalizer；輸入完整 rubric 與 topology，輸出 proposal operations 供 server 建立完整 candidate。
3. Finalizer 完成後改呼叫 `createSessionScriptSet()`／`POST /script-sets`，response 改用 set + children。
4. `create_artifact_set()` 保留既有 partition／transaction 外框，但把每個 partition 的 `_build_reviewed_script_for_artifact()` 換成 deterministic compile。
5. typed candidate 在保存後、建立 Artifact 前做完整的 full-plan／per-node validation，提前發現重複 step ID 或跨 item 契約錯誤。
6. 新 script-set 路徑遇到 flat legacy／mixed plan fail closed，不呼叫舊 AI Python generation。
7. UI 成功文案從「通過靜態與 AI 檢查」改成「已通過 Check Plan 與靜態安全檢查」；不得把 Finalizer 說成 AI script review。
8. generic command 採 allow-by-shape 的後端安全驗證，並補 mutation/eval bypass 測試。
9. 遠端 result/stderr 改為 bounded read，避免先讀完整巨大檔案。

### P1：修正 evidence 與 Collector 語意

1. `file_text.tail` 改為真正從檔尾 bounded read；補 full/head/tail、multibyte、oversized、decode、missing 測試。
2. 前端 `checkEvidenceText()` 目前優先回 `summary`，導致 `evidence.content` 雖可達 12000 chars，展開時仍主要落到只有約 4000 chars 的 `raw.stdout`；需提供獨立「查看證據內容」區塊並顯示 truncated。
3. Assertion schema 依 type 嚴格限制 `expected/operator/path` 型別與多餘欄位。
4. 補齊每種 Collector／Assertion 的 true、false、timeout、permission、missing、oversized 與 invalid payload 測試。
5. 明確定義 `skipped` 的 server-owned 前置條件；完成前不要讓 AI 任意產生此狀態。

### P2：相容層清理

1. 新 Artifact 的真實來源以 `policy_check_result_json.source=deterministic_compiler` 判斷；第一階段不為 `source=compiled` 強制新增 DB enum migration。
2. 若既有 response shape 必須保留 `ai_review_result_json`，只能寫入清楚的 deterministic compatibility metadata，不表示真的呼叫 AI reviewer；前端不得以此顯示「AI 已審查」。確認無 consumer 後再另案移除。
3. 經 production 資料與 callsite 盤點後，再刪除新流程不使用的 generation/review/repair prompt、retry UI 與 wrapper；不可在同一批改動直接移除舊 Artifact reader/executor。

---

## 11. 實作順序

### Phase A：契約與 deterministic compiler

- 定義 typed Collector／Assertion、plan version、validation error 與 result v2 schema。
- 實作固定 runtime template、canonical serialization、coverage 與 deterministic compiler。
- 建立 compiler、每種 Collector／Assertion、result parse 與輸出上限的 focused tests。
- 既有 flat rubric／Artifact 保持可讀；新 compiler 不從 prose 或 `detection_method` 猜命令。

### Phase B：Finalizer 與入口切換

- 將既有 `RUBRIC_POLISH_PROMPT` 全表核對收斂為 Finalizer contract；保留老師目前操作與 Chat UI。
- server 將 Finalizer proposal operations 套用到 base rubric，產生完整 candidate，而不是信任模型自行重複整份 JSON。
- 新增／使用 `AiJudgeService.createSessionScriptSet()`。
- `handleSaveAndCreate()` 在 Finalizer 與保存成功後接 `/script-sets`，保存／刷新 set 與 children。
- 在 `create_artifact_set()` 內以 deterministic compile 取代 per-node AI generation/review/repair。
- typed full-plan/per-node validation；必要時只允許一次 bounded contract repair。
- legacy/mixed plan 回自然、可處理的重新核查訊息，並修正 deterministic 成功／失敗文案。

### Phase C：安全與輸出邊界

- command allow-by-shape policy。
- Assertion discriminated validation。
- bounded remote read。
- file path/secret/symlink policy與真正 head/tail。
- 確認 result document、check、evidence、raw 均有一致上限。

### Phase D：結果與 UI 驗收

- 直接顯示 `evidence.content`、kind、truncated 與 error code。
- `collected/unknown/pass/fail/skipped` 顯示與導師 decision。
- batch hierarchy、peer metadata、run_id/vmid review save 對齊。
- mixed success/failure 與 missing check projection。

### Phase E：舊生成路徑退役

- 先確認新 UI 與所有新寫入均只走 typed compiler。
- 盤點既有 legacy rubric、Artifact、API consumer 與 retention。
- 移除「新建 Artifact」的 AI generation/review/repair；保留 immutable 舊 Artifact 執行與 v1 result reader至退役條件成立。

---

## 12. 驗收條件

全部成立才算此定案真正完成：

- 老師詢問與 Chat proposal 維持目前互動方向，不新增腳本專用對話模式。
- Save/Create 只呼叫一次全表 Finalizer；node 數不得增加 Finalizer request 數。
- server 將 Finalizer operations 套用成完整 candidate，且只有 valid typed Collector／Assertion 能進 compiler。
- Save/Create 使用 `/script-sets`，不再使用單一 `/scripts` 作為多機器主入口。
- Plan 保存 canonical node key，不保存 P label、VMID、IP、SSH 或 credential。
- 每個 ready item 都有 valid Collector；system 必有 valid Assertion，teacher 不得有 Assertion。
- 建立多 node Artifact Set 時不發出 script generation/review/repair vLLM request。
- Finalizer 只輸出 Check Plan data，不輸出 Python；若需 repair，最多針對契約失敗 item 做一次 bounded repair。
- 同一 compiler version + canonical node plan 可重現相同 script 與 coverage。
- 一個 executor node 對應一個 child Artifact 與 child Run。
- P2 測 P1 只在 P2 執行與歸類；peer unavailable 不阻斷同 child 的本機 checks。
- command/path/network/timeout/output/secret/redaction 全由 server policy 控制，不能只信 prompt 或老師 Apply。
- result 區分 `pass/fail/unknown/collected/skipped`，且 target completed 不等於 checks 全通過。
- teacher decision 不覆寫原始 evidence/status。
- frontend 以 `student -> node -> item -> checks` 顯示，能查看 bounded evidence content 並保存到正確 child `run_id/vmid`。
- 舊 Artifact/result 可讀；新寫入不再產生 legacy flat step 或 AI-generated Python。
- focused backend/frontend tests、Ruff、build、diff check 通過；live VM/PVE/SSH/browser E2E 邊界另有紀錄。

---

## 13. 建議驗證矩陣

Backend（`backend/`）：

先保留現有基線測試；下列 `test_deterministic_compiler.py` 是 Phase A 應新增的驗收檔，不是目前分支已存在的測試。

```powershell
uv run python -m pytest tests/ai/teacher_judge/test_deterministic_compiler.py -q
uv run python -m pytest tests/ai/teacher_judge/test_multi_machine_execution.py -q
uv run python -m pytest tests/ai/teacher_judge/test_template_commands_chat_normalize.py -q
uv run python -m pytest tests/ai/teacher_judge/test_template_commands_chat_proposal.py -q
uv run python -m pytest tests/ai/teacher_judge/test_script_runs.py -q
uv run ruff check app/ai/teacher_judge app/api/routes/teacher_judge_sessions.py tests/ai/teacher_judge
```

Frontend（`frontend/`）：

```powershell
npm test -- --run src/services/aiJudge.test.js src/pages/course-operations/class-workspace/AiJudgePanel.test.jsx
npm run build
```

靜態測試不能取代：

- live vLLM proposal 輸出品質。
- 真實 PVE/VM/LXC/SSH/SFTP/python3。
- 大型 log 與 result 傳輸上限。
- 瀏覽器完整 Save/Create → Execute All → Review E2E。

---

## 14. 不在本次範圍

- 重做老師詢問、Chat proposal UI 或新增每 node 的 plan-generation AI 階段。
- Windows/PowerShell executor。
- peer SSH、跨學生觀察或任意 peer command。
- 外部 HTTP、任意 socket、寫入式 API。
- 任意 regex/eval/expression language。
- 安裝、修復、重啟、刪除等寫入操作。
- 第一階段部署共用 Runner。
- AI 重新閱讀 runtime evidence 做第二次評分。
- 為每次執行建立 run-scoped 歷史目錄或重複 JSON。

---

## 15. 最終結論

此次定案的重點不是重做老師詢問架構，也不是讓 AI 更會產 Python，而是把 Save/Create 內既有的全表重新核對收斂成一次 Finalizer，再由後端建立唯一 Check Plan：

> **平常 Chat 釐清需求；Save/Create 時 AI 一次整理整份 plan；後端驗證並按 node 分割；固定 compiler 產生 child scripts；既有 Executor 執行；固定 Assertion 或導師核查。**

因此下一步最小且正確的改動順序是：

1. 先建立 typed Check Plan、deterministic compiler 與 result v2，固定可測的後端契約。
2. 將既有全表重新核對正式定義成 Finalizer，不改老師目前詢問與操作方式。
3. 把 Finalizer 後的 Save/Create 從單一 `/scripts` 切到 `/script-sets`，並在既有 per-node loop 內改成本地 compile。
4. 讓新流程對 legacy/mixed plan fail closed，且不再進入 AI Python generation/review/repair。
5. 補強安全、evidence 顯示與完整 E2E 驗收。

聊天提案與詢問方式維持不變；新增的責任邊界只存在於 Save/Create：Finalizer 一次整理全表，後端確定性產出、執行並完整呈現證據。
