"""Teacher Judge managed script generation contract."""

from __future__ import annotations

RESULT_SCHEMA_VERSION = "teacher_judge_result.v1"
RAW_OUTPUT_CHAR_LIMIT = 4000
# The first generated candidate is not a retry.  A candidate may be repaired
# at most four times overall, while the same normalized failure may trigger at
# most two repairs before the workflow is stopped and surfaced to the teacher.
SCRIPT_GENERATION_MAX_RETRIES = 4
SCRIPT_GENERATION_SAME_FAILURE_MAX_RETRIES = 2
# Keep the old name import-compatible for callers outside this module.  It is
# now the total candidate count: the initial candidate plus four retries. New
# orchestration code should use the explicit retry constants above.
SCRIPT_GENERATION_MAX_ATTEMPTS = SCRIPT_GENERATION_MAX_RETRIES + 1

SCRIPT_GENERATION_CONTRACT_PROMPT = f"""
# 腳本品質契約
- 你產生的是受管資料收集腳本，不是自由發揮的診斷腳本；可讀性、可移植性、證據品質與狀態語意都必須穩定。
- 腳本目標是收集同學 VM/LXC 內可客觀觀察的只讀資料；rubric 與 catalog 明確引用 `system.run_command` 或其他受控執行能力時，可在指定 cwd 以有限 timeout 執行單一命令並收集 exit code/stdout/stderr。所有結果整理成單一 JSON。
- 核心 helper 只有 2 個：`truncate_output`、`record_check`；僅在需要執行外部命令時才額外定義並使用 `command_available` 與 `run_command`。
- `truncate_output(text, limit={RAW_OUTPUT_CHAR_LIMIT})` 必須將 raw 輸出截斷到固定長度。
- `command_available(command)` 必須用 `shutil.which(command)` 檢查外部工具是否存在。
- `run_command(argv, cwd=None, timeout=秒數)` 必須包裝 `subprocess.run([...], cwd=cwd, capture_output=True, text=True, check=False, timeout=...)`，並回傳包含未遮蔽 `stdout`、`stderr`、`returncode` 的 dict。
- `record_check(...)` 必須使用唯一的 pure-return 形狀：`record_check(check_id, title, status, evidence, raw="")` 回傳單一結果 dict；呼叫端固定使用 `checks.append(record_check(...))`，不得把 `checks_list` 或 `errors` 傳入 helper 後由 helper 直接 append。
- `record_check` 的 raw 參數可接收字串或 `run_command()` 回傳的 payload dict，但 helper 必須先在函式定義內將非字串 payload 以 `json.dumps(raw, ensure_ascii=False, default=str)` 序列化，再由一次 `truncate_output(raw_text)` 控制整個 `raw` 欄位；不可回傳巢狀 dict，也不可只在呼叫端截斷。
- 產生 helper 時必須遵守以下固定骨架（可加型別註記，但不得改變參數順序、回傳與呼叫責任）：
  ```python
  def record_check(check_id, title, status, evidence, raw=""):
      raw_text = raw if isinstance(raw, str) else json.dumps(
          raw, ensure_ascii=False, default=str
      )
      return {{
          "id": check_id,
          "title": title,
          "status": status,
          "evidence": evidence,
          "raw": truncate_output(raw_text),
      }}

  checks.append(record_check(check_id, "收集 ...", "unknown", "...", raw_payload))
  ```

# 狀態語意
- `pass`：只有在必要條件被明確驗證成立時才能使用。
- `fail`：只有在必要條件被明確驗證不成立時才能使用。
- `warning`：收集大致可完成，但存在非阻斷異常、證據不完整、或結果有風險時使用。
- `unknown`：工具缺失、timeout、權限不足、解析失敗、環境差異導致無法判定時使用。
- `skipped`：此收集項目不適用目前 target/template 時使用。
- 只要 stdout / stderr 非空，不能直接判定為 `pass`。
- `command not found`、`FileNotFoundError`、`PermissionError`、`subprocess.TimeoutExpired` 不得判定為 `pass`。

# 可移植性與證據規則
- 優先使用 Python 標準函式庫；若需外部工具，先 `command_available()` 再執行。
- 若需執行外部指令，必須透過 `run_command()` 收集 stdout/stderr/returncode。
- 不假設外部工具一定存在；工具缺失時回 `unknown`。
- 判定條件採 rubric 要求的最小充分粒度：只有明確要求完全相等時才比較整份輸出；「有／包含／存在某行或設定」使用內容或逐行存在判定。設定行如 `web_URL=True` 可忽略行首尾及等號周圍空白，不得因 stdout 還有其他內容就判定失敗。
- `evidence` 應是老師可讀的判斷摘要，不是原始輸出全文。
- `raw` 應包含判斷所需的 stdout、stderr 與 returncode，不做內容遮蔽，只以 `truncate_output` 控制單一欄位大小。
- 發生例外時不能吞錯後標成 `pass`；應記錄到 `errors` 或回 `unknown` / `fail`。

# errors 記錄規則（執行期）
- 腳本頂層必須定義 `errors: list[str] = []`，並在每個收集項目的例外處理區塊中使用 `errors.append(f"{{check_id}}: {{錯誤說明}}")` 記錄錯誤。
- `errors` 的用途是讓老師看到執行時的收集品質：哪些項目遇到什麼問題，不是用來觸發腳本修正。
- 若所有收集項目皆成功，`errors` 輸出空陣列 `[]`。
- 以下情況**必須**在 except 區塊中追加 errors 條目，不可只靠 status 表示：
  1. `run_command` 拋出 `subprocess.TimeoutExpired` → `errors.append(f"{{check_id}}: 指令 {{command}} timeout 超過 {{N}} 秒")`
  2. `run_command` 拋出 `FileNotFoundError` → `errors.append(f"{{check_id}}: 工具 {{command}} 不存在")`
  3. `run_command` 拋出 `PermissionError` → `errors.append(f"{{check_id}}: 權限不足無法執行 {{command}}")`
  4. HTTP 請求 timeout / 連線失敗 / 非 2xx 回應 → `errors.append(f"{{check_id}}: HTTP {{status}} {{reason}}")`
  5. 解析 stdout/stderr 失敗 (JSONDecodeError / ValueError) → `errors.append(f"{{check_id}}: 解析輸出失敗")`
  6. 任何未預期的 Exception → `errors.append(f"{{check_id}}: 未預期錯誤: {{str(exc)[:200]}}")`
- 記錄到 errors 的同時，對應 check 的 status 不可為 `pass`；應為 `fail` 或 `unknown`。
- errors 中的 check_id 必須對應到該收集項目的 `record_check` 所使用的 id。
- 錯誤訊息必須使用繁體中文，足夠讓老師理解問題原因。

# 結果契約
- 最後輸出單一 JSON，`schema_version` 固定為 `{RESULT_SCHEMA_VERSION}`。
- 最後必須使用 `json.dumps(..., ensure_ascii=False)`，避免繁體中文被 escape。
- 頂層 `metadata` 必須包含 `timestamp` 與 `platform`。
- 每個 check 需包含 `id`, `title`, `status`, `evidence`, `raw`。
- `id` 必須是語意化穩定 ID，例如 `runtime.python_version`、`service.n8n_port`，不可使用 `check-1`、`item-1`、`stable_check_id`。
- coverage 的 check_id 必須與實際 record_check ID 完全一致；可直接傳字串，或使用在該次呼叫前明確指定的字串常數。不要使用動態或分支不明的 ID。
- `title` 使用「收集」語意，例如「收集 Python 版本」、「收集 n8n 連接埠」，不要用「檢查」開頭。
- 允許狀態只有 `pass`, `fail`, `warning`, `unknown`, `skipped`。
""".strip()
