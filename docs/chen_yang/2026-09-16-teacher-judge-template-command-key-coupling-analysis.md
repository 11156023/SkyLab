# Teacher Judge：template_key / command_key 連結深度追查分析

> 日期：2026-09-16
> 範圍：`backend/app/ai/teacher_judge/`、`backend/app/ai/pve_template/`、
> `backend/app/models/`、`backend/app/alembic/versions/`
> 目的：確認 template_key / command_key 目前的接收來源、與系統的連結深度，
> 評估「整體收斂成簡單提示詞（如：你是 Linux 操作人員…）」的可行性與影響範圍。

---

## 1. template_key 目前接收的資訊

### 1.1 值域

```python
# backend/app/ai/teacher_judge/template_command_service.py:12
SUPPORTED_TEMPLATE_KEYS = {"linux", "python", "n8n", "postgresql"}
```

### 1.2 四條接收入口

| # | 入口 | 位置 | 內容 |
| --- | --- | --- | --- |
| 1 | 舊版 script 建立請求 | `TeacherJudgeScriptCreateRequest.template_key`（schemas.py:307，預設 `linux`） | 使用者/前端傳的字串；normalize 小寫後 route 再驗證必須在 SUPPORTED（teacher_judge_scripts.py:60-66, 94） |
| 2 | Session 建立請求 | `TeacherJudgeSessionCreateRequest.environment_keys`（schemas.py:163, 183-193） | 老師勾選的**環境清單**（多值），必須 ⊆ SUPPORTED_TEMPLATE_KEYS |
| 3 | Blank 檢查表建立 | `create_blank_file()`（file_service.py:228） | `template_key = environment_keys[0]`（取第一個當主環境），存入 `TeacherJudgeFile.template_key`（單值）+ `environment_keys`（多值 JSON） |
| 4 | LLM 提案 check step | `TeacherJudgeRubricCheckStep.template_key`（schemas.py:49） | **僅是環境提示，可省略**；後端依 `command_key` 唯一匹配自動補齊/修復（template_command_service.py:199-219、prompt.py:82），不會因缺 key 拒絕提案 |

其他來源：fork/clone 繼承 `source.template_key`（file_service.py:279）；上傳型檔案
（`source_type="uploaded"`）目前無新建立路徑，屬 legacy 資料。

### 1.3 template_key 的下游用途

1. `get_enabled_template_commands(session, template_key)` → 查
   **`teacher_judge_template_commands` 表**（不是 `ai_pve_templates`），組 LLM
   prompt 的 command catalog（service.py:1985-1991）
2. `validate_check_steps()` 驗證提案的 `(template_key, command_key)` 引用
3. Script artifact 的 `rubric_snapshot` 標記、run metadata、AI usage 紀錄的
   `preset` 欄位（script_executor_service.py:496）
4. Fork/clone 時繼承 `source.template_key`
5. Session 公開欄位回傳（session_service.py:992）

### 1.4 template_key 與 `ai_pve_templates` 的關係（重要結論）

- Teacher Judge 的 `template_key` **目前不讀 `ai_pve_templates`**；它讀的是
  `teacher_judge_template_commands`（command catalog 表）。grep 全 backend 證實
  `AIPVETemplate` 僅被 `app/ai/pve_template/` feature 引用。
- 兩者只是 key 命名空間重疊（n8n/python/postgresql；Teacher Judge 多一個
  `linux`），語意上是同一批「機器角色」概念，但**沒有 FK、沒有同步機制、
  沒有共用 prompt**。

### 1.5 `ai_pve_templates` 資料表目前用途

- Model：`AIPVETemplate`（models/ai_pve_template.py）— AI PVE 機器角色模板，
  只服務隔離的 PVE 診斷 chat 功能（`/api/v1/ai/pve-template/*`）
- 欄位：`template_key`（unique）、`display_name`、`description`、**`system_prompt`**
  （機器角色診斷指示）、`enabled`
- 資料流：`{vmid, template_key}` targets → 查表（enabled）→ 授權 VMID →
  `compose_system_prompt()`（pve_template/prompts.py:51-90）把機器角色
  system_prompt 注入 PVE 診斷對話（PVE read-only tools + ssh_exec 確認攔截）
- Migration：`aipve01` 建 n8n/python/postgresql 種子；`aipve02` 幫 postgresql
  追加 `su - postgres` 身分指令
- 明確**非授權性質**：不含 VM 身分、連線資訊或命令授權

---

## 2. command_key 來源

`command_key` 是**平台預先登錄的受控能力 ID**，全部來自
`teacher_judge_template_commands` 表（unique `(template_key, command_key)`），
共三個注入點：

### 2.1 Migration 種子資料（主要來源）

| Migration | command_key | template_key |
| --- | --- | --- |
| `tjtc01_add_teacher_judge_template_commands.py`（2026-05-30，建表 + 13 筆種子） | `linux.os_info`、`linux.kernel`、`linux.disk_usage`、`linux.memory_usage`、`linux.listening_ports`、`linux.processes` | linux |
| 同上 | `python.version`、`python.pip_list`、`python.processes`、`python.listening_ports` | python |
| 同上 | `n8n.port_check`、`n8n.http_check`、`n8n.process_check`、`n8n.docker_check` | n8n |
| `tjpy01_add_python_entrypoint_command.py` | `python.run_entrypoint`（execution 類） | python |
| `tjpg01_add_postgresql_teacher_judge_commands.py`（2026-08-26） | `postgresql.version`、`postgresql.readiness`、`postgresql.service_status` | postgresql |

每筆帶固定 `command_template`（如 `cat /etc/os-release`）、`category`、
`description`、`risk_level`；`on_conflict_do_nothing` 不覆蓋既有資料。

### 2.2 程式碼 hardcode 的通用命令

`GENERAL_COMMAND = system.run_command`（template_command_service.py:73-87，
template_key=linux）— 跨模板通用能力；`get_enabled_template_commands(...,
include_cross_template=True)` 時若 DB 沒有此列就自動附加，作為沒有專用檢查時
的 fallback（單一 argv + cwd + timeout）。

### 2.3 「不是」來源的兩方

- **LLM**：prompt 明文禁止發明新 `command_key`（prompt.py:82）；提案經
  `validate_check_steps()` 驗證，未知的 step 直接丟棄並記
  `unknown_command` issue（template_command_service.py:210-218）
- **老師**：不指定也不需指定技術參數

### 2.4 資料流總結

```text
migration 種子 + GENERAL_COMMAND
  → teacher_judge_template_commands 表
  → get_enabled_template_commands(template_key)
  → format_template_commands_for_prompt() 注入 LLM system prompt
  → LLM 提案引用 command_key
  → validate_check_steps 驗證/修復
  → 腳本生成器按 command_key 產生受管腳本
  → 執行時依 script_content 執行（不看 command_key）
```

---

## 3. command_key 與系統的連結深度

**結論：中深連結，但全部集中在「提案與生成階段」；執行期完全不看
command_key。**

