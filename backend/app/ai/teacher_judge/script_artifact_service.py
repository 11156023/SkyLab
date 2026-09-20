"""Teacher Judge managed script artifact lifecycle service."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from inspect import signature
from typing import Any, Literal, cast

from fastapi import HTTPException
from sqlmodel import Session, col, desc, func, select

from app.ai.monitoring import (
    CALL_TJ_SCRIPT_GENERATION,
    CALL_TJ_SCRIPT_REVIEW,
    record_ai_template_call,
)
from app.ai.teacher_judge._types import (
    AIReviewResult,
    CheckResult,
    CoverageMapping,
    CoverageResult,
    FixHint,
    GateResult,
    PreviousReviewFeedback,
    TemplateCommandSnapshot,
)
from app.ai.teacher_judge.automation_support import ensure_script_generation_supported
from app.ai.teacher_judge.config import settings
from app.ai.teacher_judge.deterministic_compiler import (
    CheckPlanContractError,
    canonicalize_check_plan,
    compile_check_plan,
)
from app.ai.teacher_judge.file_service import source_file_snapshot
from app.ai.teacher_judge.machine_context import (
    load_class_machine_nodes,
    peer_node_keys_from_snapshot,
    resolve_class_machine_node,
    rubric_item_machine_issues,
    target_node_keys_from_snapshot,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeScriptArtifactPublic,
    TeacherJudgeScriptSetPublic,
)
from app.ai.teacher_judge.script_coverage_validator import (
    parse_coverage_payload,
    realign_coverage_to_script,
    validate_coverage,
)
from app.ai.teacher_judge.script_generation_contract import (
    RESULT_SCHEMA_VERSION,
    SCRIPT_GENERATION_CONTRACT_PROMPT,
    SCRIPT_GENERATION_MAX_RETRIES,
    SCRIPT_GENERATION_SAME_FAILURE_MAX_RETRIES,
)
from app.ai.teacher_judge.script_policy import (
    check_peer_runtime_policy,
    check_script_policy,
)
from app.ai.teacher_judge.script_quality_validator import check_script_quality
from app.ai.teacher_judge.service import _call_vllm
from app.ai.teacher_judge.template_command_service import get_enabled_template_commands
from app.ai.utils import apply_thinking_control
from app.core.i18n import t
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptLanguage,
    TeacherJudgeScriptSource,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand

logger = logging.getLogger(__name__)

ScriptUsageRecord = dict[str, Any]


def _ensure_peer_runtime_supported(snapshot: dict[str, Any]) -> None:
    peer_node_keys = peer_node_keys_from_snapshot(snapshot)
    if peer_node_keys:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "teacher_judge_peer_runtime_not_ready",
                "message": "跨機器觀察尚未完成受控 runtime context，暫時不能製作腳本。",
                "peer_node_keys": sorted(peer_node_keys),
            },
        )


def _script_result(value: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(value, tuple) and len(value) == 2:
        return str(value[0]), cast("dict[str, Any]", value[1] or {})
    return str(value), {}


def _generation_result(
    value: Any,
) -> tuple[str, list[CoverageMapping] | None, dict[str, Any]]:
    """Normalize generate_script_content output to (content, coverage, metrics)."""
    if isinstance(value, tuple) and len(value) == 3:
        coverage = value[1]
        return (
            str(value[0]),
            cast("list[CoverageMapping] | None", coverage),
            cast("dict[str, Any]", value[2] or {}),
        )
    if isinstance(value, tuple) and len(value) == 2:
        return str(value[0]), None, cast("dict[str, Any]", value[1] or {})
    return str(value), None, {}


def _review_result(value: Any) -> tuple[AIReviewResult, dict[str, Any]]:
    if isinstance(value, tuple) and len(value) == 2:
        return cast("AIReviewResult", value[0]), cast("dict[str, Any]", value[1] or {})
    return cast("AIReviewResult", value), {}


def _build_result(
    value: Any,
) -> tuple[
    str,
    GateResult,
    AIReviewResult,
    TeacherJudgeScriptStatus,
    list[ScriptUsageRecord],
]:
    if isinstance(value, tuple) and len(value) == 5:
        return (
            str(value[0]),
            cast("GateResult", value[1]),
            cast("AIReviewResult", value[2]),
            cast("TeacherJudgeScriptStatus", value[3]),
            cast("list[ScriptUsageRecord]", value[4]),
        )
    if isinstance(value, tuple) and len(value) == 4:
        return (
            str(value[0]),
            cast("GateResult", value[1]),
            cast("AIReviewResult", value[2]),
            cast("TeacherJudgeScriptStatus", value[3]),
            [],
        )
    raise TypeError("Unexpected build_reviewed_script result.")


SCRIPT_GENERATION_SYSTEM_PROMPT = f"""
# 角色
你是 Teacher Judge 的受管 Python 資料收集腳本產生器。

# 任務
根據 rubric snapshot 產生一份安全、受管、可重複執行的 Python managed data collection script。
腳本負責收集同學 VM/LXC 內可客觀觀察的資料；若 checklist 明確引用 catalog command，可依下列限制執行唯讀／診斷命令。最後整理成 JSON，供後續核對與證據摘要使用。

# 硬性規則
- 只能輸出 JSON，不要 markdown。
- JSON 欄位必須是 {{"script_content": "...", "coverage": [{{"check_id": "...", "rubric_item_ids": ["..."]}}]}}。
- script_content 必須是完整 Python 程式。
- 腳本可收集本機檔案內容、目錄、command log、服務、port、process、localhost HTTP 與受控命令執行結果。
- 腳本不得刪除、修改、修復、安裝、重啟、停用或重設任何環境。
- 指令輸出的 stdout/stderr 必須原樣帶回，不做遮蔽；仍不得把資料送到外部網路。
- 若需要執行指令，只能使用 subprocess.run([...], timeout=秒數, capture_output=True, text=True, check=False)。
- subprocess.run 第一個參數必須是 argv list，不得使用字串指令，不得使用 shell=True。
- 不得使用 os.system、os.popen、subprocess.Popen 或任何未設定 timeout 的指令執行方式。
- 不得以 bash、sh、zsh、cmd、PowerShell 等 shell launcher 間接執行指令。
- 若 template command 含 pipe、redirect、grep 等 shell 寫法，請改用 Python 程式解析 stdout，不要原樣 shell=True 執行。
- HTTP request 只允許 GET/HEAD localhost/127.0.0.1/::1，必須設定 timeout。
- 腳本最後必須 print 單一 JSON，schema_version 固定為 {RESULT_SCHEMA_VERSION}，並使用 json.dumps(..., ensure_ascii=False)。
- 輸出 JSON 的 metadata 必須包含 timestamp 與 platform。
- 優先根據 rubric item 的 check_steps.command_key 對應 template_commands 產生收集項目。
- `python.run_entrypoint` 是執行觀察能力，不是原始碼審查：只使用 check_steps.parameters 中已驗證的 cwd、argv、timeout_seconds，不得從自然語言猜測或補值。
- `system.run_command` 使用 check_steps.parameters 中已驗證的 argv、cwd、timeout_seconds，不得自行替換或擴張檢查範圍。
- 你熟悉 Linux、Windows 系統管理與常見 CLI 工具。外部指令只用於取得 rubric 所需的唯讀診斷資訊；不得修改系統狀態、執行高風險或破壞性操作，也不得要求提權。只收集足以回答問題的資訊，並在 evidence 解讀結果，不要只複製 raw 輸出。
- `judgement_mode=ai` 時，依 rubric item 的 title 與 detection_method 實作最小充分的判定。只有明確要求完全相等時才比較整份 stdout；「有／包含／存在某行或設定」應檢查內容或逐行存在，不得要求整份輸出只有該字串。設定行如 `web_URL=True` 可忽略行首尾及等號周圍空白，但 key 與值仍須相符。
- `judgement_mode=teacher` 時，腳本只負責完整收集指定答案／檔案／系統資訊；不得發明客觀答案或代替導師判定內容正確性。成功收集證據的 check 使用 `unknown` 並清楚標示「待導師核查」，evidence/raw 帶回可讀證據；執行或收集失敗仍依事實使用 fail/unknown 並記錄 errors。
- `system.run_command` 只允許單一唯讀／診斷 argv；禁止 pipe、redirect、寫入型 Git 子命令及其他會改變環境的操作。
- 若 rubric item 宣告 `peer_node_key` 且 check step 的 argv 含完整元素 `{{peer.ip}}`，只能用固定相對路徑 `runtime_context.json` 讀取該 item 宣告的 literal peer node key，再取 `peers[peer_node_key].ip_address`；不可列舉 peers、讀取其他 node 或把 context 當成 inventory。
- peer context 的 `resolution_status` 不是 `ready` 或 `ip_address` 為空時，該 peer check 必須記錄 `peer_unavailable` 並使用 `unknown`，同一份腳本的其他本機檢查仍要繼續；不得把空值傳給命令，也不得把 peer IP 寫死在 source。
- v1 peer probe 只允許把上述 context 得到的 IP 作為 `ping` 的 argv element；不得把它放入 shell、URL、CIDR、檔案路徑或其他命令。
- 執行 Python 入口時，必須使用 argv list、明確 `cwd`、有限 timeout，並把 exit code、stdout、stderr、未捕捉例外與 timeout 寫成該 check 的證據。
- 若 rubric 缺少工作目錄、命令或「正常結束／常駐服務」判準，不得搜尋檔案系統或猜路徑；該 check 必須回傳 `unknown`，清楚寫出缺少的資訊。
- 不得把 Python 執行檢查替換成 n8n、Port 或程序存在檢查；這些只能在 rubric 本來就要求時使用。
- 若 previous_review_feedback 有內容，代表上一輪腳本審查未通過；必須修正其中所有 policy、quality validator、coverage 覆蓋與 AI reviewer 問題。
- 若 previous_review_feedback 含 `repair_guidance`，必須先依其中的 `target`、`line_range` 與 `required_pattern` 修正指定責任邊界；`record_check` 的 raw 截斷永遠由 helper 定義負責，不能改成逐一修正呼叫端。
- `record_check` 必須使用唯一的 pure-return 形狀：`record_check(check_id, title, status, evidence, raw="")` 回傳單一結果 dict；呼叫端固定使用 `checks.append(record_check(...))`，不得把 `checks_list` 或 `errors` 傳入 helper 後由 helper 直接 append。
- `record_check` 的 raw 參數可接收字串或 `run_command()` 回傳的 payload dict，但 helper 必須先在函式定義內以 `json.dumps(raw, ensure_ascii=False, default=str)` 將非字串 payload 序列化，再由一次 `truncate_output(raw_text)` 控制整個 raw 欄位；不得回傳巢狀 dict，也不得只在呼叫端截斷。
- 若 previous_review_feedback 含 available_check_ids，這是後端已從腳本靜態解析出的可用 check ID；coverage 只能使用其中的值，不能自行創造或改寫 ID。
- 腳本頂層必須定義 `errors: list[str] = []`。每個收集項目的例外處理區塊（try/except）必須使用 `errors.append(f"{{check_id}}: {{錯誤說明}}")` 記錄錯誤原因，讓老師看到執行時的收集品質。所有收集成功時 errors 輸出空陣列。

