# AI Teacher Judge 多機器檢查定案

> 版本：2026-09-20
> 狀態：架構定案；不代表 deterministic compiler 已完成
> 範圍：Teacher Judge 提案、Check Plan、每節點 Artifact、批次執行、結果投影與導師核查

## 1. 一句話定案

> Teacher Judge 保留現有 item、node、artifact set、run batch 與結果投影骨架；AI 在老師 Apply 前產生 item-centric typed Check Plan，後端以單一安全政策驗證並按 canonical `target_node_key` 編譯自足腳本，執行後只做 deterministic Assertion 或導師核查，不再生成、修復或以 AI 判讀 Python runtime 結果。

核心決策：

- 不新增重複的頂層 `nodes[]`；沿用 rubric `items[]`，依每個 item 的 `target_node_key` 分組。
- `target_node_key` 是執行與結果歸屬的唯一機器身分；P1/P2/P3 只是輸入別名與顯示標籤。
- `peer_node_key` 只表示 executor 要觀察的同一位學生之 peer；不建立 peer SSH，也不形成第二份結果。
- 一個 node 一個 child Artifact；同組共用 `artifact_set_id`，同批執行共用 `run_batch_id`。
- 第一階段仍輸出自足的 `script.py`，沿用現有 SSH/SFTP/python3 Executor、資料表與主要 API。
- `script.py` 改由後端 deterministic compiler 產生，不再對每個 node 呼叫 AI generation、review 或 repair。
- system item 由固定 Assertion 產生 `pass/fail`；teacher item 只取證，成功後回 `collected`。
- 舊 Artifact/result 保持可讀可執行；新寫入只使用新契約，不永久雙寫。

## 2. 已查證的現況

以下是 2026-09-20 工作樹中的實際契約，不是未來構想。

| 邊界 | 現況 | 定案影響 |
|---|---|---|
| AI 提案 | `service.py` checklist tools 產生 item-level `target_node_key`、`peer_node_key`、`judgement_mode` 與 flat `check_steps` | 保留 item-level 結構，升級 `check_steps` |
| 機器身分 | `machine_context.py` 將 P 標籤解析成 class-local `node_key` | 持久化、partition、run、result 一律使用 `node_key` |
| Artifact | `partition_analysis_by_target_node()` 已分組；`create_artifact_set()` 已建立每 node 一個 child | 保留 `artifact_set_id` 與 child Artifact |
| 腳本生成 | 每個 partition 仍呼叫 AI generation/review/repair | 這是本計畫要取代的主要 Token 與不確定性來源 |
| Peer | Run 依 `(class_id, student_id, peer_node_key)` 解析 peer IP，只注入 executor runtime context | 保留，且不得把 IP/credential 寫入 plan |
| 執行 | 現有 Executor 經 SSH/SFTP 上傳並以 `python3` 執行 `script_content` | 第一階段不改 transport |
| 結果 | coverage 將 runtime `checks[].id` 投影回 rubric item | 保留此映射 |
| 批次 | 已有 `run_batch_id`、child runs 與 `student -> node -> item -> checks` 投影 | 保留 API 與結果階層 |
| Result v1 | `evidence/raw` 為最多 4000 字字串；status 為 `pass/fail/warning/unknown/skipped` | 新 Artifact 改用 v2；v1 只讀相容 |

重要澄清：現況在學生機器執行後本來就沒有第二次 vLLM 評分。現在的不確定性位於執行前的 Python generation/review/repair，以及 AI 產生的 Python 內自行編碼判定邏輯。新架構移除的是這一層自由生成，不重做已存在的 multi-machine orchestration。

## 3. 不採用的方案

### 3.1 每個 node 各自讓 AI 生成完整 Python

不採用。每增加一個 node，都會重複 helper、JSON footer、例外處理、policy repair 與 reviewer request，Token 和失敗面近似線性增加。

### 3.2 AI 生成一份大型 multi-node Python，再由後端切割

不採用。共用函式、global state、分支與 coverage 會交錯，無法安全依 AST 或文字切成 child scripts。

### 3.3 新增頂層 `nodes[]`

不採用。現行 rubric item 已有 `target_node_key`；再保存 `nodes[].checks[]` 會形成第二份 source of truth。