| 層 | 連結程度 | 說明 |
| --- | --- | --- |
| 執行期 | ❌ 零連結 | `script_executor_service.py` 全檔沒有 command_key。`_execute_target_script()`（:234）只是 SFTP 上傳 `script_content` + SSH 執行，拿 exit code/stdout/stderr。**command_key 不是 runtime dispatch 表** |
| 生成期 | 中 | `generate_script_content()`（script_artifact_service.py:798）把 template_commands snapshot（`command_template`、`description`、`risk_level`）當 prompt 上下文交給 LLM 產腳本；生成 prompt（:149-150）與 AI reviewer prompt（:218-219）有針對 `run_command` / `run_entrypoint` 的明文規則 |
| 驗證門檻 | 🔴 最深（3 層） | ① 提案驗證 `validate_check_steps`：`(template_key, command_key)` 必須命中 enabled catalog，否則丟棄 ② 生成阻擋 `automation_support.py:50-79`：**hardcode 兩個特殊 command_key**（`python.run_entrypoint` 必填 cwd+argv；`system.run_command` 必填 argv）③ 靜態政策 `script_policy.py`（AST：argv list、timeout 必填、禁 shell=True/Popen）+ `script_quality_validator.py` + AI reviewer + coverage mapping |
| 資料層 | 中 | `teacher_judge_template_commands` 表 + 3 個 migration 種子 + hardcode `GENERAL_COMMAND` |
| 周邊 | 淺 | AI usage `preset` 欄位、結果分析 metadata（script_result_analysis_service.py:101）、前端 check_steps / detectable 三態顯示 |

---

## 4. 「整體收斂成簡單提示詞」的可行性評估

### 4.1 關鍵發現

1. **安全底線其實不在 catalog**——腳本的安全主要來自 `script_policy.py` 的
   AST 靜態檢查（獨立於 command_key：argv list、timeout 必填、禁 shell=True、
   禁 Popen）。就算砍掉整個 catalog 機制，只要保留這層，生成的腳本仍有安全
   保證。
2. **catalog 真正提供的價值**是「提案階段的自由度限制」：LLM 只能引用
   已註冊能力、結構化參數（argv/cwd/timeout 1-300）、缺資訊時走
   `missing_information` gap UX，而不是直接產任意 shell 字串。
3. **想要的模式已存在於 repo**：`ai_pve_template` feature 就是「role
   system_prompt（你是 N8N/Python/PostgreSQL 機器…）+ 工具執行 + 後端確認
   攔截」，沒有任何 command catalog。
4. 換句話說：**生成階段的嚴格度主要來自 3 層審查門檻
   （policy AST / quality / AI reviewer），不是 catalog 本身。**

### 4.2 收斂可砍掉的

- command catalog 表與 migrations（不需回滾，資料留著無害）
- `check_steps` 結構與參數驗證（`validate_check_steps`、
  `sanitize_check_step_parameters`、`coerce_timeout_seconds`）
- `automation_support.py` 的 blockers（manual/partial 阻擋、
  missing_step_information）
- `detectable` auto/partial/manual 三態與 `missing_information` gap UX
- `teacher_judge_template_commands` 相關查詢與 prompt 注入

### 4.3 建議保留的安全底線

- `script_policy.py` AST 靜態檢查（與 command_key 無關的獨立安全層）
- `script_quality_validator.py` 質量驗證
- AI reviewer 審查
- 執行期 SSH 隔離（SFTP 上傳 + exec）與 approved-only 執行閘門
- timeout 上限（`MAX_TIMEOUT_SECONDS`）

### 4.4 收斂後的新流程（草案）

```text
老師描述檢查項目（自然語言）
  → chat_with_rubric 產生檢查表（items + detection_method，無 check_steps）
  → 生成腳本：system prompt = 「你是 <環境角色> 操作人員…」
     + 角色來源可選 ai_pve_templates.system_prompt（n8n/python/postgresql）
     + 唯讀安全規則 + rubric items + missing info
  → script_policy AST 檢查 + quality validator + AI reviewer（不變）
  → 教師核准 → SSH 執行（不變）
```

### 4.5 影響範圍估算

- Backend（約 6-8 檔）：
  - `schemas.py`：`TeacherJudgeRubricCheckStep`、detectable 三態可縮
  - `template_command_service.py`：可整檔刪除（保留 timeout 常數）
  - `automation_support.py`：可整檔刪除
  - `service.py`：提案工具鏈與 `validate_check_steps` 呼叫點
  - `script_artifact_service.py`：`SCRIPT_GENERATION_SYSTEM_PROMPT`、
    `AI_REVIEWER_SYSTEM_PROMPT` 改為角色提示詞版
  - `prompt.py`：移除 template command context 說明
- 前端：rubric 編輯器的 check_steps/detectable 顯示
- Tests：`test_teacher_judge_*` 多檔需改寫
- Migration：不回滾；`teacher_judge_template_commands` 資料無害保留
- AI usage：`preset` 欄位改用環境 key 或角色 key（紀錄語意不變）

### 4.6 風險與假設

- 假設：教學場景仍希望「檢查表 → 腳本取證」兩段式；若改成單段式
  （直接從自然語言生成腳本），blocker UX 消失，老師需自行判斷項目是否可自動化。
- 風險：失去 catalog 後，LLM 生成腳本的檢查範圍約束完全落在 prompt 規則與
  靜態檢查上；`script_policy.py` 是 AST 層、管不到語意範圍擴張
  （如 argv 換成另一個查更廣的指令），需靠 AI reviewer 補位。
- 假設：`ai_pve_templates.system_prompt` 的角色描述可作為生成 prompt 的角色
  來源（目前兩表沒有連接，需新加查表）。

---

## 5. 附帶改動：班級機器對照系統範本名稱（2026-09-17 追加）

> 背景：追查「班級管理 → 這堂課固定使用什麼機器 → 把系統資訊撈回對照」時，
> 發現 `machine_nodes` 只回 `source_template_id`（UUID），要對照回系統範本
> 名稱得再多一次請求。以下改動讓單一請求就能撈回對照資訊。

### 5.1 改動內容

`backend/app/api/routes/teaching_classes.py`：

- 新增 `_template_names_for_nodes()`（批次把節點的 `source_template_id`
  查成 `vm_templates.name`）與 `_machine_node_dump()`（節點序列化統一補欄位）
- `_serialize`（詳情頁 `GET /teaching-classes/{class_id}`）與
  `_serialize_list`（列表頁 `GET /teaching-classes`）的 `machine_nodes`
  每筆新增 **`template_name`** 欄位
- 列表頁整頁只多 1 次 template 查詢（無範本節點時 0 次），
  `test_query_count_does_not_grow_with_classes_or_weeks` 的常數查詢保證不受影響

### 5.2 對照規則

| 節點來源 | template_name | 來源識別 |
| --- | --- | --- |
| `source_type="template"` | `vm_templates.name`（由 `source_template_id` 查） | `source_template_id`（UUID，FK） |
| `source_type="custom"` | `null` | `custom_image_ref`（自訂 LXC=vztmpl volid；自訂 VM=PVE 範本 VMID） |

回傳範例：

```json
{
  "node_key": "web-server",
  "name": "Web 伺服器",
  "resource_type": "qemu",
  "source_type": "template",
  "template_name": "Ubuntu 24.04 教學母機",
  "source_template_id": "uuid-..."
}
```

### 5.3 相關事實（本次追查確認）

- 班級機器規格鎖定：`spec_change_service._reject_fixed_resource`（:194-206）
  對 `allocation_scope="teaching_class"` 的機器直接拒絕規格變更
  （`spec_change.class_machine_spec_fixed`）；手動改機器清單也被
  `PUT /{class_id}/machines` 擋下（`machinesManagedByCourseEnvironment`）