# rubric 覆蓋映射（coverage）
- coverage 列出 script_content 中每個 record_check 與其支持的 rubric item id 對應；沒有對應 rubric item 的輔助收集可不列入。
- 一個 check 可支持多個 rubric item；一個 rubric item 也可由多個 check 支持。
- check_id 必須與 script_content 中 record_check 使用的 id 完全一致；可直接傳字串，或使用在該次呼叫前明確指定的字串常數變數。
- 不要使用動態、分支不明、重新指定或由外部輸入產生的 check_id；coverage 只會接受後端能靜態證明的值。
- rubric_item_ids 必須是 rubric snapshot 中真實存在的 item id。
- 每個 rubric item 都必須至少被一個 check 覆蓋；若某項目真的無法取證，仍不得虛構映射，讓驗證明確回報缺口。

# 簡潔程式碼骨架
- 產生單檔 Python script；不要建立 class、plugin 架構、retry framework 或多層抽象。
- 核心 helper 只有 2 個：`truncate_output`、`record_check`；僅在需要執行外部命令時才額外定義並使用 `command_available` 與 `run_command`，標準函式庫即可完成的檢查不需要外部命令 helper。
- `record_check` 必須照品質契約的固定 skeleton 使用單一 `raw` 參數並回傳 dict；呼叫端直接傳入原始 raw payload，禁止使用 `checks_list` side effect、分離的 `raw_stdout`／`raw_stderr` 參數、巢狀 raw dict，或只在呼叫端逐欄截斷。
- `run_command()` 只負責接受 argv list、cwd 與 timeout，並回傳未遮蔽的 `stdout`、`stderr`、`returncode`；若捕捉例外，回傳 `returncode=None` 與錯誤文字，不要在 helper 內操作 `errors` 或 `checks`，由呼叫端依 `returncode is None` 記錄錯誤。
- 每個收集項目使用同一個簡潔模式：
  1. 先決定 `check_id`
  2. 需要外部命令時，先檢查工具是否存在；缺工具時 `record_check(..., "unknown", ...)`
  3. 需要外部命令時執行 `run_command()`；標準函式庫可直接完成的檢查不要執行命令
  4. 若 `returncode is None`，必須 `errors.append(f"{{check_id}}: {{錯誤說明}}")` 並輸出 `unknown`
  5. `judgement_mode=ai` 只有明確驗證條件成立時才輸出 `pass`；`judgement_mode=teacher` 成功取證時輸出 `unknown` 並保留證據供導師審核
- 避免 broad `try/except` 包住大段主流程；若收集項目使用 `except Exception as exc`，該 except 區塊必須同時 `errors.append(...)`，且對應 check 不可為 `pass`。

# managed script 輸出 JSON contract
{{
  "schema_version": "{RESULT_SCHEMA_VERSION}",
  "metadata": {{
    "timestamp": "ISO-8601 timestamp",
    "platform": "platform.platform()"
  }},
  "summary": "收集摘要",
  "checks": [
    {{
      "id": "service.semantic_collection_id",
      "title": "收集名稱",
      "status": "pass | fail | warning | collected | unknown | skipped",
      "evidence": "可讀證據",
      "raw": "必要時放原始片段"
    }}
  ],
  "errors": [
    "{{ 若無錯誤則為空陣列；若有例外發生，格式為 \"check_id: 錯誤說明\" }}"
  ]
}}

{SCRIPT_GENERATION_CONTRACT_PROMPT}
""".strip()


AI_REVIEWER_SYSTEM_PROMPT = """
Canonical migration boundary: new rubric steps are flat argv/cwd/timeout data;
legacy template_key/command_key/parameters may only be read from old snapshots.
The logical target is target_node_key. Never require or expose VMID, IP, SSH,
or Proxmox details to the model, and do not treat a Windows prompt claim as
executor support while the runtime remains Linux SSH/SFTP.

你是 Teacher Judge managed data collection script 的安全審查員。
只審查腳本，不執行腳本。請依 policy 判斷它是否只做 read-only inspection，或只執行 rubric 與 catalog 明確授權的受控程式入口。

## 安全審查
若腳本可能刪除、修改、修復、安裝、重啟或對外傳資料，approved 必須是 false。讀取檔案與原樣回傳受控命令的 stdout/stderr 本身不是拒絕理由。
若腳本使用 `python.run_entrypoint`，確認它只採用 rubric check_steps.parameters 的 cwd、argv、timeout_seconds，且程式只收集 exit code/stdout/stderr、沒有安裝或修復動作；risk_level 至少為 medium。`judgement_mode=teacher` 不得因沒有客觀答案而拒絕，但必須確認腳本能執行並帶回證據。只有靜態政策與本 AI reviewer 都核准時，腳本才會進入可執行狀態。
若腳本使用 `system.run_command`，確認它只採用 check_steps 中已驗證的 argv、cwd、有限 timeout，無 shell/pipe/redirect、提權或範圍擴張，且只做唯讀／診斷操作；stdout/stderr 不需遮蔽。
若 rubric 有 peer item，確認腳本從固定 `runtime_context.json` 讀取同一 item 宣告的 logical peer，處理 `resolution_status=unavailable` 後只將 IP 傳給 `ping` argv；不得接受任意輸入 IP 或列舉其他 peers。
若 rubric 只要求內容、行或設定存在，腳本不得擅自改成整份 stdout 完全相等；這種過度收緊應列為 issues。

## 錯誤記錄完整性
- 檢查腳本有 subprocess.run / HTTP 請求等外部呼叫時，是否有對應的 try/except 並在 except 中 call errors.append()；但 `run_command` helper 可將未預期例外轉成含 `returncode=None` 的結構化結果，由呼叫端記錄 errors 與 unknown/fail 狀態。
- 若腳本有例外處理但 errors 始終為空陣列，應列為 issues。
- 檢查 bare except / except Exception 後是否有將錯誤記錄到 errors。
- 確認 `record_check` 使用單一 `raw` 參數並回傳結果 dict，呼叫端以 `checks.append(record_check(...))` 收集；helper 必須把非字串 raw payload 序列化後，將整個 `raw` 欄位交給一次 `truncate_output`，且最終值是字串。`checks_list` side effect、巢狀 raw dict、分離 stdout/stderr 參數、只截斷子欄位，或只有呼叫端截斷，都應列為 issues。

只輸出 JSON：
{
  "approved": true,
  "risk_level": "low | medium | high",
  "issues": [],
  "suggested_fix": null
}
""".strip()


FIX_SCRIPT_SYSTEM_PROMPT = """
# 角色
你是 managed data collection 腳本的精準 patch 修正器。你的任務是根據修正指令對腳本做**指定行區間的最小替換**。

# 規則
- 只修改修正指令指向的內容，不要重寫整個腳本
- 不要改動與修正指令無關的任何程式碼
- 保持腳本結構、縮排、邏輯不變
- 腳本內容已附行號（格式：`0001|code`），修正時可參考行號定位
- 優先使用 repair_instructions 裡的 issue、line_range、snippet、required_pattern 定位與修正
- fix_instructions 是原始 validator hint；repair_instructions 是精簡後的修正指令，優先依 repair_instructions 行動
- replacement 只能包含替換後的 Python 程式碼，不要包含 `0001|` 行號前綴
- 若只需新增一行，請把該 except 區塊整段以相同縮排替換，不要重排其他區塊
- 對 `normalize_record_check_contract`，這是 helper 與呼叫端的契約錯誤，不能只替換 raw 欄位：重新生成完整腳本，固定使用 `record_check(check_id, title, status, evidence, raw="") -> dict` 並由呼叫端 `checks.append(record_check(...))` 收集。不得保留 `checks_list`、`errors` 或分離 stdout/stderr 參數。
- 對 `add_truncate_in_record_check`，只修改 `record_check` 函式定義；將非字串 raw payload 先以 `json.dumps(raw, ensure_ascii=False, default=str)` 序列化，再改成 `"raw": truncate_output(raw_text)`，不可回傳巢狀 dict，也不得以呼叫端的 `truncate_output(...)` 取代。
- 正確形狀：helper 回傳的 `raw` 是一次外層 `truncate_output(...)` 的字串；錯誤形狀：只截斷 `stdout`／`stderr` 子欄位、回傳 dict，或只在呼叫端截斷。
- 對 `normalize_run_command_error_contract`，只替換 `run_command` 的指定 except 區塊；回傳 `{"stdout": "", "stderr": str(exc), "returncode": None}`，不要在 helper 內加入 `errors.append` 或 `checks.append`。
- 對一般收集流程的 `bare except / except Exception 後未將錯誤記錄到 errors`，必須在同一個 except 區塊加入 `errors.append(...)`，並確保對應 `record_check` 狀態不是 `pass`；若 target 是 `run_command_exception_handler`，遵守上一條結構化回傳契約，不要在 helper 內 append。

# 輸出格式
只能輸出一個 JSON：
{
  "line_replacements": [
    {
      "start_line": 12,
      "end_line": 15,
      "replacement": "替換後的完整行區間程式碼（不要行號前綴）"
    }
  ],
  "changes_summary": "簡短繁體中文說明改動了什麼，1-2 句"
}
""".strip()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_ai_review(payload: Any) -> AIReviewResult:
    if (
        not isinstance(payload, dict)
        or type(payload.get("approved")) is not bool
        or not isinstance(payload.get("risk_level"), str)
        or payload.get("risk_level") not in {"low", "medium", "high"}
        or not isinstance(payload.get("issues"), list)
    ):
        return {
            "approved": False,
            "risk_level": "high",
            "issues": ["AI reviewer 回傳格式不正確"],
            "suggested_fix": "請重新生成腳本",
        }

    approved = payload.get("approved") is True and not payload["issues"]
    risk_level = cast(Literal["low", "medium", "high"], payload["risk_level"])
    issues = payload["issues"]

    return {
        "approved": approved,
        "risk_level": risk_level,
        "issues": [str(issue) for issue in issues],
        "suggested_fix": payload.get("suggested_fix"),
    }


def _class_machine_display_names(
    session: Session,
    teaching_class_id: uuid.UUID,
) -> dict[str, str]:
    return {
        node.node_key: (node.name or "").strip() or node.node_key
        for node in load_class_machine_nodes(session, teaching_class_id)
    }


def _artifact_public_name(
    artifact: TeacherJudgeScriptArtifact,
    node_display_names: dict[str, str] | None,
) -> str:
    """Map a legacy `· {node_key}` name suffix to the teacher's machine name."""

    if not node_display_names:
        return artifact.name
    node_key = str(artifact.target_node_key or "")
    if not node_key:
        return artifact.name
    suffix = f" · {node_key}"
    if not artifact.name.endswith(suffix):
        return artifact.name
    machine_name = node_display_names.get(node_key)
    if not machine_name or machine_name == node_key:
        return artifact.name
    return f"{artifact.name[: -len(suffix)]} · {machine_name}"[:255]