### 3.4 把 `collect_only` 當 Assertion

不採用。它沒有真假條件；是否由老師判斷應由 `judgement_mode=teacher` 表達。

### 3.5 第一階段部署共用 Runner

暫不採用。`runner.py + plan.json` 會同時改變遠端部署、版本協商與 Executor lifecycle。自足 `script.py` 已能消除 AI code generation；只有實際出現 Artifact 體積或部署成本問題時再另案處理。

## 4. 最終資料流

    老師需求
      -> AI checklist tools
      -> item-centric Check Plan
      -> schema / topology / safety validation
      -> 老師 Apply
      -> 依 target_node_key deterministic partition
      -> 每 node deterministic compile 成 child script.py
      -> artifact_set
      -> 老師觸發 execute all
      -> run_batch / child runs
      -> 每位學生的 executor SSH/SFTP/python3
      -> Collector 取得 observation
      -> Assertion 固定判定
      -> result v2 validation
      -> coverage projection
      -> student -> executor node -> rubric item -> checks
      -> 導師查看或核查 collected 項目

AI proposal、老師 Apply、Artifact 可執行、老師啟動 run 是不同狀態。AI 回覆文字、plan validation 或 compiler 成功都不能自行啟動學生機器上的命令。

## 5. Canonical Check Plan

### 5.1 不另建平行文件

Check Plan 就是 rubric analysis 中可執行 item 的正式結構：

    {
      "schema_version": "teacher_judge_check_plan.v1",
      "items": []
    }

`total_items`、`auto_count` 等摘要若保留，只能由 `items[]` 重算，不能成為執行真相。

### 5.2 Item 契約

    {
      "id": "nginx-service",
      "title": "Nginx 必須啟動",
      "detectable": "auto",
      "judgement_mode": "system",
      "target_node_key": "web",
      "peer_node_key": null,
      "check_steps": []
    }

規則：

- `id` 是 rubric item 身分。
- `target_node_key` 必須是目前班級實際存在的 `node_key`。
- `peer_node_key` 選填，且不可等於 `target_node_key`。
- `detectable=auto` 代表可執行取證；`partial/manual` 不進 compiler，但仍保留在 rubric/UI。
- `judgement_mode=system` 必須有 Assertion。
- `judgement_mode=teacher` 必須省略 Assertion，Collector 成功後回 `collected`。
- 現有 `judgement_mode=ai` 是舊名稱；新寫入正規化成 `system`，舊資料只在 read boundary 映射 `ai -> system`。

### 5.3 Check step 契約

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
        "expected": "active"
      }
    }

規則：

- `step.id` 在同一份 plan 內唯一且穩定，不使用位置 ID。
- Step 巢狀在單一 rubric item 下，不重複 `rubric_item_ids`、`target_node_key` 或 `peer_node_key`。
- coverage 由 `parent item.id -> step.id` 確定性產生，不交給 AI 另寫。
- Compiler 只能使用 validated plan；不得從 title、detection_method 或自然語言再推論命令與判定。
- 一個 step 只有一個 Collector 與零或一個 Assertion；第一版不加入任意布林 expression tree。

## 6. Collector v1

第一版只提供五種 Collector：

| Type | 必要欄位 | 用途 |
|---|---|---|
| `command` | `argv`、`timeout_seconds`；`cwd` 選填 | 版本、服務狀態、程式入口 |
| `file_text` | `path`、`read_mode`、`max_chars`；`lines/encoding` 選填 | 設定檔、結果檔、文字 log |
| `file_stat` | `path` | 檔案存在、類型與大小 |
| `localhost_http` | `method`、`url`、`timeout_seconds`、`max_chars` | 只讀 localhost Web/API |
| `peer_ping` | `timeout_seconds` | executor 對宣告 peer 的連通性 |

`system_info` 第一版不新增；先由安全 `command` 表達，出現重複且穩定需求後再升成專屬 Collector。

### 6.1 Command policy

