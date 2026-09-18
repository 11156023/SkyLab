# Teacher Judge 腳本執行結果收斂與移除 runtime result analysis layer 實作計畫

- 日期：2026-09-17
- 範圍：`backend/app/ai/teacher_judge/`（script executor / result contract / generation gates / monitoring）、`backend/app/services/course/ai_assignment_service.py`、`frontend/src/pages/course-operations/class-workspace/AiJudgePanel.jsx`
- 狀態：已完成實作，focused 驗證通過

---

## 1. 目標與邊界

本次只處理「腳本已在學生 VM/LXC 執行完畢後，backend 如何接收、保存與顯示結果」：

- **刪除整個 runtime result analysis layer**：不再將 `parsed_result`、rubric 與 target 資訊送給 VLLM。
- **直接使用腳本回傳的 `parsed_result.checks[]`** 作為檢查點結果。
- `checks[].title`、`checks[].status`、`checks[].evidence`、`checks[].raw` 是執行結果的唯一事實來源。
- backend 只做 JSON schema 驗證、執行狀態整理、保存與權限範圍內的安全化投影，不重新解釋證據。

本次**保留**：

- rubric 分析、腳本生成、腳本修復與 AI reviewer；這些都發生在「核准腳本以前」。
- `check_script_policy`、`check_script_quality`、coverage 閘門、approved-before-run。
- class/owner/VM/LXC/running/IP/SSH key 驗證、最多五台 target、SSH timeout、輸出大小限制。
- `teacher_judge_result.v1` 腳本輸出契約。

本次**移除**：

- `script_result_analysis_service.py` 整個 runtime service。
- `_call_ai_judgement()`、AI judgement prompt、runtime AI semaphore、runtime AI schema validation。
- 每個 target 的 `ai_judgement`、`model`、`metrics`、`analyzed_at` 與 `ai_*` summary 欄位。
- `tj_result_ai` usage 記錄。

---

## 2. 現況資料流與要移除的節點

目前實際流程如下：

```text
POST /teaching-classes/{class_id}/judge/scripts/{script_id}/runs
  → create_script_run()
  → submit(execute_script_run(run_id))

背景執行：
  → _execute_target_script()
  → _target_result()
  → validate_managed_script_output()
  → target.status / reason_code / validation / parsed_result
  → analyze_target_results()                 ← 本次刪除
  → _call_ai_judgement()                     ← 本次刪除
  → _save_analyzed_results()
  → teacher_judge_script_runs.target_results_json
```

實際位置：

| 功能 | 目前位置 | 修正後責任 |
| --- | --- | --- |
| 建立 run | `teacher_judge_scripts.py:180` → `script_run_service.py:224` | 保持不變 |
| SSH 執行 | `script_executor_service.py:234` | 保持不變 |
| result JSON 驗證與 target 狀態 | `script_executor_service.py:329` | 保持不變 |
| runtime AI 判讀 | `script_result_analysis_service.py` | 整檔刪除 |
| 結果保存 | `script_executor_service.py:640` | 改名為 `_save_results()`，直接保存執行結果 |
| 教師顯示 | `AiJudgePanel.jsx:2608` 起 | 改顯示 `parsed_result.checks[]` |
| 學生安全化結果 | `ai_assignment_service.py:88` | 從 `parsed_result` 建立安全投影，不讀新 `ai_judgement` |

`_target_result()` 已經將以下兩種狀態分開，修正後必須維持：

- target `status=completed`：腳本 exit code 為 0 且 JSON 合約有效。
- `parsed_result.checks[].status=pass/fail/warning/unknown/skipped`：單一檢查點的結果。

target 執行完成不代表所有檢查點通過；檢查點失敗也不應把整個 run lifecycle 任意改成 executor failure。

---

## 3. 修正後的結果契約

### 3.1 腳本結果：唯一檢查事實來源

腳本仍輸出 `teacher_judge_result.v1`：

```json
{
  "schema_version": "teacher_judge_result.v1",
  "metadata": {
    "timestamp": "...",
    "platform": "linux"
  },
  "summary": "...",
  "checks": [
    {
      "id": "service.n8n_port",
      "title": "收集 n8n 連接埠",
      "status": "pass",
      "evidence": "n8n 正在監聽 5678",
      "raw": "{\"stdout\":\"...\",\"stderr\":\"\",\"returncode\":0}"
    },
    {
      "id": "history.file",
      "title": "收集 history 記錄",
      "status": "warning",
      "evidence": "檔案存在但內容不完整",
      "raw": "抓回的文字檔內容或 stdout/stderr/returncode"
    }
  ],
  "errors": []
}
```