def _artifact_to_public(
    artifact: TeacherJudgeScriptArtifact,
    node_display_names: dict[str, str] | None = None,
) -> TeacherJudgeScriptArtifactPublic:
    return TeacherJudgeScriptArtifactPublic(
        id=str(artifact.id),
        artifact_set_id=(
            str(artifact.artifact_set_id) if artifact.artifact_set_id else None
        ),
        target_node_key=artifact.target_node_key,
        source_analysis_revision=artifact.source_analysis_revision,
        teaching_class_id=str(artifact.teaching_class_id),
        session_id=str(artifact.session_id) if artifact.session_id else None,
        name=_artifact_public_name(artifact, node_display_names),
        template_key=artifact.template_key,
        rubric_snapshot_json=artifact.rubric_snapshot_json,
        source_file_id=str(artifact.source_file_id)
        if artifact.source_file_id
        else None,
        source_file_snapshot_json=artifact.source_file_snapshot_json,
        script_language=artifact.script_language.value,
        script_content=artifact.script_content,
        source=artifact.source.value,
        version=artifact.version,
        status=artifact.status.value,
        policy_check_result_json=artifact.policy_check_result_json,
        ai_review_result_json=artifact.ai_review_result_json,
        created_by=str(artifact.created_by) if artifact.created_by else None,
        approved_by=str(artifact.approved_by) if artifact.approved_by else None,
        created_at=artifact.created_at.isoformat(),
        updated_at=artifact.updated_at.isoformat(),
        approved_at=artifact.approved_at.isoformat() if artifact.approved_at else None,
    )


def _rubric_snapshot(
    analysis: TeacherJudgeRubricAnalysis, template_key: str
) -> dict[str, Any]:
    snapshot = analysis.model_dump(mode="json")
    snapshot["template_key"] = template_key
    return snapshot


def _template_commands_snapshot(
    commands: list[TeacherJudgeTemplateCommand] | None,
) -> list[TemplateCommandSnapshot]:
    if not commands:
        return []

    return [
        {
            "command_key": command.command_key,
            "command_label": command.command_label,
            "category": command.category,
            "command_template": command.command_template,
            "description": command.description,
            "risk_level": command.risk_level,
            "requires_confirmation": command.requires_confirmation,
        }
        for command in commands
    ]


def _snapshot_uses_legacy_command_references(
    rubric_snapshot: dict[str, Any],
) -> bool:
    """Return whether a snapshot still needs the legacy command catalog."""
    raw_items = rubric_snapshot.get("items")
    if not isinstance(raw_items, list):
        return False
    return any(
        isinstance(step, dict)
        and (step.get("template_key") or step.get("command_key"))
        for item in raw_items
        if isinstance(item, dict)
        for step in (item.get("check_steps") or [])
    )


def _with_template_command_catalog(
    rubric_snapshot: dict[str, Any],
    template_commands: list[TeacherJudgeTemplateCommand] | None,
) -> dict[str, Any]:
    snapshot = dict(rubric_snapshot)
    command_catalog = _template_commands_snapshot(template_commands)
    if command_catalog and _snapshot_uses_legacy_command_references(snapshot):
        snapshot["template_commands"] = command_catalog
    return snapshot


def _previous_review_feedback(
    artifact: TeacherJudgeScriptArtifact,
) -> PreviousReviewFeedback | None:
    policy_check = artifact.policy_check_result_json or {}
    ai_review = artifact.ai_review_result_json or {}
    safety_issues = policy_check.get("safety_issues")
    policy_issues = (
        safety_issues if isinstance(safety_issues, list) else policy_check.get("issues")
    )
    quality_issues = policy_check.get("quality_issues")
    coverage = policy_check.get("coverage")
    review_attempts = policy_check.get("review_attempts")
    ai_issues = ai_review.get("issues")
    feedback = {
        "policy_approved": (
            policy_check.get("safety_approved")
            if "safety_approved" in policy_check
            else policy_check.get("approved")
        ),
        "policy_issues": policy_issues if isinstance(policy_issues, list) else [],
        "quality_approved": policy_check.get("quality_approved"),
        "quality_issues": quality_issues if isinstance(quality_issues, list) else [],
        "ai_review_approved": ai_review.get("approved"),
        "ai_review_issues": ai_issues if isinstance(ai_issues, list) else [],
        "ai_review_suggested_fix": ai_review.get("suggested_fix"),
    }
    if isinstance(coverage, dict):
        coverage_issues = coverage.get("issues")
        uncovered_items = coverage.get("uncovered_items")
        available_check_ids = coverage.get("available_check_ids")
        feedback.update(
            {
                "coverage_approved": coverage.get("approved"),
                "coverage_issues": (
                    coverage_issues if isinstance(coverage_issues, list) else []
                ),
                "uncovered_rubric_items": (
                    uncovered_items if isinstance(uncovered_items, list) else []
                ),
                "available_check_ids": (
                    available_check_ids
                    if isinstance(available_check_ids, list)
                    else []
                ),
            }
        )
    if isinstance(review_attempts, list):
        for attempt in reversed(review_attempts):
            if not isinstance(attempt, dict) or attempt.get("phase") not in {
                "static",
                "coverage",
            }:
                continue
            raw_hints = attempt.get("fix_hints")
            if not isinstance(raw_hints, list):
                continue
            repair_hints = [
                cast("FixHint", hint)
                for hint in raw_hints
                if isinstance(hint, dict)
            ]
            if repair_hints:
                feedback["repair_guidance"] = _repair_instructions(repair_hints)[:5]
            break
    has_failed_review = (
        feedback["policy_approved"] is False
        or feedback["quality_approved"] is False
        or feedback.get("coverage_approved") is False
        or feedback["ai_review_approved"] is False
        or bool(feedback["policy_issues"])
        or bool(feedback["quality_issues"])
        or bool(feedback.get("coverage_issues"))
        or bool(feedback.get("uncovered_rubric_items"))
        or bool(feedback["ai_review_issues"])
        or bool(feedback["ai_review_suggested_fix"])
    )
    if not has_failed_review:
        return None
    return cast("PreviousReviewFeedback", feedback)


def _resolve_status(
    policy_check: GateResult,
    ai_review: AIReviewResult,
) -> TeacherJudgeScriptStatus:
    coverage = policy_check.get("coverage")
    coverage_approved = (
        coverage.get("approved") is True
        if isinstance(coverage, dict)
        else True
    )
    if (
        policy_check.get("approved") is True
        and coverage_approved
        and ai_review.get("approved") is True
    ):
        return TeacherJudgeScriptStatus.approved
    return TeacherJudgeScriptStatus.review_failed


def _merge_gate_results(
    safety_check: CheckResult,
    quality_check: CheckResult,
) -> GateResult:
    safety_issues = safety_check.get("issues")
    quality_issues = quality_check.get("issues")
    quality_warnings = quality_check.get("warnings")
    combined_issues = [
        *(
            [str(issue) for issue in safety_issues]
            if isinstance(safety_issues, list)
            else []
        ),
        *(
            [str(issue) for issue in quality_issues]
            if isinstance(quality_issues, list)
            else []
        ),
    ]
    approved = (
        safety_check.get("approved") is True and quality_check.get("approved") is True
    )
    return {
        "approved": approved,
        "blocked": not approved,
        "risk_level": "low" if approved else "high",
        "issues": list(dict.fromkeys(combined_issues)),
        "safety_approved": safety_check.get("approved") is True,
        "safety_issues": [str(issue) for issue in safety_issues]
        if isinstance(safety_issues, list)
        else [],
        "quality_approved": quality_check.get("approved") is True,
        "quality_issues": [str(issue) for issue in quality_issues]
        if isinstance(quality_issues, list)
        else [],
        "quality_warnings": [str(warning) for warning in quality_warnings]
        if isinstance(quality_warnings, list)
        else [],
    }


def _merge_peer_policy(
    safety_check: CheckResult,
    peer_check: CheckResult,
) -> CheckResult:
    """Fold the peer-specific safety gate into the ordinary policy gate."""

    if peer_check.get("approved") is True:
        return safety_check
    return {
        **safety_check,
        "approved": False,
        "blocked": True,
        "risk_level": "high",
        "issues": list(
            dict.fromkeys(
                [
                    *safety_check.get("issues", []),
                    *peer_check.get("issues", []),
                ]
            )
        ),
        "fix_hints": [
            *safety_check.get("fix_hints", []),
            *peer_check.get("fix_hints", []),
        ],
    }


def _gate_attempt_record(
    *,
    attempt: int,
    safety_check: CheckResult,
    quality_check: CheckResult,
    fix_hints: list[FixHint],
) -> dict[str, object]:
    safety_issues = safety_check.get("issues")
    quality_issues = quality_check.get("issues")
    quality_warnings = quality_check.get("warnings")
    return {
        "attempt": attempt,
        "safety_approved": safety_check.get("approved") is True,
        "safety_issues": [str(issue) for issue in safety_issues]
        if isinstance(safety_issues, list)
        else [],
        "quality_approved": quality_check.get("approved") is True,
        "quality_issues": [str(issue) for issue in quality_issues]
        if isinstance(quality_issues, list)
        else [],
        "quality_warnings": [str(warning) for warning in quality_warnings]
        if isinstance(quality_warnings, list)
        else [],
        "fix_hints": fix_hints,
    }


@lru_cache(maxsize=2048)
def _cached_hint_fingerprint(hint_key: str) -> str:
    """Cache stable sha256 fingerprints for retry signatures.

    Pure memoization: same input string yields the same 16-hex digest as the
    inline hashlib call it replaces. Bounded LRU, no retry-semantics change.
    """
    return hashlib.sha256(hint_key.encode("utf-8")).hexdigest()[:16]


@lru_cache(maxsize=2048)
def _cached_issue_fingerprint(normalized_issue: str) -> str:
    """Cache stable sha256 fingerprints for retry signatures (see above)."""
    return hashlib.sha256(normalized_issue.encode("utf-8")).hexdigest()[:16]


def _failure_signature(
    *,
    phase: str,
    issues: list[str],
    fix_hints: list[FixHint],
) -> str:
    """Build a stable, non-sensitive key for the repeated-error guard."""
    hint_parts = [
        _cached_hint_fingerprint(
            ":".join(
                str(hint.get(key) or "")
                for key in (
                    "type",
                    "target",
                    "field",
                    "function",
                    "command",
                    "required_pattern",
                )
            )
        )
        for hint in fix_hints
    ]
    issue_parts = [
        _cached_issue_fingerprint(" ".join(str(issue).split()).lower())
        for issue in issues
    ]
    parts = [
        part
        for part in (
            *(f"hint:{fingerprint}" for fingerprint in hint_parts),
            *(f"issue:{fingerprint}" for fingerprint in issue_parts),
        )
        if part.strip(":")
    ]
    if not parts:
        parts = ["unclassified"]
    return f"{phase}:{'|'.join(dict.fromkeys(parts))[:300]}"


def _coverage_failure_signature(coverage: CoverageResult) -> str:
    """Keep repeated coverage failures stable across changing ID examples.

    The concrete unknown IDs are useful feedback, but they are not a useful
    retry identity: a model can rename the same invalid IDs on every call and
    otherwise evade the repeated-error guard until the global retry limit.
    Coverage failure kinds remain distinct so a genuinely different repair
    opportunity can still receive another attempt.
    """

    failure_kinds = sorted(
        {
            str(hint.get("type") or "unknown")
            for hint in coverage.get("fix_hints", [])
            if isinstance(hint, dict)
        }
    )
    return _failure_signature(
        phase="coverage",
        issues=failure_kinds or ["unclassified"],
        fix_hints=[],
    )


