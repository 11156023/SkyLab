"""Teacher Judge managed script generation contract."""

from __future__ import annotations

RESULT_SCHEMA_VERSION = "teacher_judge_result.v1"
RAW_OUTPUT_CHAR_LIMIT = 400
SCRIPT_GENERATION_MAX_ATTEMPTS = 3

SCRIPT_GENERATION_CONTRACT_PROMPT = f"""
# 腳本品質契約
- 你產生的是受管資料收集腳本，不是自由發揮的診斷腳本；可讀性、可移植性、證據品質與狀態語意都必須穩定。
- 腳本目標是收集同學 VM/LXC 內的服務、port、process、localhost HTTP 等只讀資料，整理成單一 JSON。
- 腳本必須定義並使用這些 helper：`truncate_output`、`redact_sensitive_text`、`command_available`、`run_command`、`record_check`。
- `truncate_output(text, limit={RAW_OUTPUT_CHAR_LIMIT})` 必須將 raw 輸出截斷到固定長度。
- `redact_sensitive_text(text)` 必須遮罩 token、password、secret、api key、private key、bearer 等敏感字樣；不得使用裸 `key` 這種過度寬泛規則。建議使用下列結構：
  ```python
  def redact_sensitive_text(text: str) -> str:
      patterns = [
          (
              r"(?i)(password|passwd|secret|token|api_key|bearer|auth_token|access_token|private_key|ssh-rsa|id_rsa)\\s*[:=]\\s*[^\\s]+",
              r"\\1: [REDACTED]",
          ),
          (r"([a-f0-9]{32,})", "[REDACTED_HASH]"),
          (r"(\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3})", r"\\1"),
      ]
      redacted = text
      for pattern, replacement in patterns:
          redacted = re.sub(pattern, replacement, redacted)
      return redacted
  ```
- `command_available(command)` 必須用 `shutil.which(command)` 檢查外部工具是否存在。
- `run_command(argv, timeout=秒數)` 必須包裝 `subprocess.run([...], capture_output=True, text=True, check=False, timeout=...)`，並回傳包含 `stdout`、`stderr`、`returncode` 的 dict。
- `record_check(...)` 必須統一建立檢查結果，所有 `raw` 都必須先經過 `redact_sensitive_text` 與 `truncate_output`。

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
- 不要假設一定有 `systemctl`、`ss`、`curl`、`grep`；工具缺失時回 `unknown`。
- `evidence` 應是老師可讀的判斷摘要，不是原始輸出全文。
- `raw` 只能是必要片段，且必須經過脫敏與截斷；禁止直接保存完整 stdout/stderr。
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
- `title` 使用「收集」語意，例如「收集 Python 版本」、「收集 n8n 連接埠」，不要用「檢查」開頭。
- 允許狀態只有 `pass`, `fail`, `warning`, `unknown`, `skipped`。
""".strip()