- 機器 ↔ 範本的真正連結是 `pve_vmid`／`resources.template_id`，
  不是顯示名稱；`vm_templates` 的 `pve_vmid/node/storage/resource_type/
  default_disk` 不可更新，有克隆子機時不可刪除
- 建機當下的規格快照在 `BatchProvisionJob.template_params`
  （`GET /batch-provision/{job_id}/status` 的 `BatchProvisionJobSpec`：
  `vm_template_id` / `ostemplate` / `template_id` / `os_info`）

### 5.4 驗證

- `tests/api/routes/test_teaching_class_list_serializer.py`、
  `tests/test_teaching_class_resource_usage.py`、
  `tests/services/test_teaching_class_orchestration.py`、
  `tests/services/test_class_resource_governance.py` 全數通過（32 tests）
- `ruff check` 通過（檔內既有未格式化段落非本次改動）
- 前端 `normalizeClass` 以展開傳遞節點欄位，不需改動即可取得 `template_name`

### 5.5 補充：詳情回應的完整形式（多台機器 + 連結圖譜，2026-09-17）

`GET /teaching-classes/{class_id}`（`_serialize`）**單一呼叫**同時帶回
機器清單、機器類型與連結圖譜：

```jsonc
{
  "...班級本體 (TeachingClass 欄位全量)": "...",
  "member_count": 30,

  "machine_nodes": [            // 多台允許；unique (class_id, node_key)
    {
      "node_key": "web-server",
      "source_type": "template",
      "source_template_id": "uuid-...",
      "template_name": "Ubuntu 24.04 母機",   // 本次新加（custom 節點為 null）
      "name": "Web 伺服器",                    // 老師取的機器名
      "resource_type": "qemu",                 // 機器類型：qemu=VM / lxc=LXC
      "cpu": 2, "memory_mb": 2048, "disk_gb": 20,
      "network": "lab-net", "sort_order": 0, "batch_job_id": null
    }
  ],
  "students": [               // 每位學生實際開出的機器（多台 × 多人）
    { "email": "...", "full_name": "...",
      "machines": [ { "machine_node_id": "...", "vmid": 1013,
                      "status": "completed", "error": null } ] }
  ],
  "ready_machines": 28,
  "total_machines": 90,        // = 學生數 × 機器數
  "provision_jobs": [ { "id": "...", "status": "...", "total": 90,
                        "done": 88, "failed_count": 2 } ],

  "course_environment": { "id": "...", "version_id": "...",
                          "name": "...", "version": 3, "status": "published" },
  "topology_edges": [          // CourseEnvironmentEdge：防火牆式連線
    { "source_node_key": "web-server", "target_node_key": "db-server",
      "direction": "one_way|bidirectional",
      "protocol": "tcp", "port": 22 }
  ],
  "node_positions": { "web-server": { "x": 120.0, "y": 80.0 } },
  "publications": [            // 對外服務：外網 → 機器（逐生組網域）
    { "node_key": "n8n", "mode": "domain|firewall_only",
      "port": 5678, "protocol": "tcp",
      "hostname_prefix": "{student}-n8n", "enable_https": true }
  ],

  "capacity_preview": { "...": "..." },
  "capacity_reservation": {    // ClassCapacityReservation：送審時凍結
    "student_count": 30, "machine_count": 90,
    "cpu_cores": 180, "memory_mb": 184320, "disk_gb": 1800,
    "ip_count": 90, "network_count": 2,
    "placement_plan": "{...}",       // 整班釘定的叢集/節點計畫
    "student_placements": "{...}",   // {machine_node_id: {user_id: 節點名}}
    "status": "reserved"
  }
}
```

重點：

- 機器類型在 `machine_nodes[].resource_type`（`qemu`/`lxc`），系統範本
  名稱用本次新加的 `template_name` 對照
- 圖譜三件套 `topology_edges`（誰連誰、方向/協定/port）+
  `node_positions`（老師在課程編輯器排的座標，班級複本沿用）+
  `publications`（對外服務，`hostname_prefix` 含 `{student}` 樣板）
- 列表頁 `GET /teaching-classes` 走輕量 `_serialize_list`：
  只有 `machine_nodes`（含 `template_name`）與摘要計數，**沒有**圖譜；
  圖譜只在詳情呼叫
- 週次 `weeks[]`（week_number/session_date/title/target_node_key/
  files）也在詳情回應內

---

## 6. 收斂決議：刪除 template_key / command_key，改組裝「目前機器清單」前提（2026-09-18）

> 決策方向：不再讓 Teacher Judge 依賴 `template_key`（linux/python/n8n/postgresql）
> 與 `command_key`（catalog 表）的預先對應。改成每次對話／生成腳本時，**動態組裝
> 一段「目前班級實際有哪些機器」的簡單前提文字**（LXC Linux、QEMU Windows…），
> 並明確告訴 AI：這是實際存在的事實，不是能力對照表，不得依賴或假設原有對應。
> 安全底線改由 `script_policy.py` AST + quality validator + AI reviewer 承接。

### 6.1 現況盤點（本次程式碼逐檔確認）

`template_key` / `command_key` 的實際耦合點（=要拆的清單）：

