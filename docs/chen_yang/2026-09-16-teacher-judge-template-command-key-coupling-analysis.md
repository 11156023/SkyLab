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