def _retry_summary(
    *,
    retry_count: int,
    failure_counts: dict[str, int],
    stop_reason: str,
) -> dict[str, object]:
    return {
        "retry_count": retry_count,
        "max_retries": SCRIPT_GENERATION_MAX_RETRIES,
        "same_failure_retry_limit": SCRIPT_GENERATION_SAME_FAILURE_MAX_RETRIES,
        "stop_reason": stop_reason,
        "failure_counts": dict(failure_counts),
    }


# Model-side failures that a fresh call can plausibly recover from: malformed
# model output (502) and timeouts/upstream errors (502/504). A missing model
# configuration (503) is a setup problem, not a retry case.
MODEL_CALL_RETRYABLE_STATUS_CODES = frozenset({502, 504})


def _model_issue(exc: HTTPException) -> str:
    if isinstance(exc.detail, dict):
        return str(exc.detail.get("message") or exc.detail)
    return str(exc.detail)


def _feedback_snapshot(
    *,
    rubric_snapshot: dict[str, Any],
    attempt: int,
    gate_result: GateResult,
    ai_review: AIReviewResult | None = None,
    coverage: CoverageResult | None = None,
    fix_hints: list[FixHint] | None = None,
) -> dict[str, Any]:
    feedback: dict[str, Any] = {
        "attempt": attempt,
        "policy_approved": gate_result.get("safety_approved"),
        "policy_issues": gate_result.get("safety_issues", []),
        "quality_approved": gate_result.get("quality_approved"),
        "quality_issues": gate_result.get("quality_issues", []),
    }
    if coverage is not None:
        feedback.update(
            {
                "coverage_approved": coverage.get("approved"),
                "coverage_issues": coverage.get("issues", []),
                "uncovered_rubric_items": coverage.get("uncovered_items", []),
                "available_check_ids": coverage.get("available_check_ids", []),
            }
        )
    if ai_review is not None:
        feedback.update(
            {
                "ai_review_approved": ai_review.get("approved"),
                "ai_review_issues": ai_review.get("issues", []),
                "ai_review_suggested_fix": ai_review.get("suggested_fix"),
            }
        )
    if fix_hints:
        # Keep the fresh-generation feedback bounded and focused on the
        # validator-owned repair target; do not copy the script or raw output.
        feedback["repair_guidance"] = _repair_instructions(fix_hints)[:5]
    next_snapshot = dict(rubric_snapshot)
    next_snapshot["previous_review_feedback"] = feedback
    return next_snapshot


def _repair_instructions(fix_hints: list[FixHint]) -> list[dict[str, object]]:
    instructions: list[dict[str, object]] = []
    for hint in fix_hints:
        line_range: list[int] | None = None
        lineno = hint.get("lineno")
        end_lineno = hint.get("end_lineno")
        if isinstance(lineno, int) and isinstance(end_lineno, int):
            line_range = [lineno, end_lineno]

        issue = str(
            hint.get("description")
            or "; ".join(str(issue) for issue in hint.get("issues", []))
            or hint.get("type")
            or "未指定修正項目"
        )
        instruction: dict[str, object] = {
            "issue": issue,
            "fix_goal": _fix_goal_for_hint(hint),
            "target": str(hint.get("target") or hint.get("type") or "script"),
        }
        if line_range:
            instruction["line_range"] = line_range
        if hint.get("snippet"):
            instruction["snippet"] = str(hint["snippet"])
        if hint.get("required_pattern"):
            instruction["required_pattern"] = str(hint["required_pattern"])
        if hint.get("suggested_fix"):
            instruction["suggested_fix"] = str(hint["suggested_fix"])
        instructions.append(instruction)
    return instructions


def _fix_goal_for_hint(hint: FixHint) -> str:
    hint_type = hint.get("type")
    if hint_type == "normalize_record_check_contract":
        return (
            "此錯誤需要重新生成完整腳本：record_check 必須使用 "
            "(check_id, title, status, evidence, raw=\"\") 並回傳單一結果 dict，"
            "呼叫端使用 checks.append(record_check(...))；不得只修改 helper 保留 checks_list side effect。"
        )
    if hint_type == "add_truncate_in_record_check":
        return (
            '只修改 record_check 函式定義；將非字串 raw payload 先序列化，'
            '再將回傳物件的 "raw" 欄位交給一次 truncate_output(raw_text)，'
            "呼叫端保持傳入原始 raw，不要只修改呼叫端。"
        )
    if hint_type == "normalize_run_command_error_contract":
        return (
            '只替換 run_command 指定 except 區塊；回傳 '
            '{"stdout": "", "stderr": str(exc), "returncode": None}，'
            "不要在 helper 內操作 errors 或 checks，由呼叫端處理 returncode=None。"
        )
    if hint_type == "add_errors_append_in_except":
        return (
            '只替換指定 except 區塊；加入 errors.append(f"<check_id>: ...")，'
            "並確保該錯誤路徑輸出的 record_check status 是 unknown 或 fail，不可為 pass。"
        )
    if hint_type == "ai_reviewer_feedback":
        return "依 AI reviewer issues 做最小行區間替換，不要重寫整份腳本。"
    if hint_type == "fix_coverage_refs":
        return (
            "coverage 的 check_id 只能使用 previous_review_feedback."
            "available_check_ids 中已由後端解析出的值；不要自行創造或改寫 ID。"
        )
    if hint_type == "cover_rubric_items":
        return (
            "為每個 uncovered rubric item 補上真實的 record_check 證據；"
            "若目前無法安全取證，保留缺口，不要虛構 coverage 映射。"
        )
    if hint_type == "provide_coverage_mapping":
        return "補上 coverage，且 check_id 必須與腳本實際可解析的 record_check ID 完全一致。"
    return "依 issue 做最小行區間替換，保持未相關程式碼不變。"


def _fix_hint_log_summary(fix_hints: list[FixHint]) -> list[dict[str, object]]:
    summary: list[dict[str, object]] = []
    for hint in fix_hints[:5]:
        item: dict[str, object] = {
            "type": str(hint.get("type") or "unknown"),
            "target": str(hint.get("target") or ""),
        }
        if isinstance(hint.get("lineno"), int):
            item["line"] = hint["lineno"]
        if hint.get("description"):
            item["description"] = str(hint["description"])
        summary.append(item)
    return summary


def _line_replacement_log_summary(raw_replacements: Any) -> list[dict[str, object]]:
    if not isinstance(raw_replacements, list):
        return []
    summary: list[dict[str, object]] = []
    for raw in raw_replacements[:5]:
        if not isinstance(raw, dict):
            continue
        replacement = raw.get("replacement")
        replacement_lines = (
            len(str(replacement).splitlines()) if isinstance(replacement, str) else 0
        )
        summary.append(
            {
                "start_line": raw.get("start_line"),
                "end_line": raw.get("end_line"),
                "replacement_lines": replacement_lines,
            }
        )
    return summary