| # | 位置 | 內容 | 拆除動作 |
| --- | --- | --- | --- |
| 1 | `template_command_service.py` | `SUPPORTED_TEMPLATE_KEYS`、`GENERAL_COMMAND`、`get_enabled_template_commands`、`format_template_commands_for_prompt`、`validate_check_steps*` | 整檔刪除；僅保留 `coerce_timeout_seconds`（搬到 schemas 或 script 服務） |
| 2 | `prompt.py:5-14` | `TEMPLATE_COMMAND_CONTEXT_TEMPLATE`（catalog 注入） | 改為「機器清單前提」模板（見 6.3） |
| 3 | `prompt.py:33, 78-89, 82` | catalog / `system.run_command` / `python.run_entrypoint` 規則（決策規則 7、8、SITUATION_REFINE 1.2/1.1-5） | 改寫為「依檢查目的自行選 Linux/Windows 唯讀 CLI + argv 契約」 |
| 4 | `service.py:2038-2149` | `chat_with_rubric(template_key, template_commands, environment_keys)`、`_run_proposal_tool_loop` 傳遞 | 簽名改收 `machine_context: str`；刪 catalog 查詢 |
| 5 | `service.py:400-520` | `_normalize_check_steps` 的 command_key 修復／回收邏輯、`validate_check_steps` 呼叫（:434, :487）、`_recovered_catalog_item_titles`、`_partial_failure_note` | 大幅簡化：只剩 argv/cwd/timeout 正規化 |
| 6 | `automation_support.py` | `python.run_entrypoint` / `system.run_command` 特殊 blocker、`(template_key, command_key)` 驗證 | 簡化成「argv 非空即合格」，刪 catalog 比對 |
| 7 | `script_artifact_service.py` | `SCRIPT_GENERATION_SYSTEM_PROMPT:148-157`、`AI_REVIEWER_SYSTEM_PROMPT:214-220` 的 catalog/run_command/run_entrypoint 規則；`_template_commands_snapshot`、`_with_template_command_catalog`；`create_artifact`/`regenerate_artifact` 的 `template_commands` 參數 | prompt 改 argv 契約；刪 snapshot 函式；參數移除 |
| 8 | `schemas.py:46-63` | `TeacherJudgeRubricCheckStep{template_key, command_key, command_label, parameters}` | 改為扁平結構 `{argv: list[str], cwd: str|None, timeout_seconds: int 1-300}`（見 6.4） |
| 9 | `schemas.py:15-18, 183-193, 239, 307-322, 374/377` | `SUPPORTED_TEMPLATE_KEYS` 驗證、session `environment_keys` 限制、Public 欄位、`TeacherJudgeScriptCreateRequest.template_key` | 移除驗證；DB 欄位保留但不寫新語意 |
| 10 | `routes/teacher_judge_sessions.py:548-595, 800, 825` | 每則訊息查 catalog + 傳 `template_commands` | 改查 machine_nodes 組裝前提字串 |
| 11 | `routes/teacher_judge_scripts.py:60-99` | `_normalize_supported_template_key` 驗證入口 | 移除驗證，改接受並忽略 |
| 12 | `file_service.py:215-236, 274-287` | blank 建立 `environment_keys` 必填 ⊆ SUPPORTED、fork 繼承 | blank 不再要求環境勾選；欄位留空/沿用 |
| 13 | `models/teacher_judge_file.py:66-69`、`teacher_judge_script_artifact.py:71` | DB 欄位 | **不回**不回滾**：欄位保留，新寫入固定值（legacy 相容）；`teacher_judge_template_commands` 表 + 3 個 migration 種子留著無害，只是不再被查詢 |
| 14 | `script_executor_service.py:480,496,634,669`、`script_run_service.py:264` | AI usage `preset=template_key`、run metadata | `preset` 改寫 resource_type 摘要（如 `lxc,qemu`）或沿用 artifact 欄位值 |
| 15 | 前端 `aiJudge.js:19-39`、`AiJudgePanel.jsx:1359,1518,776-782,2368,3021,1325` | `TEMPLATE_OPTIONS` 環境勾選、`getTemplateLabel`、check_steps 的 key 顯示、script 標籤 | 移除環境勾選 UI；step 顯示改 argv；標籤改 resource_type |
| 16 | locales（en/ja/zh-TW `ai_teacher_judge.json:19, 78`） | `schemas.template_key_unsupported`、`file.template_key_not_in_candidates` | 刪除或改寫 |
| 17 | Tests | `test_teacher_judge_*` 多檔、`aiJudge.test.js`、`AiJudgePanel.test.jsx` | 大批改寫 |

### 6.2 機器前提的資料來源（已存在的鏈路，不用新表）

Session 都掛在 `teaching_class_id` 之下（routes 是
`/teaching-classes/{class_id}/judge/sessions/*`），因此每次請求可直接：

```text
session.teaching_class_id
  → MachineNode（unique (class_id, node_key)）
  → resource_type: "lxc" | "qemu"（doc §5.5 已確認）
  → template_name（§5.1 新加欄位；custom 節點為 null，用 custom_image_ref）
```

組裝成一行式清單，例如：

```text
本次班級目前開立的機器：3 台 LXC Linux（n8n 伺服器、Python 伺服器、PostgreSQL 資料庫）、1 台 QEMU Windows。
```

- 沒有任何機器節點時 fallback：「尚未建立機器清單；預設以 LXC Linux 環境規劃唯讀診斷。」
- Windows 判定：`resource_type="qemu"` 且 `template_name`／建機規格快照
  `os_info`（BatchProvisionJobSpec）含 Windows 字樣；其餘 LXC 一律視為 Linux。
- 附件逐項核查、refine、生成腳本走同一個組裝函式，保證前提一致。

### 6.3 新前提模板（取代 TEMPLATE_COMMAND_CONTEXT_TEMPLATE）

```text
# 本次檢查環境（實際機器清單）
- {machine_context}
- 這是實際存在的事實描述，不是能力對照表：沒有 template_key、command_key
  或任何「環境 → 可用能力」的預設對應；不得引用、猜測或發明任何 key。
- 你熟悉 Linux 與 Windows 系統管理及常見唯讀 CLI。依檢查目的自行選擇適合的
  診斷指令（LXC Linux 機器用 Linux CLI，QEMU Windows 機器用 Windows CLI），
  不拘泥固定指令；清單未列出套件、服務或路徑等細節時，缺資訊就問老師，
  不得只因「清單沒寫」而拒絕提案或宣稱能力不足。
```

AI 提案一律收斂成單一受控 argv 執行步驟（原 `system.run_command` 的語意），
不再有第二種能力類型 → `python.run_entrypoint` 特殊分支一併消失
（它本來就是「cwd + argv + timeout」，新扁平結構直接涵蓋）。

### 6.4 新 check_step 契約

```python
class TeacherJudgeRubricCheckStep(BaseModel):
    argv: list[str]                      # 非空字串陣列
    cwd: str | None = None
    timeout_seconds: int | None = None   # 1-300，缺省由後端補 30
```

舊資料相容（不用寫 DB migration）：before-validator 讀到舊形狀
`{template_key, command_key, parameters:{argv, cwd, timeout_seconds}}` 時，
直接取 `parameters` 對映到新欄位、丟棄 key 欄位；`python.run_entrypoint`
與 `system.run_command` 視為同一型別。

### 6.5 分階段實作

- **P1 提示層解耦（低風險，先行）**：
  `routes/teacher_judge_sessions.py` 改組裝 machine_context →
  `chat_with_rubric(machine_context=...)` → `prompt.py` 換模板與規則（6.3）；
  暫時保留舊 schema 與 `validate_check_steps`（吃老資料），但不再把 catalog
  注入任何 prompt。
- **P2 契約扁平化**：schemas 改 6.4 + 舊形狀轉換；刪
  `template_command_service.py`（保留 timeout 常數）、簡化
  `automation_support.py`、`script_artifact_service.py` 兩個 system prompt
  改 argv 契約、routes/scripts 驗證移除、`file_service` blank 不再要求
  environment_keys。
- **P3 前端與周邊**：環境勾選 UI 移除（blank 建立只填名稱）、check_steps
  顯示改 argv 行、script 標籤改 resource_type、locales 清理、測試改寫。

### 6.6 保留的安全底線（與 key 無關，全部不動）

`script_policy.py` AST（argv list、timeout 必填、禁 shell=True/Popen）、
`script_quality_validator.py`、AI reviewer（改寫措辭但保留職能）、執行期
SFTP+SSH approved-only 閘門、`MAX_TIMEOUT_SECONDS=300`。
catalog 表與 migrations 不回滾，資料留著無害。

### 6.7 風險

- 語意約束從 catalog 移到 prompt 後，腳本檢查範圍的「廣度」約束靠 AI reviewer
  補位（AST 管不到 argv 換成查更廣的指令）——既有機制本就如此，非新增風險。
- 舊 analysis_json 內含 `command_key` 引用的提案資料，靠 6.4 的 before-validator
  轉換；轉換失敗的 step 丟棄並列 missing_information，不阻擋整表。
- AI usage `preset` 欄位語意改變，歷史資料為舊 key、新資料為 resource_type
  摘要，統計報表需知道分界點。

---

## 7. P1 / P2 / P3 展開規格（2026-09-18）

> 本節是 §6 決議的可執行規格。每階段獨立可交付、可上線；驗收標準見各節末。
> 共用前置：新模組 `machine_context.py`（§7.0），P1 就要建立。