- 只接受非空 argv array，不接受 command string。
- 禁止 shell launcher、`shell=True`、pipe、redirect、command substitution 與 destructive subcommand。
- 把現有 `script_policy.py` command 規則抽成共用 validator；proposal 與 compiler 使用同一 source of truth。
- 啟動失敗、timeout、permission denied、command not found 回 `unknown`。
- 非零 return code 搭配 `returncode_equals` 時可明確產生 `fail`；其他 assertion 不得因 stdout 碰巧符合而忽略非零 code，應回 `unknown`。

### 6.2 File policy

`file_text` 支援 `full/head/tail`，先限制讀取 bytes 再 decode。路徑必須經 server-owned policy 正規化，拒絕 traversal、敏感 credential/key、device/pseudo filesystem 與 symlink escape。只讀文字，不寫入、不刪除、不 chmod、不解壓縮、不執行、不展開 binary。

`max_chars` 有平台上限，AI 或老師只能縮小。`file_stat` 只回必要 metadata；`exists` Assertion 應搭配它。`file_text` 找不到檔案是取證失敗，回 `unknown`，不偷換成 `fail`。

### 6.3 Localhost HTTP policy

- 第一版只允許 `GET/HEAD`。
- URL 必須是 literal localhost、`127.0.0.1` 或 `::1`。
- redirect 逐跳驗證 loopback，禁止導向外部或 metadata service。
- response body、header 與總輸出均有上限。

### 6.4 Peer policy

`peer_node_key` 只存在 parent item。後端依 `(class_id, student_id, peer_node_key)` 解析 runtime IP；IP 只注入 child runtime context，不持久化到 plan，不回傳前端。不讀 peer SSH credential，也不向 peer 建立 SSH session。

peer 缺失或未就緒只使相依 item 回 `unknown/peer_unavailable`；同一 child 的本機 checks 繼續執行。

## 7. Assertion v1

| Type | 欄位 | 語意 |
|---|---|---|
| `returncode_equals` | `expected` integer | process/ping return code 相等 |
| `text_equals` | `expected`、`normalize` 選填 | canonical text 完整相等 |
| `text_contains` | `expected`、`case_sensitive` 選填 | canonical text 包含固定字串 |
| `number_compare` | `operator`、`expected` | `eq/ne/gt/gte/lt/lte` |
| `json_path_equals` | `path`、`expected` | 已解析 JSON 的受限 path 相等 |
| `exists` | `expected` boolean | `file_stat.exists` 相等 |

第一版刻意不支援任意 `eval`、Python callback、regex、expression language 或 `line_exists`。regex 會帶來 ReDoS 與 runtime 差異；確有需求時再用受控 parser 或預先定義 pattern ID。

Assertion 只能讀 Collector 的 canonical observation，不能直接讀 runtime context、credential 或其他 step 的任意輸出。

## 8. Result v2

    {
      "schema_version": "teacher_judge_result.v2",
      "metadata": {
        "timestamp": "2026-09-20T00:00:00Z",
        "platform": "linux",
        "plan_version": "teacher_judge_check_plan.v1"
      },
      "checks": [
        {
          "id": "service.nginx.active",
          "title": "取得 Nginx 服務狀態",
          "status": "pass",
          "judgement_mode": "system",
          "evidence": {
            "kind": "command",
            "summary": "Nginx 狀態為 active",
            "content": "active",
            "truncated": false
          },
          "raw": {
            "returncode": 0,
            "stdout": "active\n",
            "stderr": "",
            "error_code": null
          }
        }
      ],
      "errors": []
    }

### 8.1 Status

| Status | 條件 |
|---|---|
| `pass` | Collector 成功且 system Assertion 成立 |
| `fail` | Collector 成功且 system Assertion 明確不成立 |
| `unknown` | timeout、權限、工具缺失、parse 失敗、peer unavailable 或 runtime exception |
| `collected` | teacher mode Collector 成功，等待老師查看或判定 |
| `skipped` | 此項目明確不適用目前 target/template |

新版不再產生 `warning`。舊 v1 `warning` 仍由相容 reader 顯示為待導師核查，不改寫歷史。

### 8.2 Evidence、raw 與聚合