async def generate_script_content(
    *,
    rubric_snapshot: dict[str, Any],
    template_key: str,
) -> tuple[str, list[CoverageMapping] | None, dict[str, Any]]:
    if not settings.VLLM_MODEL_NAME:
        raise HTTPException(
            status_code=503, detail=t("artifact.model_not_configured")
        )

    user_payload: dict[str, Any] = {
        "rubric_snapshot": rubric_snapshot,
        "previous_review_feedback": rubric_snapshot.get(
            "previous_review_feedback"
        ),
    }
    if rubric_snapshot.get("template_commands") or _snapshot_uses_legacy_command_references(
        rubric_snapshot
    ):
        # Legacy snapshots retain their catalog context until conversion has
        # completed. New flat snapshots deliberately omit these fields.
        user_payload["template_key"] = template_key
        user_payload["template_commands"] = rubric_snapshot.get(
            "template_commands", []
        )

    payload = apply_thinking_control(
        {
            "model": settings.VLLM_MODEL_NAME,
            "messages": [
                {"role": "system", "content": SCRIPT_GENERATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "max_tokens": settings.VLLM_CHAT_MAX_TOKENS,
            "temperature": 0.1,
            "top_p": settings.VLLM_TOP_P,
            "response_format": {"type": "json_object"},
        },
        settings.VLLM_ENABLE_THINKING,
    )
    content, metrics = await _call_vllm(payload, timeout=float(settings.VLLM_TIMEOUT))

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning("Teacher Judge script generation output was not JSON: %s", exc)
        raise HTTPException(
            status_code=502, detail=t("artifact.generation_not_json")
        ) from exc

    if not isinstance(parsed, dict) or not isinstance(parsed.get("script_content"), str):
        logger.warning("Teacher Judge script generation output missing script_content")
        raise HTTPException(status_code=502, detail=t("artifact.generation_not_json"))
    script_content = parsed["script_content"].strip()
    if not script_content:
        logger.warning("Teacher Judge script generation returned empty script_content")
        raise HTTPException(status_code=502, detail=t("artifact.no_script_content"))
    coverage = parse_coverage_payload(parsed.get("coverage"))
    if coverage is None:
        logger.warning(
            "Teacher Judge script generation missing or invalid coverage mapping"
        )
    return script_content, coverage, dict(metrics)


async def review_script_with_ai(
    *,
    script_content: str,
    rubric_snapshot: dict[str, Any],
) -> tuple[AIReviewResult, dict[str, Any]]:
    if not settings.VLLM_MODEL_NAME:
        raise HTTPException(
            status_code=503, detail=t("artifact.model_not_configured")
        )

    payload = apply_thinking_control(
        {
            "model": settings.VLLM_MODEL_NAME,
            "messages": [
                {"role": "system", "content": AI_REVIEWER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "script_content": script_content,
                            "rubric_snapshot": rubric_snapshot,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "max_tokens": min(settings.VLLM_CHAT_MAX_TOKENS, 2048),
            "temperature": 0.0,
            "top_p": settings.VLLM_TOP_P,
            "response_format": {"type": "json_object"},
        },
        settings.VLLM_ENABLE_THINKING,
    )
    content, metrics = await _call_vllm(payload, timeout=float(settings.VLLM_TIMEOUT))

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {}
    return _normalize_ai_review(parsed), dict(metrics)


def _apply_line_replacements(
    script_content: str,
    raw_replacements: Any,
) -> str:
    if not isinstance(raw_replacements, list) or not raw_replacements:
        raise HTTPException(
            status_code=502, detail=t("artifact.no_line_replacements")
        )

    lines = script_content.split("\n")
    replacements: list[tuple[int, int, str]] = []
    for raw in raw_replacements:
        if not isinstance(raw, dict):
            raise HTTPException(
                status_code=502, detail=t("artifact.fix_format_invalid")
            )
        start_line = raw.get("start_line")
        end_line = raw.get("end_line")
        replacement = raw.get("replacement")
        if (
            not isinstance(start_line, int)
            or isinstance(start_line, bool)
            or not isinstance(end_line, int)
            or isinstance(end_line, bool)
            or not isinstance(replacement, str)
        ):
            raise HTTPException(
                status_code=502, detail=t("artifact.fix_line_number_invalid")
            )
        if start_line < 1 or end_line < start_line or end_line > len(lines):
            raise HTTPException(
                status_code=502, detail=t("artifact.fix_line_out_of_range")
            )
        replacements.append((start_line, end_line, replacement))

    sorted_replacements = sorted(replacements, key=lambda item: item[0])
    previous_end = 0
    for start_line, end_line, _replacement in sorted_replacements:
        if start_line <= previous_end:
            raise HTTPException(
                status_code=502, detail=t("artifact.fix_line_overlap")
            )
        previous_end = end_line

    patched_lines = list(lines)
    for start_line, end_line, replacement in reversed(sorted_replacements):
        replacement_lines = replacement.split("\n") if replacement else []
        patched_lines[start_line - 1 : end_line] = replacement_lines

    result = "\n".join(patched_lines).strip()
    if not result:
        raise HTTPException(status_code=502, detail=t("artifact.fix_no_result"))
    return result


async def fix_script_content(
    *,
    script_content: str,
    fix_hints: list[FixHint],
) -> tuple[str, dict[str, Any]]:
    if not settings.VLLM_MODEL_NAME:
        raise HTTPException(
            status_code=503, detail=t("artifact.model_not_configured")
        )

    lines = script_content.split("\n")
    numbered = "\n".join(f"{i + 1:04d}|{line}" for i, line in enumerate(lines))
    repair_instructions = _repair_instructions(fix_hints)
    logger.info(
        "Teacher Judge script patch requested: hints=%s",
        _fix_hint_log_summary(fix_hints),
    )

    payload = apply_thinking_control(
        {
            "model": settings.VLLM_MODEL_NAME,
            "messages": [
                {"role": "system", "content": FIX_SCRIPT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "script_with_lines": numbered,
                            "repair_instructions": repair_instructions,
                            "fix_instructions": fix_hints,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "max_tokens": settings.VLLM_CHAT_MAX_TOKENS,
            "temperature": 0.1,
            "top_p": settings.VLLM_TOP_P,
            "response_format": {"type": "json_object"},
        },
        settings.VLLM_ENABLE_THINKING,
    )
    content, metrics = await _call_vllm(payload, timeout=float(settings.VLLM_TIMEOUT))

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502, detail=t("artifact.fix_not_json")
        ) from exc

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail=t("artifact.fix_not_json"))
    logger.info(
        "Teacher Judge script patch response: replacements=%s summary=%s",
        _line_replacement_log_summary(parsed.get("line_replacements")),
        parsed.get("changes_summary"),
    )
    fixed_content = _apply_line_replacements(
        script_content,
        parsed.get("line_replacements"),
    )
    return fixed_content, dict(metrics)


async def build_reviewed_script(
    *,
    rubric_snapshot: dict[str, Any],
    template_key: str,
    include_usage: bool = False,
) -> (
    tuple[
        str,
        GateResult,
        AIReviewResult,
        TeacherJudgeScriptStatus,
    ]
    | tuple[
        str,
        GateResult,
        AIReviewResult,
        TeacherJudgeScriptStatus,
        list[ScriptUsageRecord],
    ]
):
    attempt_snapshot = dict(rubric_snapshot)
    usage_records: list[ScriptUsageRecord] = []
    attempt_records: list[dict[str, object]] = []
    failure_counts: dict[str, int] = {}
    retry_count = 0
    generation_error: str | None = None
    review_call_error: str | None = None

    gate_result: GateResult = {
        "approved": False,
        "blocked": True,
        "risk_level": "high",
        "issues": [],
        "safety_approved": False,
        "safety_issues": [],
        "quality_approved": False,
        "quality_issues": [],
        "quality_warnings": [],
    }
    last_ai_review: AIReviewResult = {
        "approved": False,
        "risk_level": "high",
        "issues": [],
        "suggested_fix": None,
    }
    stop_reason = "unrecoverable_error"
    # stop_reason 在各 break 分支設定，其餘路徑皆 return。
    # script_content is None while a fresh generation is required: on the first
    # call, or after a failed patch fallback. last_gated_content keeps the most
    # recent candidate that reached the gates for the final result.
    script_content: str | None = None
    last_gated_content: str | None = None
    coverage: list[CoverageMapping] | None = None
    coverage_state: dict[str, Any] | None = None
    # True when script_content was produced by a line-replacement patch: the
    # stored coverage must then be re-aligned against the patched script's
    # actual record_check ids before it can be trusted.
    coverage_needs_realign = False
    while True:
        if script_content is None:
            try:
                script_content, coverage, metrics = _generation_result(
                    await generate_script_content(
                        rubric_snapshot=attempt_snapshot,
                        template_key=template_key,
                    )
                )
            except HTTPException as exc:
                if exc.status_code not in MODEL_CALL_RETRYABLE_STATUS_CODES:
                    raise
                generation_error = _model_issue(exc)
                signature = _failure_signature(
                    phase="generation",
                    issues=[generation_error],
                    fix_hints=[],
                )
                failure_counts[signature] = failure_counts.get(signature, 0) + 1
                attempt_records.append(
                    {
                        "attempt": len(attempt_records) + 1,
                        "phase": "generation",
                        "failure_signature": signature,
                        "retry_count": retry_count,
                        "same_failure_count": failure_counts[signature],
                        "generation_status_code": exc.status_code,
                        "generation_issues": [generation_error],
                    }
                )
                logger.warning(
                    "Teacher Judge script generation failed retry=%s/%s same_failure=%s/%s signature=%s error=%s",
                    retry_count,
                    SCRIPT_GENERATION_MAX_RETRIES,
                    failure_counts[signature],
                    SCRIPT_GENERATION_SAME_FAILURE_MAX_RETRIES,
                    signature,
                    generation_error,
                )
                if failure_counts[signature] > SCRIPT_GENERATION_SAME_FAILURE_MAX_RETRIES:
                    stop_reason = "same_failure_limit"
                    break
                if retry_count >= SCRIPT_GENERATION_MAX_RETRIES:
                    stop_reason = "total_retry_limit"
                    break
                retry_count += 1
                continue
            usage_records.append(
                {
                    "call_type": CALL_TJ_SCRIPT_GENERATION,
                    "metrics": metrics,
                }
            )
            generation_error = None
            coverage_needs_realign = False

        last_gated_content = script_content
        safety_check = check_script_policy(script_content)
        safety_check = _merge_peer_policy(
            safety_check,
            check_peer_runtime_policy(script_content, attempt_snapshot),
        )
        quality_check = check_script_quality(script_content)
        gate_result = _merge_gate_results(safety_check, quality_check)

        if not gate_result["approved"]:
            fix_hints = safety_check.get("fix_hints", []) + quality_check.get(
                "fix_hints", []
            )
            signature = _failure_signature(
                phase="static",
                issues=gate_result["issues"],
                fix_hints=fix_hints,
            )
            same_failure_count = failure_counts.get(signature, 0) + 1
            failure_counts[signature] = same_failure_count
            repair_mode = (
                "stop"
                if same_failure_count > SCRIPT_GENERATION_SAME_FAILURE_MAX_RETRIES
                or retry_count >= SCRIPT_GENERATION_MAX_RETRIES
                else (
                    "fresh_generation"
                    if same_failure_count >= 2
                    or not fix_hints
                    or any(
                        hint.get("type") == "normalize_record_check_contract"
                        for hint in fix_hints
                    )
                    else "line_patch"
                )
            )
            attempt_record = _gate_attempt_record(
                attempt=len(attempt_records) + 1,
                safety_check=safety_check,
                quality_check=quality_check,
                fix_hints=fix_hints,
            )
            attempt_record.update(
                {
                    "phase": "static",
                    "failure_signature": signature,
                    "retry_count": retry_count,
                    "same_failure_count": same_failure_count,
                    "repair_mode": repair_mode,
                }
            )
            attempt_records.append(attempt_record)
            logger.warning(
                "Teacher Judge script gate failed retry=%s/%s same_failure=%s/%s signature=%s hints=%s",
                retry_count,
                SCRIPT_GENERATION_MAX_RETRIES,
                same_failure_count,
                SCRIPT_GENERATION_SAME_FAILURE_MAX_RETRIES,
                signature,
                _fix_hint_log_summary(fix_hints),
            )

            if same_failure_count > SCRIPT_GENERATION_SAME_FAILURE_MAX_RETRIES:
                stop_reason = "same_failure_limit"
                break
            if retry_count >= SCRIPT_GENERATION_MAX_RETRIES:
                stop_reason = "total_retry_limit"
                break

            retry_count += 1
            attempt_snapshot = _feedback_snapshot(
                rubric_snapshot=rubric_snapshot,
                attempt=len(attempt_records),
                gate_result=gate_result,
                fix_hints=fix_hints,
            )
            if repair_mode == "fresh_generation" or not fix_hints:
                script_content = None
                coverage = None
                coverage_needs_realign = False
                continue
            if repair_mode == "line_patch":
                try:
                    script_content, metrics = _script_result(
                        await fix_script_content(
                            script_content=script_content,
                            fix_hints=fix_hints,
                        )
                    )
                except HTTPException as exc:
                    logger.warning(
                        "Teacher Judge script patch failed; falling back to regenerate: %s",
                        exc.detail,
                    )
                    script_content = None
                    coverage = None
                    coverage_needs_realign = False
                    continue
            usage_records.append(
                {
                    "call_type": CALL_TJ_SCRIPT_GENERATION,
                    "metrics": metrics,
                }
            )
            coverage_needs_realign = True
            continue

        # ── rubric coverage 閘門 ──
        # 生成回應必須附上 coverage 映射；映射引用的 check/rubric id 必須真實
        # 存在，且每個 rubric item 都至少被一個 check 覆蓋。patch 產生的腳本
        # 先以實際 record_check ids 對帳，再驗證完整性。
        if coverage is None:
            missing_issue = "模型未提供 rubric 覆蓋映射（coverage）"
            coverage_check: CoverageResult = {
                "approved": False,
                "issues": [missing_issue],
                "fix_hints": [
                    {
                        "type": "provide_coverage_mapping",
                        "description": "生成回應必須附上 coverage：每個 record_check 對應的 rubric item ids",
                    }
                ],
                "mappings": [],
                "uncovered_items": [],
                "available_check_ids": [],
            }
            effective_coverage: list[CoverageMapping] | None = None
        else:
            effective_coverage = (
                realign_coverage_to_script(coverage, script_content)
                if coverage_needs_realign
                else coverage
            )
            coverage_check = validate_coverage(
                coverage=effective_coverage,
                script_content=script_content,
                rubric_items=cast(
                    "list[dict[str, Any]]", rubric_snapshot.get("items") or []
                ),
            )
        coverage_state = {
            "approved": coverage_check["approved"],
            "issues": coverage_check["issues"],
            "mappings": coverage_check["mappings"],
            "uncovered_items": coverage_check["uncovered_items"],
            "available_check_ids": coverage_check["available_check_ids"],
        }
        if effective_coverage is not None:
            coverage = effective_coverage
            coverage_needs_realign = False

        if not coverage_check["approved"]:
            coverage_issues = coverage_check["issues"]
            for issue in coverage_issues:
                if issue not in gate_result["issues"]:
                    gate_result["issues"].append(issue)
            signature = _coverage_failure_signature(coverage_check)
            failure_counts[signature] = failure_counts.get(signature, 0) + 1
            attempt_records.append(
                {
                    "attempt": len(attempt_records) + 1,
                    "phase": "coverage",
                    "failure_signature": signature,
                    "retry_count": retry_count,
                    "same_failure_count": failure_counts[signature],
                    "coverage_issues": coverage_issues,
                    "uncovered_rubric_items": coverage_check["uncovered_items"],
                    "available_check_ids": coverage_check["available_check_ids"],
                    "fix_hints": coverage_check["fix_hints"],
                }
            )
            logger.warning(
                "Teacher Judge script coverage failed retry=%s/%s same_failure=%s/%s signature=%s issues=%s",
                retry_count,
                SCRIPT_GENERATION_MAX_RETRIES,
                failure_counts[signature],
                SCRIPT_GENERATION_SAME_FAILURE_MAX_RETRIES,
                signature,
                coverage_issues,
            )
            if failure_counts[signature] > SCRIPT_GENERATION_SAME_FAILURE_MAX_RETRIES:
                stop_reason = "same_failure_limit"
                break
            if retry_count >= SCRIPT_GENERATION_MAX_RETRIES:
                stop_reason = "total_retry_limit"
                break
            retry_count += 1
            attempt_snapshot = _feedback_snapshot(
                rubric_snapshot=rubric_snapshot,
                attempt=len(attempt_records),
                gate_result=gate_result,
                coverage=coverage_check,
                fix_hints=coverage_check["fix_hints"],
            )
            # 補一個缺失的收集項目不適合行區間 patch，直接重新生成。
            script_content = None
            continue

        review_call_error = None
        while True:
            try:
                last_ai_review, metrics = _review_result(
                    await review_script_with_ai(
                        script_content=script_content,
                        rubric_snapshot=attempt_snapshot,
                    )
                )
            except HTTPException as exc:
                if exc.status_code not in MODEL_CALL_RETRYABLE_STATUS_CODES:
                    raise
                review_call_error = _model_issue(exc)
                signature = _failure_signature(
                    phase="ai_review_call",
                    issues=[review_call_error],
                    fix_hints=[],
                )
                failure_counts[signature] = failure_counts.get(signature, 0) + 1
                attempt_records.append(
                    {
                        "attempt": len(attempt_records) + 1,
                        "phase": "ai_review_call",
                        "failure_signature": signature,
                        "retry_count": retry_count,
                        "same_failure_count": failure_counts[signature],
                        "ai_review_issues": [f"AI 複核呼叫失敗：{review_call_error}"],
                    }
                )
                logger.warning(
                    "Teacher Judge AI review call failed retry=%s/%s same_failure=%s/%s signature=%s error=%s",
                    retry_count,
                    SCRIPT_GENERATION_MAX_RETRIES,
                    failure_counts[signature],
                    SCRIPT_GENERATION_SAME_FAILURE_MAX_RETRIES,
                    signature,
                    review_call_error,
                )
                if failure_counts[signature] > SCRIPT_GENERATION_SAME_FAILURE_MAX_RETRIES:
                    stop_reason = "same_failure_limit"
                    break
                if retry_count >= SCRIPT_GENERATION_MAX_RETRIES:
                    stop_reason = "total_retry_limit"
                    break
                retry_count += 1
                continue
            usage_records.append(
                {
                    "call_type": CALL_TJ_SCRIPT_REVIEW,
                    "metrics": metrics,
                }
            )
            review_call_error = None
            break
        if review_call_error is not None:
            break

        if last_ai_review.get("approved") is True:
            stop_reason = "passed"
            break

        ai_fix_hints: list[FixHint] = [
            cast(
                "FixHint",
                {
                    "type": "ai_reviewer_feedback",
                    "issues": last_ai_review.get("issues", []),
                    "suggested_fix": last_ai_review.get("suggested_fix"),
                },
            )
        ]
        signature = _failure_signature(
            phase="ai_review",
            issues=last_ai_review.get("issues", []),
            fix_hints=ai_fix_hints,
        )
        failure_counts[signature] = failure_counts.get(signature, 0) + 1
        attempt_records.append(
            {
                "attempt": len(attempt_records) + 1,
                "phase": "ai_review",
                "failure_signature": signature,
                "retry_count": retry_count,
                "same_failure_count": failure_counts[signature],
                "safety_approved": gate_result["safety_approved"],
                "safety_issues": gate_result["safety_issues"],
                "quality_approved": gate_result["quality_approved"],
                "quality_issues": gate_result["quality_issues"],
                "quality_warnings": gate_result.get("quality_warnings", []),
                "ai_review_issues": last_ai_review.get("issues", []),
                "ai_review_suggested_fix": last_ai_review.get("suggested_fix"),
                "fix_hints": ai_fix_hints,
            }
        )
        if not last_ai_review.get("issues") and not last_ai_review.get(
            "suggested_fix"
        ):
            stop_reason = "unrecoverable_error"
            break
        if failure_counts[signature] > SCRIPT_GENERATION_SAME_FAILURE_MAX_RETRIES:
            stop_reason = "same_failure_limit"
            break
        if retry_count >= SCRIPT_GENERATION_MAX_RETRIES:
            stop_reason = "total_retry_limit"
            break

        retry_count += 1
        attempt_snapshot = _feedback_snapshot(
            rubric_snapshot=rubric_snapshot,
            attempt=len(attempt_records),
            gate_result=gate_result,
            ai_review=last_ai_review,
        )
        try:
            script_content, metrics = _script_result(
                await fix_script_content(
                    script_content=script_content,
                    fix_hints=ai_fix_hints,
                )
            )
        except HTTPException as exc:
            logger.warning(
                "Teacher Judge AI feedback patch failed; falling back to regenerate: %s",
                exc.detail,
            )
            script_content = None
            continue
        usage_records.append(
            {
                "call_type": CALL_TJ_SCRIPT_GENERATION,
                "metrics": metrics,
            }
        )
        coverage_needs_realign = True

    if generation_error is not None:
        gate_result["generation_error"] = generation_error
        if generation_error not in gate_result["issues"]:
            gate_result["issues"] = [*gate_result["issues"], generation_error]

    if review_call_error is not None:
        review_issue = f"AI 複核呼叫失敗：{review_call_error}"
        if review_issue not in gate_result["issues"]:
            gate_result["issues"] = [*gate_result["issues"], review_issue]

    if coverage_state is not None:
        gate_result["coverage"] = coverage_state
        if coverage_state.get("approved") is not True:
            gate_result["approved"] = False
            gate_result["blocked"] = True
            gate_result["risk_level"] = "high"

    gate_result["review_attempts"] = attempt_records
    gate_result["retry_summary"] = _retry_summary(
        retry_count=retry_count,
        failure_counts=failure_counts,
        stop_reason=stop_reason,
    )
    status = _resolve_status(gate_result, last_ai_review)
    final_content = (
        script_content if script_content is not None else (last_gated_content or "")
    )
    result = (final_content, gate_result, last_ai_review, status)
    if include_usage:
        return (*result, usage_records)
    return result


def list_artifacts(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID | None = None,
) -> list[TeacherJudgeScriptArtifactPublic]:
    query = select(TeacherJudgeScriptArtifact).where(
        TeacherJudgeScriptArtifact.teaching_class_id == teaching_class_id
    )
    if session_id is not None:
        query = query.where(TeacherJudgeScriptArtifact.session_id == session_id)
    artifacts = session.exec(
        query.order_by(desc(TeacherJudgeScriptArtifact.created_at))
    ).all()
    node_display_names = _class_machine_display_names(session, teaching_class_id)
    return [_artifact_to_public(artifact, node_display_names) for artifact in artifacts]


def get_artifact(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> TeacherJudgeScriptArtifact:
    artifact = session.get(TeacherJudgeScriptArtifact, artifact_id)
    if artifact is None or artifact.teaching_class_id != teaching_class_id:
        raise HTTPException(status_code=404, detail="Script artifact not found")
    return artifact


def get_artifact_public(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> TeacherJudgeScriptArtifactPublic:
    return _artifact_to_public(
        get_artifact(
            session=session,
            teaching_class_id=teaching_class_id,
            artifact_id=artifact_id,
        ),
        _class_machine_display_names(session, teaching_class_id),
    )


def _next_artifact_version(
    *,
    session: Session,
    artifact: TeacherJudgeScriptArtifact,
) -> int:
    max_version = session.exec(
        select(func.max(TeacherJudgeScriptArtifact.version)).where(
            TeacherJudgeScriptArtifact.teaching_class_id == artifact.teaching_class_id,
            TeacherJudgeScriptArtifact.name == artifact.name,
            TeacherJudgeScriptArtifact.template_key == artifact.template_key,
        )
    ).one()
    return int(max_version or artifact.version) + 1


def _record_script_usage(
    *,
    session: Session,
    user_id: uuid.UUID | None,
    template_key: str,
    usage_records: list[ScriptUsageRecord],
) -> None:
    for record in usage_records:
        record_ai_template_call(
            session=session,
            user_id=user_id,
            call_type=str(record.get("call_type") or ""),
            model_name=settings.VLLM_MODEL_NAME,
            preset=template_key,
            metrics=cast("dict[str, Any]", record.get("metrics") or {}),
        )


async def _build_reviewed_script_for_artifact(
    *,
    rubric_snapshot: dict[str, Any],
    template_key: str,
) -> tuple[
    str,
    GateResult,
    AIReviewResult,
    TeacherJudgeScriptStatus,
    list[ScriptUsageRecord],
]:
    kwargs: dict[str, Any] = {
        "rubric_snapshot": rubric_snapshot,
        "template_key": template_key,
    }
    if "include_usage" in signature(build_reviewed_script).parameters:
        kwargs["include_usage"] = True
    return _build_result(await build_reviewed_script(**kwargs))


async def create_artifact(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    name: str,
    template_key: str,
    rubric_analysis: TeacherJudgeRubricAnalysis,
    created_by: uuid.UUID | None,
    source_file_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    template_commands: list[TeacherJudgeTemplateCommand] | None = None,
) -> TeacherJudgeScriptArtifactPublic:
    artifact_name = name.strip()
    if not artifact_name:
        raise HTTPException(status_code=400, detail=t("artifact.name_blank"))

    if template_commands is None:
        template_commands = get_enabled_template_commands(
            session, template_key, include_cross_template=True
        )
    ensure_script_generation_supported(
        rubric_analysis,
        template_commands,
        require_target_node=bool(
            load_class_machine_nodes(session, teaching_class_id)
        ),
    )

    # Single model_dump reused for both the artifact rubric snapshot and the
    # source file analysis_json (previously dumped twice with equal content).
    # The snapshot takes a shallow top-level copy plus template_key, so
    # analysis_json never gains template_key. Nested items are shared
    # read-only: downstream only shallow-copies the top level and serializes
    # each dict to its own JSON column on commit (no in-place nested mutation).
    analysis_dump = rubric_analysis.model_dump(mode="json")
    _ensure_peer_runtime_supported(analysis_dump)
    target_node_keys = target_node_keys_from_snapshot(analysis_dump)
    if len(target_node_keys) > 1:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_mixed_target_nodes",
                "message": "同一份 Teacher Judge 腳本目前只能對應一個 target_node_key。",
                "target_node_keys": sorted(target_node_keys),
            },
        )
    target_node_key = next(iter(target_node_keys), None)
    if target_node_key and resolve_class_machine_node(
        session, teaching_class_id, target_node_key
    ) is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_target_node_not_in_class",
                "message": "檢查項目的 target_node_key 不屬於目前班級。",
                "target_node_key": target_node_key,
            },
        )
    rubric_base: dict[str, Any] = {
        **analysis_dump,
        **({"target_node_key": target_node_key} if target_node_key else {}),
    }
    if _snapshot_uses_legacy_command_references(analysis_dump):
        rubric_base["template_key"] = template_key
    rubric_snapshot = _with_template_command_catalog(rubric_base, template_commands)
    source_file, source_file_snapshot_json = source_file_snapshot(
        session=session,
        teaching_class_id=teaching_class_id,
        file_id=source_file_id,
    )
    if source_file is not None:
        source_file.analysis_json = analysis_dump
        source_file.updated_at = _now()
        session.add(source_file)
    (
        script_content,
        policy_check,
        ai_review,
        status,
        usage_records,
    ) = await _build_reviewed_script_for_artifact(
        rubric_snapshot=rubric_snapshot,
        template_key=template_key,
    )
    if (
        status == TeacherJudgeScriptStatus.reviewed
        and policy_check.get("approved") is True
        and ai_review.get("approved") is True
    ):
        # Compatibility for older test doubles/callers: a fully passed new
        # workflow is system-approved even if the legacy helper name/status is
        # still returned.
        status = TeacherJudgeScriptStatus.approved

    artifact = TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        session_id=session_id,
        name=artifact_name,
        template_key=template_key,
        rubric_snapshot_json=rubric_snapshot,
        source_file_id=source_file_id,
        source_file_snapshot_json=source_file_snapshot_json,
        script_language=TeacherJudgeScriptLanguage.python,
        script_content=script_content,
        source=TeacherJudgeScriptSource.ai_generated,
        version=1,
        status=status,
        policy_check_result_json=cast("dict[str, Any]", policy_check),
        ai_review_result_json=cast("dict[str, Any]", ai_review),
        created_by=created_by,
        approved_at=_now() if status == TeacherJudgeScriptStatus.approved else None,
        updated_at=_now(),
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    _record_script_usage(
        session=session,
        user_id=created_by,
        template_key=template_key,
        usage_records=usage_records,
    )
    return _artifact_to_public(
        artifact,
        _class_machine_display_names(session, teaching_class_id),
    )


def partition_analysis_by_target_node(
    analysis: TeacherJudgeRubricAnalysis,
    *,
    node_order: list[str] | None = None,
) -> list[tuple[str, TeacherJudgeRubricAnalysis]]:
    """Partition executable rubric items by their canonical executor node."""

    grouped: dict[str, list[Any]] = {}
    for item in analysis.items:
        node_key = str(item.target_node_key or "").strip()
        if not node_key:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "teacher_judge_target_node_required",
                    "message": "每個可執行檢查項目都必須指定 target_node_key。",
                    "item_ids": [item.id],
                },
            )
        grouped.setdefault(node_key, []).append(item)
    ordered_keys = [key for key in node_order or [] if key in grouped]
    ordered_keys.extend(sorted(set(grouped) - set(ordered_keys)))
    partitions: list[tuple[str, TeacherJudgeRubricAnalysis]] = []
    for node_key in ordered_keys:
        items = grouped[node_key]
        partitions.append(
            (
                node_key,
                analysis.model_copy(
                    deep=True,
                    update={
                        "items": items,
                        "total_items": len(items),
                        "checked_count": sum(1 for item in items if item.checked),
                        "auto_count": sum(
                            1 for item in items if item.detectable == "auto"
                        ),
                        "partial_count": sum(
                            1 for item in items if item.detectable == "partial"
                        ),
                        "manual_count": sum(
                            1 for item in items if item.detectable == "manual"
                        ),
                        "pending_review_item_ids": [
                            item_id
                            for item_id in analysis.pending_review_item_ids
                            if item_id in {item.id for item in items}
                        ],
                    },
                ),
            )
        )
    return partitions