### 7.0 共用：機器前提組裝模組

新檔 `backend/app/ai/teacher_judge/machine_context.py`：

```python
def build_machine_context(db: Session, teaching_class_id: uuid.UUID) -> str:
    """組裝「目前班級實際有哪些機器」的前提文字。"""
```

- 查詢：`TeachingClassMachineNode`（`models/teaching_class.py:84`，欄位
  `resource_type` lxc|qemu、`source_type`、`source_template_id`、`custom_image_ref`、
  `name`、`role`、`sort_order`），`WHERE class_id = ...` 依 `sort_order, node_key` 排序。
- 範本名稱：批次把 `source_template_id` join `VMTemplate.name`（§5.1 已有
  同型查詢 `_template_names_for_nodes` 可參考）；custom 節點用
  `custom_image_ref` 當名稱線索，取不到就用 `name`。
- OS 推斷規則：
  - `resource_type == "lxc"` → Linux
  - `resource_type == "qemu"` 且範本名稱／`custom_image_ref`／建機快照
    `os_info` 含 `windows`（casefold）→ Windows；否則 Linux
- 輸出格式（單行；`name` 太長時只取 role/name 前 20 字）：

```text
本次班級目前開立的機器：3 台 LXC Linux（n8n 伺服器、Python 伺服器、PostgreSQL 資料庫）、1 台 QEMU Windows（Windows 11 教學母機）。
```

- 邊界：沒有任何節點 →
  `本次班級尚未建立機器清單；預設以 LXC Linux 環境規劃唯讀診斷。`
  查詢例外（理論上不發生，session 恆屬班級）→ 同 fallback 並 log warning。
- 效能：每請求 2 條查詢（nodes + templates 批次）或 1 條（無範本節點）；
  不快取（機器清單會變）。

### 7.1 P1：提示層解耦（schema 與驗證機制全部不動）

範圍：只改「LLM 看到什麼」。`validate_check_steps`、`GENERAL_COMMAND`、
schema、腳本生成流程全部保留（`system.run_command` 仍是唯一提案型別），
但 catalog 內容不再注入任何 prompt。

| # | 檔案 | 變更 |
| --- | --- | --- |
| 1 | `machine_context.py`（新） | §7.0 |
| 2 | `prompt.py` | 刪 `TEMPLATE_COMMAND_CONTEXT_TEMPLATE`（:5-14），新 `MACHINE_CONTEXT_TEMPLATE`＝§6.3 三條（含「沒有 template_key/command_key 對應、不得引用或發明任何 key」）；`CHAT_SYSTEM_TEMPLATE` 的 `{template_command_context}` 插槽改名 `{machine_context}`；規則 7（:82）改為「提案步驟一律用 `system.run_command` + 單一 argv（`template_key` 可省略），不得發明其他 `command_key`」；:33 行改為「環境事實以本次機器清單為準，不是能力對照表，不得只因清單未列而拒絕提案」；SITUATION_REFINE 1.1-5/1.2（:236-241）的 catalog 字樣改為 argv 契約 |
| 3 | `service.py` | `chat_with_rubric`（:2038）與 `analyze_attachments_itemwise`（:2457）簽名加 `machine_context: str = ""`；:2090-2096 改組 `MACHINE_CONTEXT_TEMPLATE.format(machine_context=machine_context)`；`template_commands` 參數保留（P1 仍傳，供驗證） |
| 4 | `routes/teacher_judge_sessions.py` | :548-596 保留 catalog 查詢（驗證用），新增 `machine_context = build_machine_context(db, item.teaching_class_id)`，傳入 chat / itemwise |
| 5 | `routes/teacher_judge_scripts.py` | 不動（生成流程 P1 不碰） |
| 6 | `script_artifact_service.py` | 不動（rubric_snapshot 仍帶 catalog，生成 prompt 不變） |

不動清單（P1 刻意保留）：`template_command_service.py`、`automation_support.py`、
schemas、前端。P1 的 prompt 已明文「不要發明 command_key」，模型輸出仍走
`system.run_command`，驗證鏈不變。

**P1 驗收**

- 手動：對話提案正常建立（auto 項目有 argv step）；prompt 內容 grep 不到
  `template_key: ` / `command catalog` 字樣；機器清單文字正確反映班級節點。
- 測試：`test_teacher_judge_chat*` 相關 prompt 斷言改寫；
  新增 `build_machine_context` 單元測試（lxc/qemu/windows/custom/空班級 5 案）。
- `ruff check backend/app/ai/teacher_judge/` 通過。

### 7.2 P2：契約扁平化（後端全面拆 key）

#### 7.2.1 schemas.py

```python
DEFAULT_CHECK_STEP_TIMEOUT_SECONDS = 30
MAX_CHECK_STEP_TIMEOUT_SECONDS = 300

def coerce_timeout_seconds(value: Any) -> int | None:  # 自 template_command_service 搬入
    ...

class TeacherJudgeRubricCheckStep(BaseModel):
    """單一受控 argv 執行步驟（無 template_key / command_key）。"""
    argv: list[str] = Field(default_factory=list)
    cwd: str | None = None
    timeout_seconds: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_step(cls, value: Any) -> Any:
        # 舊形狀 {template_key, command_key, command_label, parameters:{argv,cwd,timeout_seconds}}
        # → 取出 parameters 攤平；key 欄位直接丟棄；非 dict 原樣交由欄位驗證失敗。
        ...

    @field_validator("argv")
    # 過濾非字串/空白元素；允許空 list（由 blockers 補 gap）。
```

- `SUPPORTED_TEMPLATE_KEYS`、`sanitize_check_step_parameters` import 移除；
  `_RETIRED_RUBRIC_GAP_MARKERS` 機制保留。
- `TeacherJudgeSessionCreateRequest`：`environment_keys` validator 刪除
  （欄位可留、值忽略）；`model_post_init` blank 分支不再要求 environment_keys。
- `TeacherJudgeScriptCreateRequest.template_key` → `str | None = None`
  （validator 刪除；寫入時後端固定填 `"linux"` legacy 值）。
- `TeacherJudgeSessionPublic.template_key`、`TeacherJudgeFilePublic` 兩欄位
  保留（讀 DB 舊值），前端顯示由 P3 移除。

#### 7.2.2 服務層