- `evidence` 是老師可讀、依 Collector typed 的證據；`raw` 是除錯資料。
- 每個文字欄位、每個 check、每份 result document 都要有獨立上限，不能只依賴 SSH stdout 16 KiB excerpt。
- `truncated=true` 必須明示；截斷後不足以判定時只能回 `unknown`。
- peer IP、credential、token 與敏感路徑內容在持久化及 public projection 前由 server 再遮蔽。
- system item：任一必要 check `unknown` 則 item `unknown`；否則任一 `fail` 則 `fail`；全部 `pass` 才是 `pass`。
- teacher item：Collector 全部成功則 item `collected`；取證失敗則 `unknown`。
- 導師 decision 存在 `teacher_review.decisions`，不覆寫原始 machine result。
- target `status=completed` 只代表腳本執行完成，不代表所有 checks 通過。

## 9. Multi-machine 與 coverage

### 9.1 Partition

後端在完整驗證後，以 canonical `target_node_key` 分組：

    items[web] -> child artifact(web)
    items[db]  -> child artifact(db)

P 標籤只在 public projection/UI 依目前 `sort_order` 計算，不能作 Artifact、Run 或 Result identity。

### 9.2 Coverage

    rubric item.id
      -> nested check_steps[].id
      -> compiler-produced coverage
      -> runtime checks[].id
      -> server projection
      -> frontend item/checks

缺少已宣告 check ID 時，對應 item 回 `unknown`。額外 check 放入 `unmapped_checks` 並視為 contract violation，不猜 rubric 歸屬。

### 9.3 P2 測 P1

若 P2/P1 的 canonical keys 是 `db/web`：

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

只建立並執行 `db` child；結果只歸 `db` node，UI 額外顯示「觀察 P1 / web」。不得複製到 `web` 群組。

## 10. Compiler 與 Artifact

第一階段：

    validated node plan
      -> deterministic compiler
      -> self-contained script.py
      -> existing script_content
      -> existing SSH/SFTP/python3 executor

Compiler 由固定、受測試的 runtime template 加 canonical JSON plan 組成，不用字串拼接產生任意 Python。相同 compiler version 與相同 canonical plan 必須產生 byte-for-byte 相同 `script_content`。

保留：

- `artifact_set_id`
- `target_node_key`
- `source_analysis_revision`
- `rubric_snapshot_json`
- `script_content`
- `version/status`
- `policy_check_result_json.coverage`

第一階段不新增第二份 Check Plan table 或 run-scoped Artifact 目錄。`schema_version`、compiler version 與 canonical plan 放在 Artifact snapshot/validation metadata。

語意清理：

- 新編譯 Artifact 的 `source` 不應標成 `ai_generated`；新增 `compiled` 並以 Alembic migration 處理 enum。
- `ai_review_result_json` 不得偽造 AI reviewer 已執行。過渡期明示 `skipped: deterministic_compiler`，前端不再以它作 gate，之後移除無 consumer 的欄位。
- `policy_check_result_json` 保存 plan validation、compiler version、coverage 與 defense-in-depth static gate。

原子性：

- 所有 partitions 先在記憶體完成 schema、安全與 compile 驗證。
- 任一 child 失敗則整個 set 零寫入，回傳 item/step/node 精準錯誤。
- 全部成功後才在單一 transaction 寫 children。
- 不先保存半組 Artifact，再補償式刪除。

## 11. Run、投影與導師核查

- `create_script_run_batch()` 仍為每個 approved child 建立 child run，共用 `run_batch_id`。
- child runs 可獨立成功或失敗；batch 用 `completed_with_failures` 表示局部失敗。
- 建立 batch 是 transaction；任一 child run 建立失敗則全部 rollback。
- 執行 target 只能由 server-owned membership、node mapping、VMID、Resource owner、live status 與 SSH key 解析。
- frontend hierarchy 固定為 `student -> executor node -> rubric item -> checks`。
- `peer_node_key` 是 item metadata，不參與 partition；`vmid` 是當次 target，不是 plan identity。
- UI 顯示 `display_label + machine name`，操作仍使用 `node_key`。
- system check 文案改為「系統已判定」，不再稱「AI 已判定」。
- teacher check 的 `collected` 顯示「待導師核查」；`unknown` 顯示取證失敗原因。
- 導師核查 API 仍以 `(run_id, vmid)` 定位；batch row 必須攜帶正確 child `run_id` 與 executor `vmid`。