def _latest_set_children(
    rows: list[TeacherJudgeScriptArtifact],
    *,
    node_order: dict[str, int] | None = None,
) -> list[TeacherJudgeScriptArtifact]:
    """Return one current child per node, excluding archived history."""

    latest: dict[str, TeacherJudgeScriptArtifact] = {}
    for row in rows:
        if row.status == TeacherJudgeScriptStatus.archived:
            continue
        node_key = str(row.target_node_key or "")
        current = latest.get(node_key)
        if current is None or (row.version, row.created_at) > (
            current.version,
            current.created_at,
        ):
            latest[node_key] = row
    order = node_order or {}
    return sorted(
        latest.values(),
        key=lambda row: (
            order.get(str(row.target_node_key or ""), 10**9),
            str(row.target_node_key or ""),
        ),
    )


def _script_set_to_public(
    rows: list[TeacherJudgeScriptArtifact],
    *,
    node_order: dict[str, int] | None = None,
    node_display_names: dict[str, str] | None = None,
) -> TeacherJudgeScriptSetPublic:
    children = _latest_set_children(rows, node_order=node_order)
    if not children or children[0].artifact_set_id is None:
        raise HTTPException(status_code=404, detail="Script set not found")
    statuses = {child.status.value for child in children}
    status: Literal["approved", "review_failed", "mixed"] = (
        "approved"
        if statuses == {TeacherJudgeScriptStatus.approved.value}
        else "review_failed"
        if statuses == {TeacherJudgeScriptStatus.review_failed.value}
        else "mixed"
    )
    first = children[0]
    return TeacherJudgeScriptSetPublic(
        artifact_set_id=str(first.artifact_set_id),
        teaching_class_id=str(first.teaching_class_id),
        session_id=str(first.session_id) if first.session_id else None,
        source_file_id=str(first.source_file_id) if first.source_file_id else None,
        source_analysis_revision=first.source_analysis_revision,
        status=status,
        children=[_artifact_to_public(child, node_display_names) for child in children],
    )