| 檔案 | 變更 |
| --- | --- |
| `template_command_service.py` | **整檔刪除**；timeout 常數與 `coerce_timeout_seconds` 已搬 schemas |
| `service.py` | `_normalize_check_steps(raw, template_commands=None, template_key=...)` → `_normalize_check_steps(raw)`：刪 :403-496 的 catalog 修復/回收分支，Pydantic before-validator 承接舊形狀；:592-609 的 `system_command_steps` / `template_commands is not None` 降級分支改為「`detectable == "auto"` 且 `check_steps` 全部 argv 空 → partial + missing argv 文案」；刪 `_allowed_command_text`、`_recovered_catalog_item_titles`、`_manual_candidates_needing_capability_review`、`validate_check_steps` import；`_proposal_candidate_rejection`（:736）文案去掉 command_key 清單，改「請提供單一非空 argv list」；`_PARAMETER_GAP_FIELD_HINTS` 縮成 argv/cwd 兩條；`_proposal_unavailable_reply`（:1053）刪 `template_commands` 參數與 invalid_references 區塊；`_partial_failure_note`（:1205）同步縮參數；`chat_with_rubric`/`_run_proposal_tool_loop`/`analyze_attachments_itemwise` 刪 `template_commands`、`environment_keys` 參數 |
| `automation_support.py` | `missing_step_information(step)` 只剩 argv 非空檢查；`get_script_generation_blockers(analysis)`（刪 commands 參數）；`_item_missing_information` 刪 `valid_commands` 比對；`TeacherJudgeTemplateCommand` import 刪除 |
| `script_artifact_service.py` | `SCRIPT_GENERATION_SYSTEM_PROMPT`：刪 :148-150（command_key 對應）、:154-157（run_command/run_entrypoint 專屬規則），改一條「check_steps[].argv/cwd/timeout_seconds 是已驗證的受控參數，不得替換或擴張檢查範圍；缺少真實路徑/命令時該 check 回 unknown」；`AI_REVIEWER_SYSTEM_PROMPT` :214-220 同步改 argv 契約措辭；刪 `_template_commands_snapshot`（:340）、`_with_template_command_catalog`（:360）、`generate_script_content` payload 的 `template_commands`/`template_key` 欄位（:815-821，改帶 `machine_context`）；`create_artifact`/`regenerate_artifact` 刪 `template_commands` 參數、`ensure_script_generation_supported(rubric_analysis)` 單參呼叫；snapshot 不再寫 `template_key`（:1626/:1723）；`_record_script_usage` `preset=template_key` 保留（DB 欄位還在） |
| `routes/teacher_judge_sessions.py` | :548-552 catalog 查詢刪除；chat/itemwise/refine blocker 呼叫改新簽名 |
| `routes/teacher_judge_scripts.py` | 刪 `_normalize_supported_template_key`（:60）與 SUPPORTED import；`template_key` 以常數 `"linux"` 傳入 create_artifact |
| `file_service.py` | `create_blank_file`（:215）：不再收/驗 environment_keys，`template_key="linux"` 固定、`environment_keys=[]`；`clone_file_asset` 沿用來源值不變 |
| `session_service.py` | `maybe_summarize` 刪 `template_commands` 參數（:1640-1647）；:992 保留 |
| `script_executor_service.py` / `script_run_service.py` | 不動（`artifact.template_key` 欄位續存續用） |
| `_types.py` | `TemplateCommandSnapshot`（:133）刪除 |

#### 7.2.3 資料相容

- 舊 `analysis_json.check_steps[{template_key, command_key, parameters}]`：
  before-validator 攤平，`python.run_entrypoint`/`system.run_command` 一律視為
  argv 步驟；轉不出 argv 的 step 保留空物件，由 blocker 列 missing。
- 舊 `rubric_snapshot_json` 內 `template_commands`/`template_key` 鍵：
  生成 payload 建構時明確 `pop("template_commands", None)`/`pop("template_key", None)`。
- DB：`teacher_judge_template_commands` 表、3 個 migration、
  `teacher_judge_files.template_key/environment_keys`、
  `teacher_judge_script_artifacts.template_key` 欄位**全部不動**（不回滾）。

**P2 驗收**

- 單元：legacy step 轉換（4 案：run_command、run_entrypoint、無 argv、非 dict）；
  blockers 在空 argv/無 steps 時產生正確 missing 文案。
- 整合：`POST /scripts` 以舊版 analysis_json（含 command_key）能建 artifact；
  全新對話提案 → 製作腳本 → 核准全鏈通。
- `ruff check` + 受影響 pytest 檔全綠；grep 確認 backend 內
  `SUPPORTED_TEMPLATE_KEYS`、`get_enabled_template_commands`、
  `TeacherJudgeTemplateCommand` 零殘留（model/migration 檔除外）。

### 7.3 P3：前端與周邊

| # | 檔案 | 變更 |
| --- | --- | --- |
| 1 | `frontend/src/services/aiJudge.js` | 刪 `TEMPLATE_OPTIONS`、`getTemplateLabel`（:19-39）；`createBlankSession` 刪 `environmentKeys` 預設（:75-87）；`RUBRIC_POLISH_PROMPT`/`RUBRIC_REASSESS_PROMPT`（:27-32）刪 `check_steps`/`parameters` 逐欄指示，改「補充檢測方式與缺少資訊，保留 auto/partial/manual 判定」 |
| 2 | `AiJudgePanel.jsx` | 刪 `getTemplateLabel` import 與 4 處使用（:17, :1325, :2368, :3021, :780-782）；step 顯示改 argv 摘要（`step.argv?.join(" ")`，逾 40 字截斷；cwd 顯示為小字）；`getStepParameterSummary`（:226-232）改讀扁平欄位 `step.argv/step.cwd/step.timeout_seconds`；刪 `environmentKeys` state（:1359, :1495, :1518——UI 已無勾選，屬殘留）；:2368/:3021 script 標籤的 template 字樣移除 |
| 3 | locales `ai_teacher_judge.json`（en/ja/zh-TW） | 刪 `schemas.template_key_unsupported`、`file.template_key_not_in_candidates`（各 :19, :78）；`services` ns 內 `linuxTemplateLabel` 若無他用一併刪 |
| 4 | 測試 | `aiJudge.test.js`（getTemplateLabel/TEMPLATE_OPTIONS 斷言刪）、`AiJudgePanel.test.jsx`（check_steps fixture 改扁平形狀 :579-704；file fixture 的 environment_keys 斷言刪）；backend 端 P2 已列 |

**P3 驗收**

- `npm run lint` + `vitest run`（frontend）全綠；手動：建立空白檢查（只填名稱）、
  對話提案 chip 顯示 argv、製作腳本標籤無 template 字樣。
- i18n key grep 零殘留。

### 7.4 全鏈資料流（P2/P3 後）

```text
老師描述 → chat_with_rubric(machine_context, rubric_context)
  → system prompt＝機器清單前提（無任何 key）＋ argv 契約
  → 提案 check_steps = [{argv, cwd, timeout_seconds}]
  → schema before-validator 相容舊形狀
  → blockers 只驗 argv
  → generate_script_content(rubric_snapshot, machine_context)
  → script_policy AST + quality + AI reviewer（不變）
  → 核准 → SFTP+SSH 執行（不變，從未看過 key）
```

### 7.5 各階段工時估算（ backend / frontend / tests ）

| 階段 | 檔數 | 重點工項 | 相對風險 |
| --- | --- | --- | --- |
| P1 | backend 4（1 新 3 改）+ 測試 | prompt 文案、context 組裝 | 低（驗證鏈不動） |
| P2 | backend 10 | schemas 扁平化 + 舊資料轉換 + 6 檔簽名清理 | 中（契約變更） |
| P3 | frontend 3 + locales 6 + 測試 | 顯示層、prompt 字串 | 低 |

---

## 8. 最終收斂決策與執行計劃（2026-09-18）

> 本節覆蓋前面 §6–§7 中仍屬暫定或互相衝突的敘述，作為後續實作與驗收的唯一基準。§1–§5 保留作為問題背景、程式碼證據與設計推導；§6–§7 保留作為工作草稿與拆工記錄。

### 8.1 一句話決策