## 12. 遷移與相容性

### 12.1 不改寫歷史

- 舊 AI-generated Artifact immutable，沿現有流程執行。
- 舊 `teacher_judge_result.v1` 由 reader adapter 驗證與顯示。
- 不批次改寫既有 rubric JSON、Artifact 或 Run result。

### 12.2 新寫入單一契約

- Cutover 後 proposal tool、API schema、normalization 與 persistence 只寫 `teacher_judge_check_plan.v1`。
- read boundary 可接受舊 flat step，但不得重新輸出成新寫入格式。
- 不永久雙寫同義欄位。

### 12.3 舊 flat step

舊 step 只有 `argv/cwd/timeout_seconds`，沒有可信 assertion：

1. 已存在 Artifact 繼續用已保存的 `script_content`。
2. 重新製作 Artifact 時，要求重新分析成完整 typed plan。
3. 若只做機械轉換，最多轉成 `command + judgement_mode=teacher`；不得從 title 或舊 generated Python 猜 assertion。

舊 Artifact 的 regenerate 改為依目前 rubric revision 建新 compiled version，不 repair 舊 AI Python。revision 不符時沿用 optimistic conflict。

## 13. 實作階段

### Phase 0：封住現有 multi-machine baseline

- 完成工作樹中 batch review UI 與 service API 串接。
- 以測試鎖定 `reviewState`、`machineNodes` 傳遞、batch/legacy fallback 與 review save 的 `run_id/vmid` 定位，避免後續 compiler 改動造成回歸。
- 跑現有 multi-machine、projection、frontend review focused tests。
- 此階段不混入 compiler，避免無法區分 orchestration regression 與新 engine 問題。

### Phase 1：Check Plan schema 與 proposal

修改 `schemas.py`、`service.py`、`prompt.py`、`machine_context.py` 及 proposal/apply tests：

- typed Collector/Assertion discriminated unions。
- `schema_version` 與 `judgement_mode=system|teacher`。
- P label -> node_key 正規化。
- item-wise validation error；一個錯誤不移除其他可用 proposal。
- Apply 後只保存 canonical plan。

### Phase 2：共用安全 validator 與 compiler

- 抽出共用 command/path/network policy。
- 新增集中式 plan validator 與 deterministic compiler。
- `script_artifact_service.py` 以 compile 取代 per-node AI generation/review/repair。
- 保留 static policy 作 defense in depth。
- coverage 由 compiler 產生。
- 新增 `compiled` Artifact source migration。

成功條件：建立多 node Artifact set 時，完全不發出 script generation/review/repair vLLM request。

### Phase 3：Result v2 與 Executor validation

- 新增 compiler runtime template。
- 擴充 `script_policy.py` result v2 validator。
- `script_executor_service.py` 實作總輸出上限、parse 與穩定 error code。
- 保留 v1/v2 read adapter。

成功條件：每個 Collector/Assertion 的正常、false、timeout、permission、missing、truncated 都有固定 status。

### Phase 4：Projection 與 frontend

- `script_run_service.py` 支援 v2 aggregation/projection。
- teacher review validation 依 `judgement_mode` 與 `collected/unknown`。
- `AiJudgePanel.jsx` 顯示 batch hierarchy、evidence/raw 與 collected。
- 沿用 `aiJudge.js` script-set/run-batch API。

### Phase 5：刪除退役生成路徑

確認沒有新 callsite 後移除：

- 新 Artifact 的 AI Python generation。
- AI reviewer 與 Python repair loop。
- 只服務新生成流程的 prompt、retry UI 與 wrapper。
- 新 UI 不再使用的 `ai_review_result_json` surface。

舊 Artifact executor 與 v1 reader 保留到 retention 到期；這是歷史相容，不是新寫入雙軌。

## 14. 測試矩陣

### Schema / normalization

- P 標籤正規化成唯一 node_key。
- 未知、歧義或相同 target/peer 被拒絕。
- system 缺 assertion、teacher 帶 assertion 被拒絕。
- 重複 step ID、未知 Collector/Assertion、額外欄位被拒絕。
- partial/manual item 不進 compiler且不從 rubric 消失。