`ManagedScriptCheck` 保持現有欄位與限制：

- `title`：檢查點名稱。
- `status`：只允許 `pass | fail | warning | unknown | skipped`。
- `evidence`：老師可讀摘要。
- `raw`：原始證據字串，最多 4000 字元；可包含 stdout/stderr/returncode 或檔案內容。

本次**不新增 `evidence_kind`**。檢查點是 command、檔案或 history，不影響結果契約；`title`、`evidence`、`raw` 已足夠顯示資料。

### 3.2 run 保存形狀

新 run 的 `target_results_json` 建議使用新版本標記，移除第二份 AI JSON：

```json
{
  "schema_version": "teacher_judge_run_results.v2",
  "targets": [
    {
      "vmid": 101,
      "status": "completed",
      "reason_code": "success",
      "exit_code": 0,
      "validation": {
        "valid": true,
        "schema_version": "teacher_judge_result.v1",
        "checks_count": 2
      },
      "parsed_result": {
        "schema_version": "teacher_judge_result.v1",
        "metadata": {},
        "summary": "...",
        "checks": [],
        "errors": []
      }
    }
  ]
}
```

`result_summary_json` 只保留執行層統計：

```json
{
  "total": 3,
  "completed": 2,
  "failed": 1,
  "valid_json": 2,
  "invalid_json": 1
}
```

不再保存：

- `ai_judgement`
- `ai_completed` / `ai_failed` / `ai_skipped`
- `model`、`metrics`、`analyzed_at`
- runtime AI 產生的自然語言 summary 或 item judgement

舊有 `teacher_judge_run_results.v1` 與 `ai_judgement` 資料不做 destructive migration；讀取端保留一次性的歷史相容分支，新 run 不再寫入舊欄位。

---

## 4. 使用者介面目標

教師執行結果直接依 `parsed_result.checks[]` 顯示：

```text
檢查點 1（通過）
  ├─ checks[].title  → 收集 n8n 連接埠
  └─ checks[].status → pass

檢查點 2（需注意）
  ├─ checks[].title  → 收集 history 記錄
  ├─ checks[].status → warning
  └─ checks[].raw    → stdout/stderr/returncode 或檔案內容（最多 4000 字元）
```

前端固定映射：

| script status | 顯示 |
| --- | --- |
| `pass` | 通過 |
| `fail` | 未通過 |
| `warning` | 需注意 |
| `unknown` | 無法判定／待導師核查 |
| `skipped` | 略過 |

target 執行狀態另外顯示：

- `completed`：腳本執行完成且 JSON 有效。
- `failed`：依 `reason_code` 顯示 Python 缺失、非零 exit code、JSON 錯誤、SSH 或 target 驗證錯誤。

移除目前 `AiJudgementBadge` 的「分析中」、「AI 核對失敗」分支；run 尚未終態時仍保留輪詢與「等待回收」，但不再等待 AI 分析階段。

---

## 5. 實作步驟

### 5.1 刪除 runtime analysis layer

1. 刪除 `backend/app/ai/teacher_judge/script_result_analysis_service.py`。
2. 從 `script_executor_service.py` 移除：
   - `analyze_target_results` import。
   - `pending_judgement` import。
   - `_with_pending_ai_judgement()`。
   - `_ai_summary()`。
   - `_record_result_ai_usage()`。
   - `CALL_TJ_RESULT_ANALYSIS` import。
3. 將 `_save_analyzed_results()` 改名為 `_save_results()`，只寫入 `_summary(results)` 與 target 結果。
4. `_execute_script_run()` 在 worker 收集完成後直接呼叫 `_save_results()`，不再 `await analyze_target_results()`。
5. `_execute_targets()` 不再回傳 `rubric_snapshot` 或 `script_metadata`；保留必要的 target results 與 worker-owned Session。
6. `progress_json.stage` 將 `analyzing` 改為 `finalizing` 或直接進入 `completed`。

執行與保存仍維持現有 cancellation/worker drain 邊界；刪除 AI 不得讓 SSH、Session 或晚到的 failure overwrite 已完成 run。

### 5.2 保持生成期安全閘門

- `script_policy.py` 不新增 `evidence_kind`。
- `script_generation_contract.py` 保持現有 `record_check()`、status、raw 截斷與 errors 規則。
- `script_quality_validator.py` 不改變既有 `record_check` contract。
- `script_coverage_validator.py` 與 AI reviewer 仍是 artifact 核准前的閘門；coverage 不再於 runtime 用來產生第二份判讀 JSON。
- 不因移除 runtime AI 而放寬 argv、cwd、timeout、no-shell、readonly 或 approved-before-run。