Teacher Judge 的穩定機器身份只採用教學班級中的 `node_key`；P1/P2/P3 只是由 `sort_order` 推導的顯示標籤，不是資料鍵。Rubric 以 `target_node_key` 指定檢查目標，後端再依 `(class_id, student_id, node_key)` 解析到學生機器、VMID、資源與連線資訊。AI 不接觸 VMID、IP、SSH 或 Proxmox 細節；新的 rubric/artifact/run contract 不再依賴 `template_key` 或 `command_key`。

### 8.2 最終 contract 邊界

| 維度 | 最終決策 | 遷移原則 |
| --- | --- | --- |
| 機器身份 | `TeachingClassMachineNode.node_key` 是班級內穩定邏輯身份，`name` 是可改的顯示名稱 | 班級發佈後不得因重排、改名而改變 `node_key`；是否允許刪除或重建要在 A0 定案，預設不回收舊 key |
| P 標籤 | `P1/P2/P3` 僅由 `sort_order` 產生 | UI、prompt 顯示 P 標籤時同時帶 `node_key`；重排只改標籤，不改目標身份 |
| Rubric 目標 | 可執行的 `TeacherJudgeRubricItem` 必須有 `target_node_key` | 建立與更新時由 server 驗證 key 屬於該 `class_id`，不可接受任意字串或跨班級 key |
| 檢查命令 | 新 contract 使用扁平化 `check_steps: [{argv, cwd, timeout_seconds}]` | 新資料不寫 `template_key`、`command_key`；舊資料先經 before-validator 轉成扁平 step 再進後續驗證 |
| Template / environment | `environment_keys`、`template_key` 只作舊資料與舊 API 的相容欄位，不再是新流程的選擇權威 | 新流程從 class machine node 與 executor capability 取得環境資訊；不要因 key 名稱相同而重用 `ai_pve_templates` |
| 執行目標 | 一般 UI / AI flow 傳 `target_node_key` 與 scope，不傳 VMID | VMID 僅在 server-side resolver、run snapshot、executor progress 中存在；舊 `target_vmids` 只能作過渡或受控管理入口，仍須做 class membership 授權 |
| Artifact scope | v1 一份 artifact 對應一個 `target_node_key` | 一份 rubric 若包含多個 node key，生成時拆成多份 artifact，或明確阻擋；v1 不隱含跨機器 DAG |

### 8.3 最終資料流

```text
rubric_item.target_node_key
  -> 驗證 class.machine_nodes 與授權
  -> (class_id, student_id?, node_key)
  -> TeachingClassStudentMachine
  -> VMID -> resource / IP / SSH / executor capability
  -> 建立 server-side target snapshot
  -> SFTP + SSH 執行
  -> 以 (student_id, node_key) 聚合 progress / result
```

AI 可見的 machine context 只描述邏輯拓撲與能力，例如：

```text
P1 | node_key=web | name=Web Server | role=frontend | resource_type=qemu
P2 | node_key=db  | name=Database  | role=backend  | resource_type=qemu
```

這裡的 P1/P2 是顯示資訊，`node_key` 才是後續工具與 rubric 使用的 identity。不得把 VMID、Proxmox node name、IP、SSH key path 或密碼放入 prompt、rubric snapshot 或 AI tool schema。

### 8.4 建議的最終 payload

Rubric item：

```json
{
  "id": "web-service-check",
  "title": "Web service responds",
  "target_node_key": "web",
  "check_steps": [
    {
      "argv": ["curl", "--fail", "http://127.0.0.1:8080/health"],
      "cwd": "/workspace",
      "timeout_seconds": 20
    }
  ]
}
```

Class-wide run request：

```json
{
  "artifact_id": "artifact-123",
  "target_scope": "all_students_on_node",
  "target_node_key": "web"
}
```

Server-side snapshot 可保留實際執行所需的內部欄位，但對外結果以邏輯身份為主：

```json
{
  "targets": [
    {
      "student_id": "student-01",
      "node_key": "web",
      "display_label": "P1",
      "status": "queued"
    }
  ]
}
```

`vmid`、`ip`、SSH 連線資料若存在，必須限制在 server-side snapshot / executor context，不能回填到 LLM context，也不應成為一般教師選擇目標的 UI 主鍵。

### 8.5 v1 範圍與明確不做事項

1. **單機器 artifact。** 一份 script/artifact 只針對一個 `target_node_key`。Rubric 混用多個 node key 時，v1 預設阻擋並要求拆分；若產品決定自動拆分，必須產生可追蹤的多份 artifact，而不是在一份 script 內偷偷跨機器。
2. **同一邏輯節點 fan-out。** `all_students_on_node` 解析該班所有具有此 node key 且有可執行資源的學生機器；教師不需要也不應手工貼 VMID。單一學生執行可另帶 `student_id`，同樣由 server 解析。
3. **跨機器檢查延後。** v1 不加入即時任意 command tool、DAG、跨節點多 script orchestration。未來若需要，增加明確的 `executor_node_key`、peer topology / runtime context 與授權規則，再另立 contract。
4. **OS / shell 能力必須誠實標示。** 現有 executor 是 Linux SSH、SFTP、`python3` 與 Unix cleanup 導向；不能只因 prompt 寫了 Windows 就宣稱支援 Windows。若 A4 前沒有 Windows executor adapter，v1 只列出支援的 Linux runtime，遇到不支援的 OS 必須在建立或執行前明確阻擋並回報原因。
5. **fan-out 不得被固定五台截斷。** `MAX_SSH_CONCURRENCY=5` 可保留作併發上限，但現有 `MAX_RUN_TARGETS=5` 不能繼續作班級執行的硬上限。全班執行應排隊處理所有匹配目標；若要設總量上限，必須是可配置、可見且有分批策略的 policy。
6. **安全鏈保留並加上 node 授權。** AST policy、quality validator、AI reviewer、教師核准、timeout、SFTP/SSH 等既有閘門都保留；新增 `target_node_key` 的班級歸屬、學生機器存在、resource 狀態與 executor capability 驗證。

### 8.6 分階段執行計劃

#### A0：凍結決策與建立 fixtures

- 凍結 `node_key` 的生命週期規則：班級發佈後不可因排序或改名變更；釐清刪除、重建、複製班級時的行為。
- 凍結 artifact scope、`all_students_on_node` / single-student scope、混合 node key 的阻擋或拆分策略。
- 凍結支援的 OS、shell、interpreter 與 executor capability；未實作的 Windows 不列入可執行清單。
- 定義 legacy converter 的輸入輸出與錯誤格式，至少建立「舊 `template_key + command_key`」、「已扁平化新 step」、「缺 node key」、「無學生機器」fixtures。
- 建立 3 個 node × 3 個學生的拓撲 fixture，供後續 fan-out、重排與權限測試共用。

#### A1：Target-aware context 與 proposal contract

涉及 `backend/app/ai/teacher_judge/schemas.py`、`service.py`、prompt/context builder、`teacher_judge_sessions.py` 與相關測試。

- 在 rubric item 加入 `target_node_key`，並在 proposal tool schema、normalize、compare 與 server route validation 全鏈路傳遞。
- AI context 改列邏輯 machine nodes：`node_key`、顯示名稱、role、resource type、能力摘要；不列 VMID / IP / SSH。
- P1 可作為過渡：可以暫時隱藏 catalog 細節，但只要 schema 仍要求 `command_key`，就不能宣稱已完成「去 key contract」。
- 舊 catalog 僅供 legacy read/convert；不得讓新 proposal 透過 `template_key` / `command_key` 選擇命令。
- 對不屬於該班級的 `target_node_key`、缺少目標的可執行 item、混合 node key artifact 提供可測試的阻擋錯誤。