def get_artifact_set(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_set_id: uuid.UUID,
    session_id: uuid.UUID | None = None,
) -> TeacherJudgeScriptSetPublic:
    statement = select(TeacherJudgeScriptArtifact).where(
        TeacherJudgeScriptArtifact.teaching_class_id == teaching_class_id,
        TeacherJudgeScriptArtifact.artifact_set_id == artifact_set_id,
    )
    if session_id is not None:
        statement = statement.where(TeacherJudgeScriptArtifact.session_id == session_id)
    rows = list(session.exec(statement).all())
    if not rows:
        raise HTTPException(status_code=404, detail="Script set not found")
    nodes = load_class_machine_nodes(session, teaching_class_id)
    return _script_set_to_public(
        rows,
        node_order={node.node_key: node.sort_order for node in nodes},
        node_display_names=_class_machine_display_names(session, teaching_class_id),
    )


def list_artifact_sets(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
) -> list[TeacherJudgeScriptSetPublic]:
    rows = list(
        session.exec(
            select(TeacherJudgeScriptArtifact)
            .where(
                TeacherJudgeScriptArtifact.teaching_class_id == teaching_class_id,
                TeacherJudgeScriptArtifact.session_id == session_id,
                col(TeacherJudgeScriptArtifact.artifact_set_id).is_not(None),
            )
            .order_by(desc(TeacherJudgeScriptArtifact.created_at))
        ).all()
    )
    nodes = load_class_machine_nodes(session, teaching_class_id)
    node_order = {node.node_key: node.sort_order for node in nodes}
    grouped: dict[uuid.UUID, list[TeacherJudgeScriptArtifact]] = {}
    order: list[uuid.UUID] = []
    for row in rows:
        if row.artifact_set_id is None:
            continue
        if row.artifact_set_id not in grouped:
            grouped[row.artifact_set_id] = []
            order.append(row.artifact_set_id)
        grouped[row.artifact_set_id].append(row)
    return [
        _script_set_to_public(grouped[set_id], node_order=node_order)
        for set_id in order
    ]