### 5.3 教師前端

修改 `AiJudgePanel.jsx`：

1. 移除 `result.ai_judgement` 讀取。
2. `result.parsed_result.checks[]` 直接渲染 title/status/evidence/raw。
3. `validation.valid=false` 或 target `status=failed` 時，顯示固定 reason label。
4. table 欄位由「系統核對／導師核查」收斂為「腳本執行結果」。
5. raw 使用既有 `<pre>`，不另建第二份截斷資料。
6. 輪詢只等待 run lifecycle 終態，不等待 AI judgement。

### 5.4 學生端安全化投影

`ai_assignment_service.py` 仍不可直接把教師用的 `raw_result_json`、stdout/stderr 或 credentials 回傳給學生。

- 新 run 從 `parsed_result.checks[]` 建立安全的 item projection，只保留必要的 `item_id`、title、status、evidence/comment。
- 若學生 checkpoint 必須對應 rubric item，使用 artifact 已保存的 coverage 做 deterministic mapping；這是讀取時的 API projection，不是 runtime AI analysis。
- 不產生 score；既有 `score`/`max_score` 欄位只保留舊資料讀取相容，新的腳本結果填 `null`。
- 更新學生端 status mapping，使 `pass/fail/warning/unknown/skipped` 不再依賴舊的 `passed/partial/needs_review` AI 語意。

### 5.5 舊資料與 API 相容

- `TeacherJudgeScriptRunPublic` 目前使用 generic JSON，不需要 Alembic migration。
- 新寫入使用 `teacher_judge_run_results.v2`。
- 舊 v1 run 仍可讀；教師端可在邊界處讀取舊 `ai_judgement`，但不把它轉寫回新結果。
- 不修改或重寫歷史 run，避免破壞既有執行紀錄。

---

## 6. 測試調整

### 移除或改寫

- `backend/tests/ai/teacher_judge/test_script_runs.py`
  - 移除 `analyze_target_results` mock 與 `ai_*` summary assertions。
  - 驗證 valid/invalid JSON 直接保存。
  - 驗證 target status、reason_code、parsed_result 與 checks 不變。
- `backend/tests/test_ai_p1_regressions.py`
  - 移除 `_call_ai_judgement`、AI judgement schema、semaphore 與 runtime AI usage 測試。
  - 保留 executor worker/cancellation/Session isolation 測試。
- `backend/tests/services/course/test_ai_assignment_service.py`
  - 新增 parsed-result projection 測試。
  - 保留舊 `ai_judgement` fixture 的讀取相容測試。

### 新增核心案例

1. checks 有 `pass`、`warning`、`fail`、`unknown`、`skipped` 時，原樣保存與顯示。
2. valid JSON 但 exit code 非零時，target failure 與 parsed checks 同時保留。
3. invalid JSON 時不產生 AI 或 system judgement；只顯示 validation/reason code。
4. `VLLM_MODEL_NAME` 未設定時，已核准腳本仍可完成 runtime execution。
5. 新結果沒有 `ai_judgement`、`ai_*` summary 或 model metrics。
6. 舊 v1 run 仍能讀取並顯示。
7. 學生端不回傳 raw/stdout/stderr/credentials。

---

## 7. 驗收條件

- 執行後沒有任何 `/chat/completions` runtime result request。
- `target_results_json.targets[*].parsed_result.checks[]` 是教師檢查點顯示的唯一來源。
- 每個檢查點都能顯示 title、status、evidence，並在有 raw 時顯示最多 4000 字元的 raw。
- target execution status 與 check status 不互相覆蓋。
- runtime 失敗、JSON invalid、SSH/VM 驗證失敗都有固定 reason code。
- 腳本生成期的安全、品質、coverage、reviewer 與 approved-before-run 行為不變。
- 舊執行紀錄可讀，新執行紀錄不再產生 `ai_judgement`。

---

## 8. 最小驗證指令

在 `backend/` 執行：

```powershell
uv run python -m pytest tests/ai/teacher_judge/test_script_runs.py tests/ai/teacher_judge/test_script_policy.py tests/services/course/test_ai_assignment_service.py -q
uv run ruff check app tests
uv run mypy app
```

再執行 frontend focused test/build，確認 `AiJudgePanel` 不再讀取 `ai_judgement`。SSH 實機、Proxmox、瀏覽器輪詢與學生端完整 E2E 仍需在可用測試 stack 驗證。