#### A2：Resolver、fan-out 與執行快照

涉及 `backend/app/ai/teacher_judge/script_run_service.py`、`script_executor_service.py`、`teacher_judge_sessions.py`、`teacher_judge_scripts.py`、`teacher_judge_script_run.py` 與測試。

- 實作 `(class_id, student_id?, node_key)` resolver，先取得 `TeachingClassMachineNode`，再取得對應 `TeachingClassStudentMachine`，最後解析 live resource、IP、SSH 與 executor capability。
- target snapshot 與 progress/result 至少保留 `student_id`、`node_key`、顯示 label、resource status；VMID 只放內部執行資料。
- 將 run request 從「人工 VMID 清單為主」改成「logical target + scope 為主」，保留舊入口的明確相容層與同等強度授權。
- 移除固定 `MAX_RUN_TARGETS=5` 的班級總量限制；保留併發 5，讓 30 人班級以 queue / bounded concurrency 完成，而不是只跑前 5 台。
- 對 pending、缺 VMID、resource 不存在、未 running、OS 不支援、SSH 不可用等狀況建立逐目標結果，不可把整批錯誤壓成模糊的單一失敗。

#### A3：Catalog 退場與 step flatten

涉及 `schemas.py`、Teacher Judge service、session/script routes、持久化 JSON/DB migration 與測試。

- before-validator 先將舊 `template_key + command_key + parameters` 轉成 `argv/cwd/timeout_seconds`；轉換失敗要保留可定位的 migration error，不得靜默產生錯命令。
- 新建 artifact、rubric、session 不再寫入 `template_key`、`command_key`；新 prompt、tool schema、compare fields 也不再把它們視為可變 contract。
- 清理只依賴 catalog 的 generate / validate / execute 分支，但保留一段明確的 legacy read window，直到既有資料完成轉換。
- 對 `environment_keys` 做同樣的角色切割：可讀舊資料，但不再要求教師以它選擇執行環境；新環境資訊改由 machine node / executor capability 提供。

#### A4：前端、OS 狀態與完整驗收

涉及 `frontend/src/services/aiJudge.js`、`frontend/src/pages/course-operations/class-workspace/AiJudgePanel.jsx`、locales 與前後端測試。

- 目標選擇顯示 `P1 — name (node_key)` 或等價資訊，不顯示 VMID 作為主要選擇欄位。
- run dialog 以 node / student scope 操作；只有在必要的管理或 legacy debug 場景才顯示內部 target details。
- 明確顯示 node pending、無機器、OS 不支援、混合 artifact、部分成功與排隊狀態。
- 同步更新 rubric preview、script preview、錯誤訊息與所有 locale，避免只改主頁而留下舊的 template/command key 文案。
- 完成 browser/API/worker 測試與 `git diff --check`；若沒有真正執行 Windows executor，不得把 Windows case 標成通過。

### 8.7 驗收條件

1. 同一班級有 3 個 node、每個 node 有 3 個學生機器時，針對一個 `target_node_key` 會精準產生 3 個 target；針對三個 node 的拆分 artifact 才會產生 9 個 target，且每筆結果可由 `(student_id, node_key)` 唯一定位。
2. P 標籤重排、機器顯示名稱修改不會改變 rubric 的 `target_node_key`；跨班級或不存在的 key 在 proposal、artifact 建立、run 三層都會被拒絕。
3. AI 看到的 context、tool schema、rubric snapshot 不含 VMID、IP、SSH key path 或密碼；executor 仍能從 server-side resolver 完成連線。
4. 30 人班級在併發上限 5 下會全部排隊完成，不會因舊的總量上限只執行 5 台；單一目標失敗不會覆蓋其他目標結果。
5. 舊有 `template_key + command_key` 資料可被 deterministic converter 讀取；新建資料不再產生這兩個欄位，且轉換失敗有明確錯誤。
6. 混合多 node key 的單一 artifact 在 v1 有固定且可測試的行為：阻擋並要求拆分，或產生多份可追蹤 artifact；不能默默跨節點執行。
7. AST policy、quality validator、AI review、教師核准、timeout、SSH/SFTP 與 node membership 授權全數仍在執行路徑上。
8. 不支援的 OS、缺 VM、pending resource、無法取得 SSH 或不符合 executor capability 時，系統在執行前或逐 target 回報具體原因；不能以 prompt 文字冒充支援。

### 8.8 檔案與責任對照

| 區域 | 主要檔案 | 責任 |
| --- | --- | --- |
| Domain identity | `backend/app/models/teaching_class.py` | node key、學生機器對應與拓撲資料的穩定性 |
| Rubric / proposal | `backend/app/ai/teacher_judge/schemas.py`、`service.py` | `target_node_key`、扁平 step、legacy normalize、tool schema |
| Session / script API | `backend/app/api/routes/teacher_judge_sessions.py`、`teacher_judge_scripts.py` | logical target request、建立與執行前驗證、錯誤契約 |
| Resolve / execute | `backend/app/ai/teacher_judge/script_run_service.py`、`script_executor_service.py` | node-to-machine resolver、fan-out、capability、queue、progress、內部 VMID |
| Run persistence | `backend/app/models/teacher_judge_script_run.py` 與 migration/JSON converter | scope、snapshot、結果的相容與可追溯性 |
| Frontend | `frontend/src/services/aiJudge.js`、`AiJudgePanel.jsx`、locales | node-first UI、scope、狀態、錯誤與舊 key 文案清理 |

### 8.9 對 §6–§7 草稿的收斂修正

- 原 P1「prompt 不再暴露 catalog」只能視為降低耦合的過渡，不等於完成去除 `command_key`；完成條件是 schema、tool、validator、snapshot 與新資料寫入都改用扁平 step。
- 原 P2/P3 的階段名稱保留作為工項分組，但不是 runtime contract，也不是機器身份。所有執行目標統一回到 `node_key`。
- 原「一份 artifact = 一份 script」保留，但補上「一份 artifact = 一個 target node」的邊界；多 node rubric 必須拆分或阻擋。
- 原以 `target_vmids` 為中心的執行模型改為 logical target fan-out；VMID 仍可存在於內部 snapshot，但不再是 AI 或教師的穩定選擇鍵。
- 原工時估算只能在 A0 的四個 gate（node key 生命週期、artifact scope、OS capability、legacy converter）通過後採用；否則 P2/A2 的風險與檔案數仍可能變動。

### 8.10 必須在 A0 關閉的 gate

| Gate | 預設建議 | 未關閉時的處理 |
| --- | --- | --- |
| `node_key` 是否可重建/回收 | 發佈後 immutable，刪除的 key 不回收 | 阻擋 schema migration 與重排驗收 |
| 多 node rubric | v1 先阻擋，之後再做可追蹤拆分 | 不允許進入 script generation |
| Windows executor | 尚未有 adapter 前不列入支援 | 建立/執行前標示 unsupported |
| legacy key 退場 | 先 read/convert、禁止新寫入 | 保留 converter，不刪舊資料 |
| 手工 VMID | 僅 legacy / 管理用途 | 一般教師與 AI UI 隱藏，仍做 membership check |