async def create_artifact_set(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    name: str,
    template_key: str,
    rubric_analysis: TeacherJudgeRubricAnalysis,
    source_analysis_revision: int,
    created_by: uuid.UUID | None,
    source_file_id: uuid.UUID | None,
    artifact_set_id: uuid.UUID | None = None,
) -> TeacherJudgeScriptSetPublic:
    artifact_name = name.strip()
    if not artifact_name:
        raise HTTPException(status_code=400, detail=t("artifact.name_blank"))
    commands = get_enabled_template_commands(
        session, template_key, include_cross_template=True
    )
    ensure_script_generation_supported(
        rubric_analysis,
        commands,
        require_target_node=bool(
            load_class_machine_nodes(session, teaching_class_id)
        ),
    )
    nodes = load_class_machine_nodes(session, teaching_class_id)
    valid_node_keys = {node.node_key for node in nodes}
    node_display_names = {
        node.node_key: (node.name or "").strip() or node.node_key for node in nodes
    }
    partitions = partition_analysis_by_target_node(
        rubric_analysis,
        node_order=[node.node_key for node in nodes],
    )
    invalid_node_keys = {
        key for key, _ in partitions if key not in valid_node_keys
    }
    invalid_node_keys.update(
        peer_node_key
        for peer_node_key in peer_node_keys_from_snapshot(
            rubric_analysis.model_dump(mode="json")
        )
        if peer_node_key not in valid_node_keys
    )
    if invalid_node_keys:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_target_node_not_in_class",
                "message": "檢查項目的 target_node_key 不屬於目前班級。",
                "target_node_keys": sorted(invalid_node_keys),
            },
        )
    machine_contract_issues = {
        item.id: issues
        for item in rubric_analysis.items
        if (issues := rubric_item_machine_issues(item.model_dump(mode="json")))
    }
    if machine_contract_issues:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_machine_contract_invalid",
                "message": "檢查項目的執行節點、觀察節點或 peer token 不一致。",
                "items": machine_contract_issues,
            },
        )
    try:
        canonicalize_check_plan(rubric_analysis)
    except CheckPlanContractError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_check_plan_invalid",
                "message": "完整 Check Plan 未通過契約驗證。",
                "issues": exc.issues,
            },
        ) from exc

    build_results: list[
        tuple[
            str,
            dict[str, Any],
            str,
            dict[str, Any],
            dict[str, Any],
            TeacherJudgeScriptStatus,
            list[ScriptUsageRecord],
        ]
    ] = []
    for node_key, partition in partitions:
        partition_dump = partition.model_dump(mode="json")
        rubric_base: dict[str, Any] = {
            **partition_dump,
            "target_node_key": node_key,
        }
        if _snapshot_uses_legacy_command_references(partition_dump):
            rubric_base["template_key"] = template_key
        rubric_snapshot = _with_template_command_catalog(rubric_base, commands)
        try:
            script_content, policy, review, _compiled_plan = compile_check_plan(
                partition,
                target_node_key=node_key,
            )
            peer_policy = check_peer_runtime_policy(
                script_content,
                rubric_snapshot,
            )
            if peer_policy.get("approved") is not True:
                raise CheckPlanContractError(
                    [
                        {
                            "message": "peer runtime contract validation failed",
                            "issues": peer_policy.get("issues", []),
                        }
                    ]
                )
            policy = {
                **policy,
                "safety_approved": True,
                "safety_issues": [],
                "quality_approved": True,
                "quality_issues": [],
                "peer_runtime_policy": peer_policy,
                "approved": True,
            }
            status = TeacherJudgeScriptStatus.approved
            usage: list[ScriptUsageRecord] = []
        except CheckPlanContractError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "teacher_judge_check_plan_invalid",
                    "message": f"節點 {node_key} 的 Check Plan 未通過契約驗證。",
                    "target_node_key": node_key,
                    "issues": exc.issues,
                },
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "teacher_judge_node_compile_failed",
                    "message": f"節點 {node_key} 的 deterministic 腳本編譯失敗。",
                    "target_node_key": node_key,
                    "item_ids": [item.id for item in partition.items],
                },
            ) from exc
        if (
            status == TeacherJudgeScriptStatus.reviewed
            and policy.get("approved") is True
            and review.get("approved") is True
        ):
            status = TeacherJudgeScriptStatus.approved
        build_results.append(
            (
                node_key,
                rubric_snapshot,
                script_content,
                policy,
                review,
                status,
                usage,
            )
        )

    set_id = artifact_set_id or uuid.uuid4()
    version_by_node: dict[str, int] = {}
    if artifact_set_id is not None:
        previous_rows = list(
            session.exec(
                select(TeacherJudgeScriptArtifact).where(
                    TeacherJudgeScriptArtifact.teaching_class_id
                    == teaching_class_id,
                    TeacherJudgeScriptArtifact.artifact_set_id == set_id,
                )
            ).all()
        )
        if not previous_rows:
            raise HTTPException(status_code=404, detail="Script set not found")
        if any(
            previous.session_id != session_id
            or previous.source_file_id != source_file_id
            for previous in previous_rows
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "teacher_judge_script_set_context_mismatch",
                    "message": "script set 與目前 session 或檢查表來源不一致。",
                },
            )
        for previous in previous_rows:
            node_key = str(previous.target_node_key or "")
            version_by_node[node_key] = max(
                version_by_node.get(node_key, 0), previous.version
            )
            if previous.status != TeacherJudgeScriptStatus.archived:
                previous.status = TeacherJudgeScriptStatus.archived
                previous.updated_at = _now()
                session.add(previous)
    source_file, source_snapshot = source_file_snapshot(
        session=session,
        teaching_class_id=teaching_class_id,
        file_id=source_file_id,
    )
    if source_file is not None:
        source_file.analysis_json = rubric_analysis.model_dump(mode="json")
        source_file.updated_at = _now()
        session.add(source_file)
    artifacts: list[TeacherJudgeScriptArtifact] = []
    for node_key, snapshot, content, policy, review, status, _ in build_results:
        artifact = TeacherJudgeScriptArtifact(
            artifact_set_id=set_id,
            target_node_key=node_key,
            source_analysis_revision=source_analysis_revision,
            teaching_class_id=teaching_class_id,
            session_id=session_id,
            name=f"{artifact_name} · {node_display_names.get(node_key, node_key)}"[:255],
            template_key=template_key,
            rubric_snapshot_json=snapshot,
            source_file_id=source_file_id,
            source_file_snapshot_json=source_snapshot,
            script_language=TeacherJudgeScriptLanguage.python,
            script_content=content,
            source=(
                TeacherJudgeScriptSource.regenerated
                if artifact_set_id is not None
                else TeacherJudgeScriptSource.ai_generated
            ),
            version=version_by_node.get(node_key, 0) + 1,
            status=status,
            policy_check_result_json=policy,
            ai_review_result_json=review,
            created_by=created_by,
            approved_at=_now() if status == TeacherJudgeScriptStatus.approved else None,
            updated_at=_now(),
        )
        session.add(artifact)
        artifacts.append(artifact)
    session.commit()
    for artifact in artifacts:
        session.refresh(artifact)
    for *_, usage in build_results:
        _record_script_usage(
            session=session,
            user_id=created_by,
            template_key=template_key,
            usage_records=usage,
        )
    return _script_set_to_public(
        artifacts,
        node_order={node.node_key: node.sort_order for node in nodes},
        node_display_names=node_display_names,
    )


async def regenerate_artifact(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_id: uuid.UUID,
    rubric_analysis: TeacherJudgeRubricAnalysis | None,
    created_by: uuid.UUID | None,
    template_commands: list[TeacherJudgeTemplateCommand] | None = None,
) -> TeacherJudgeScriptArtifactPublic:
    artifact = get_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact_id,
    )
    if artifact.status == TeacherJudgeScriptStatus.archived:
        raise HTTPException(
            status_code=400, detail=t("artifact.archived_cannot_regenerate")
        )
    template_key = artifact.template_key
    if template_commands is None:
        template_commands = get_enabled_template_commands(
            session, template_key, include_cross_template=True
        )

    automation_analysis = rubric_analysis or TeacherJudgeRubricAnalysis.model_validate(
        artifact.rubric_snapshot_json
    )
    ensure_script_generation_supported(
        automation_analysis,
        template_commands,
        require_target_node=bool(
            load_class_machine_nodes(session, teaching_class_id)
        ),
    )

    # Reuse a single model_dump for snapshot + source analysis_json when a fresh
    # analysis is provided (same sharing rationale as create_artifact above).
    analysis_dump: dict[str, Any] | None = None
    if rubric_analysis is not None:
        analysis_dump = rubric_analysis.model_dump(mode="json")
        rubric_base = dict(analysis_dump)
        if _snapshot_uses_legacy_command_references(analysis_dump):
            rubric_base["template_key"] = template_key
    else:
        rubric_base = artifact.rubric_snapshot_json
    _ensure_peer_runtime_supported(rubric_base)
    target_node_keys = target_node_keys_from_snapshot(rubric_base)
    if len(target_node_keys) > 1:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_mixed_target_nodes",
                "message": "同一份 Teacher Judge 腳本目前只能對應一個 target_node_key。",
                "target_node_keys": sorted(target_node_keys),
            },
        )
    target_node_key = next(iter(target_node_keys), None)
    if target_node_key and resolve_class_machine_node(
        session, teaching_class_id, target_node_key
    ) is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_target_node_not_in_class",
                "message": "檢查項目的 target_node_key 不屬於目前班級。",
                "target_node_key": target_node_key,
            },
        )
    if target_node_key:
        rubric_base["target_node_key"] = target_node_key
    rubric_snapshot = _with_template_command_catalog(
        rubric_base,
        template_commands,
    )
    source_file_id = artifact.source_file_id
    source_file_snapshot_json = artifact.source_file_snapshot_json
    if source_file_id is not None and rubric_analysis is not None:
        source_file, source_file_snapshot_json = source_file_snapshot(
            session=session,
            teaching_class_id=teaching_class_id,
            file_id=source_file_id,
        )
        if source_file is not None:
            source_file.analysis_json = cast("dict[str, Any]", analysis_dump)
            source_file.updated_at = _now()
            session.add(source_file)
    generation_snapshot = dict(rubric_snapshot)
    previous_feedback = _previous_review_feedback(artifact)
    if previous_feedback:
        generation_snapshot["previous_review_feedback"] = previous_feedback
    (
        script_content,
        policy_check,
        ai_review,
        status,
        usage_records,
    ) = await _build_reviewed_script_for_artifact(
        rubric_snapshot=generation_snapshot,
        template_key=template_key,
    )
    if (
        status == TeacherJudgeScriptStatus.reviewed
        and policy_check.get("approved") is True
        and ai_review.get("approved") is True
    ):
        status = TeacherJudgeScriptStatus.approved

    if artifact.status == TeacherJudgeScriptStatus.approved:
        next_version = _next_artifact_version(session=session, artifact=artifact)
        next_artifact = TeacherJudgeScriptArtifact(
            teaching_class_id=teaching_class_id,
            session_id=artifact.session_id,
            name=artifact.name,
            template_key=template_key,
            rubric_snapshot_json=rubric_snapshot,
            source_file_id=source_file_id,
            source_file_snapshot_json=source_file_snapshot_json,
            script_language=TeacherJudgeScriptLanguage.python,
            script_content=script_content,
            source=TeacherJudgeScriptSource.regenerated,
            version=next_version,
            status=status,
            policy_check_result_json=cast("dict[str, Any]", policy_check),
            ai_review_result_json=cast("dict[str, Any]", ai_review),
            created_by=created_by,
            approved_at=_now() if status == TeacherJudgeScriptStatus.approved else None,
            updated_at=_now(),
        )
        session.add(next_artifact)
        session.commit()
        session.refresh(next_artifact)
        _record_script_usage(
            session=session,
            user_id=created_by,
            template_key=template_key,
            usage_records=usage_records,
        )
        return _artifact_to_public(
            next_artifact,
            _class_machine_display_names(session, teaching_class_id),
        )

    artifact.rubric_snapshot_json = rubric_snapshot
    artifact.source_file_snapshot_json = source_file_snapshot_json
    artifact.script_content = script_content
    artifact.source = TeacherJudgeScriptSource.regenerated
    artifact.status = status
    artifact.policy_check_result_json = cast("dict[str, Any]", policy_check)
    artifact.ai_review_result_json = cast("dict[str, Any]", ai_review)
    artifact.approved_by = None
    artifact.approved_at = (
        _now() if status == TeacherJudgeScriptStatus.approved else None
    )
    artifact.updated_at = _now()
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    _record_script_usage(
        session=session,
        user_id=created_by,
        template_key=template_key,
        usage_records=usage_records,
    )
    return _artifact_to_public(
        artifact,
        _class_machine_display_names(session, teaching_class_id),
    )


def approve_artifact(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_id: uuid.UUID,
    approved_by: uuid.UUID | None,
) -> TeacherJudgeScriptArtifactPublic:
    artifact = get_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact_id,
    )
    if artifact.status != TeacherJudgeScriptStatus.reviewed:
        raise HTTPException(status_code=400, detail=t("artifact.not_reviewed"))
    if artifact.policy_check_result_json.get("approved") is not True:
        raise HTTPException(
            status_code=400, detail=t("artifact.policy_check_failed")
        )
    if artifact.ai_review_result_json.get("approved") is not True:
        raise HTTPException(status_code=400, detail=t("artifact.ai_review_failed"))

    artifact.status = TeacherJudgeScriptStatus.approved
    artifact.approved_by = approved_by
    artifact.approved_at = _now()
    artifact.updated_at = _now()
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return _artifact_to_public(
        artifact,
        _class_machine_display_names(session, teaching_class_id),
    )


def archive_artifact(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> TeacherJudgeScriptArtifactPublic:
    artifact = get_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact_id,
    )
    artifact.status = TeacherJudgeScriptStatus.archived
    artifact.updated_at = _now()
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return _artifact_to_public(
        artifact,
        _class_machine_display_names(session, teaching_class_id),
    )


def rename_artifact(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_id: uuid.UUID,
    name: str,
) -> TeacherJudgeScriptArtifactPublic:
    artifact = get_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact_id,
    )
    new_name = name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail=t("artifact.name_blank"))
    if len(new_name) > 255:
        raise HTTPException(status_code=400, detail=t("artifact.name_blank"))
    artifact.name = new_name
    artifact.updated_at = _now()
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return _artifact_to_public(
        artifact,
        _class_machine_display_names(session, teaching_class_id),
    )


def delete_artifact(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> None:
    artifact = get_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact_id,
    )
    session.delete(artifact)
    session.commit()