### Safety

- shell launcher、destructive command、write network method 被拒絕。
- traversal、敏感 key、symlink escape、binary/oversized file 被拒絕或安全回 unknown。
- localhost redirect 到非 loopback 被拒絕。
- peer IP 只流入 ping argv，public result 無 IP/credential。

### Compiler / runtime

- 每種 Collector/Assertion 有 golden test。
- 相同 plan + compiler version 產生相同 bytes。
- coverage 精確對應 parent item 與 step ID。
- 任一 node validation fail 時 Artifact set 零寫入。
- monkeypatch vLLM client fail-fast，證明 compile path 無 AI call。
- command 0/非 0、timeout、not found、permission denied。
- file full/head/tail、not found、decode error、truncated。
- HTTP 2xx/非 2xx、timeout、invalid JSON、oversized body。
- teacher success -> collected；system true/false -> pass/fail。
- 超限、missing check、extra check、invalid status 被 validator 阻擋。

### Multi-machine / frontend

- web/db 產生兩 child artifacts 與一 set。
- 每位學生的 db child 只解析同一學生的 web peer。
- peer missing -> 相依 item unknown，本機 item續跑。
- batch 局部失敗 -> `completed_with_failures`。
- projection 無重複、無跨 node 歸類、無 peer IP。
- 最新 run 是 batch 時讀 batch；legacy run 仍可讀。
- row key 使用 `student_id|node_key`，核查保存使用該 row 的 `run_id/vmid`。
- collected、unknown、pass、fail、skipped 與 evidence/raw 顯示正確。

## 15. 驗證順序

Backend 從 `backend/` 執行：

    uv run python -m pytest tests/ai/teacher_judge/test_template_commands_chat_proposal.py -q
    uv run python -m pytest tests/ai/teacher_judge/test_script_policy.py -q
    uv run python -m pytest tests/ai/teacher_judge/test_script_review_coverage.py -q
    uv run python -m pytest tests/ai/teacher_judge/test_multi_machine_execution.py -q
    uv run python -m pytest tests/ai/teacher_judge/test_script_runs.py -q
    uv run ruff check app/ai/teacher_judge app/api/routes/teacher_judge_sessions.py tests/ai/teacher_judge

Frontend 從 `frontend/` 執行：

    npm test -- --run src/services/aiJudge.test.js src/pages/course-operations/class-workspace/AiJudgePanel.test.jsx
    npm run build

最後執行 `git diff --check`。有 migration 時另查 `alembic heads/current/check`，只對已確認的 test/local DB 執行 upgrade。無遠端 VM/PVE/SSH 或瀏覽器環境時，必須明確回報尚未驗證 live execution/E2E。

## 16. 驗收條件

全部成立才算完成：

- Plan 保存 canonical node_key，不保存 P 標籤、VMID、IP 或 SSH。
- ready item 都有 validated Collector；system 有 Assertion，teacher 沒有。
- 建立 Artifact set 不呼叫 script generation/review/repair AI。
- 相同 plan 可重現相同 script 與 coverage。
- 一 executor node 對應一 child Artifact 與 child Run。
- P2 測 P1 只在 P2 執行與歸類。
- peer 不可用不阻斷同 child 本機 checks。
- command、path、network、timeout、output size、credential redaction 由 server policy 控制。
- result 區分 pass、fail、unknown、collected、skipped。
- teacher decision 不覆寫原始 evidence/status。
- frontend 以 student/node/item/check 顯示 batch，並保存到正確 child target。
- 舊 Artifact/result 可讀；新寫入不再產生舊 flat step 或 AI-generated Python。
- focused tests、Ruff、frontend build 與 diff check 通過；live 驗證邊界有紀錄。

## 17. 不在本次範圍

- Windows/PowerShell executor。
- peer SSH 或跨學生觀察。
- 外部 HTTP、任意 socket、寫入式 API。
- 任意 regex/eval/expression language。
- 修改、修復、安裝、重啟、刪除等寫入動作。
- 共用 Runner 的遠端版本管理與快取。
- AI 重新閱讀 runtime evidence 做第二次評分。
- 為每次執行建立 run-scoped 歷史目錄或重複 JSON。
