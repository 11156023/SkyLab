"""Teacher Judge managed script artifact lifecycle service."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, cast

from fastapi import HTTPException
from sqlmodel import Session, desc, func, select

from app.ai.teacher_judge._types import (
    AIReviewResult,
    CheckResult,
    FixHint,
    GateResult,
    PreviousReviewFeedback,
    TemplateCommandSnapshot,
)
from app.ai.teacher_judge.config import settings
from app.ai.teacher_judge.file_service import source_file_snapshot
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeScriptArtifactPublic,
)
from app.ai.teacher_judge.script_generation_contract import (
    RESULT_SCHEMA_VERSION,
    SCRIPT_GENERATION_CONTRACT_PROMPT,
    SCRIPT_GENERATION_MAX_ATTEMPTS,
)
from app.ai.teacher_judge.script_policy import check_script_policy
from app.ai.teacher_judge.script_quality_validator import check_script_quality
from app.ai.teacher_judge.service import _call_vllm
from app.ai.teacher_judge.template_command_service import get_enabled_template_commands
from app.ai.utils import apply_thinking_control
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptLanguage,
    TeacherJudgeScriptSource,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand

logger = logging.getLogger(__name__)


SCRIPT_GENERATION_SYSTEM_PROMPT = f"""
# 角色
你是 Teacher Judge 的受管 Python 資料收集腳本產生器。

# 任務
根據 rubric snapshot 產生一份只讀、可重複執行的 Python managed data collection script。
腳本負責收集同學 VM/LXC 內的服務、port、process、localhost HTTP 等資料，整理成 JSON，供後續解讀與評分使用。

# 硬性規則
- 只能輸出 JSON，不要 markdown。
- JSON 欄位必須是 {{"script_content": "..."}}。
- script_content 必須是完整 Python 程式。
- 腳本只能收集本機服務狀態、port、process、HTTP localhost endpoint。
- 腳本不得刪除、修改、修復、安裝、重啟、停用或重設任何環境。
- 腳本不得讀取 .env、.ssh、private key 或把資料送到外部網路。
- 若需要執行指令，只能使用 subprocess.run([...], timeout=秒數, capture_output=True, text=True, check=False)。
- subprocess.run 第一個參數必須是 argv list，不得使用字串指令，不得使用 shell=True。
- 不得使用 os.system、os.popen、subprocess.Popen 或任何未設定 timeout 的指令執行方式。
- 若 template command 含 pipe、redirect、grep 等 shell 寫法，請改用 Python 程式解析 stdout，不要原樣 shell=True 執行。
- HTTP request 只允許 GET/HEAD localhost/127.0.0.1/::1，必須設定 timeout。
- 腳本最後必須 print 單一 JSON，schema_version 固定為 {RESULT_SCHEMA_VERSION}，並使用 json.dumps(..., ensure_ascii=False)。
- 輸出 JSON 的 metadata 必須包含 timestamp 與 platform。
- 優先根據 rubric item 的 check_steps.command_key 對應 template_commands 產生收集項目。
- 若 previous_review_feedback 有內容，代表上一輪腳本審查未通過；必須修正其中所有 policy、quality validator、AI reviewer 問題。
- 腳本頂層必須定義 `errors: list[str] = []`。每個收集項目的例外處理區塊（try/except）必須使用 `errors.append(f"{{check_id}}: {{錯誤說明}}")` 記錄錯誤原因，讓老師看到執行時的收集品質。所有收集成功時 errors 輸出空陣列。

# 簡潔程式碼骨架
- 產生單檔 Python script；不要建立 class、plugin 架構、retry framework 或多層抽象。
- helper 只保留這 5 個：`truncate_output`、`redact_sensitive_text`、`command_available`、`run_command`、`record_check`。
- `run_command()` 只負責執行 argv list 並回傳 `stdout`、`stderr`、`returncode`；若捕捉例外，回傳 `returncode=None` 與錯誤文字，不要在 helper 內吞掉資訊。
- 每個收集項目使用同一個簡潔模式：
  1. 先決定 `check_id`
  2. 檢查工具是否存在；缺工具時 `record_check(..., "unknown", ...)`
  3. 執行 `run_command()`
  4. 若 `returncode is None`，必須 `errors.append(f"{{check_id}}: {{錯誤說明}}")` 並輸出 `unknown`
  5. 只有明確驗證條件成立時才輸出 `pass`
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
      "status": "pass | fail | warning | unknown | skipped",
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
你是 Teacher Judge managed data collection script 的安全審查員。
只審查腳本，不執行腳本。請依 policy 判斷它是否只做 read-only inspection。

## 安全審查
若腳本可能刪除、修改、修復、安裝、重啟、讀取敏感檔案或對外傳資料，approved 必須是 false。

## 錯誤記錄完整性
- 檢查腳本有 subprocess.run / HTTP 請求等外部呼叫時，是否有對應的 try/except 並在 except 中 call errors.append()。
- 若腳本有例外處理但 errors 始終為空陣列，應列為 issues。
- 檢查 bare except / except Exception 後是否有將錯誤記錄到 errors。

只輸出 JSON：
{{
  "approved": true,
  "risk_level": "low | medium | high",
  "issues": [],
  "suggested_fix": null
}}
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
- 對 `bare except / except Exception 後未將錯誤記錄到 errors`，必須在同一個 except 區塊加入 `errors.append(...)`，並確保對應 `record_check` 狀態不是 `pass`

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
    if not isinstance(payload, dict):
        return {
            "approved": False,
            "risk_level": "high",
            "issues": ["AI reviewer 回傳格式不正確"],
            "suggested_fix": "請重新生成腳本",
        }

    approved = payload.get("approved") is True
    risk_level_raw = str(payload.get("risk_level") or ("low" if approved else "high"))
    if risk_level_raw not in {"low", "medium", "high"}:
        risk_level_raw = "high"
    risk_level = cast("Literal['low', 'medium', 'high']", risk_level_raw)

    issues = payload.get("issues")
    if not isinstance(issues, list):
        issues = []

    return {
        "approved": approved,
        "risk_level": risk_level,
        "issues": [str(issue) for issue in issues],
        "suggested_fix": payload.get("suggested_fix"),
    }


def _artifact_to_public(
    artifact: TeacherJudgeScriptArtifact,
) -> TeacherJudgeScriptArtifactPublic:
    return TeacherJudgeScriptArtifactPublic(
        id=str(artifact.id),
        group_id=str(artifact.group_id),
        name=artifact.name,
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


def _with_template_command_catalog(
    rubric_snapshot: dict[str, Any],
    template_commands: list[TeacherJudgeTemplateCommand] | None,
) -> dict[str, Any]:
    snapshot = dict(rubric_snapshot)
    command_catalog = _template_commands_snapshot(template_commands)
    if command_catalog:
        snapshot["template_commands"] = command_catalog
    return snapshot


def _previous_review_feedback(
    artifact: TeacherJudgeScriptArtifact,
) -> PreviousReviewFeedback | None:
    policy_check = artifact.policy_check_result_json or {}
    ai_review = artifact.ai_review_result_json or {}
    safety_issues = policy_check.get("safety_issues")
    policy_issues = (
        safety_issues
        if isinstance(safety_issues, list)
        else policy_check.get("issues")
    )
    quality_issues = policy_check.get("quality_issues")
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
    has_failed_review = (
        feedback["policy_approved"] is False
        or feedback["quality_approved"] is False
        or feedback["ai_review_approved"] is False
        or bool(feedback["policy_issues"])
        or bool(feedback["quality_issues"])
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
    if policy_check.get("approved") is True and ai_review.get("approved") is True:
        return TeacherJudgeScriptStatus.reviewed
    return TeacherJudgeScriptStatus.review_failed


def _merge_gate_results(
    safety_check: CheckResult,
    quality_check: CheckResult,
) -> GateResult:
    safety_issues = safety_check.get("issues")
    quality_issues = quality_check.get("issues")
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
        safety_check.get("approved") is True
        and quality_check.get("approved") is True
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
        "fix_hints": fix_hints,
    }


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
    if hint_type == "add_errors_append_in_except":
        return (
            "只替換指定 except 區塊；加入 errors.append(f\"<check_id>: ...\")，"
            "並確保該錯誤路徑輸出的 record_check status 是 unknown 或 fail，不可為 pass。"
        )
    if hint_type == "ai_reviewer_feedback":
        return "依 AI reviewer issues 做最小行區間替換，不要重寫整份腳本。"
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
            len(str(replacement).splitlines())
            if isinstance(replacement, str)
            else 0
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
) -> str:
    if not settings.VLLM_MODEL_NAME:
        raise HTTPException(status_code=503, detail="VLLM_MODEL_NAME 未設定。")

    payload = apply_thinking_control(
        {
            "model": settings.VLLM_MODEL_NAME,
            "messages": [
                {"role": "system", "content": SCRIPT_GENERATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "template_key": template_key,
                            "rubric_snapshot": rubric_snapshot,
                            "template_commands": rubric_snapshot.get(
                                "template_commands", []
                            ),
                            "previous_review_feedback": rubric_snapshot.get(
                                "previous_review_feedback"
                            ),
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
    content, _metrics = await _call_vllm(payload, timeout=float(settings.VLLM_TIMEOUT))

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="AI 產生腳本格式不是 JSON。") from exc

    script_content = str(parsed.get("script_content") or "").strip()
    if not script_content:
        raise HTTPException(status_code=502, detail="AI 未產生 script_content。")
    return script_content


async def review_script_with_ai(
    *,
    script_content: str,
    rubric_snapshot: dict[str, Any],
) -> AIReviewResult:
    if not settings.VLLM_MODEL_NAME:
        raise HTTPException(status_code=503, detail="VLLM_MODEL_NAME 未設定。")

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
    content, _metrics = await _call_vllm(payload, timeout=float(settings.VLLM_TIMEOUT))

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {}
    return _normalize_ai_review(parsed)


def _apply_line_replacements(
    script_content: str,
    raw_replacements: Any,
) -> str:
    if not isinstance(raw_replacements, list) or not raw_replacements:
        raise HTTPException(status_code=502, detail="AI 未產生 line_replacements。")

    lines = script_content.split("\n")
    replacements: list[tuple[int, int, str]] = []
    for raw in raw_replacements:
        if not isinstance(raw, dict):
            raise HTTPException(status_code=502, detail="AI 修正腳本格式不正確。")
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
            raise HTTPException(status_code=502, detail="AI 修正腳本行號格式不正確。")
        if start_line < 1 or end_line < start_line or end_line > len(lines):
            raise HTTPException(status_code=502, detail="AI 修正腳本行號超出範圍。")
        replacements.append((start_line, end_line, replacement))

    sorted_replacements = sorted(replacements, key=lambda item: item[0])
    previous_end = 0
    for start_line, end_line, _replacement in sorted_replacements:
        if start_line <= previous_end:
            raise HTTPException(status_code=502, detail="AI 修正腳本行號區間重疊。")
        previous_end = end_line

    patched_lines = list(lines)
    for start_line, end_line, replacement in reversed(sorted_replacements):
        replacement_lines = replacement.split("\n") if replacement else []
        patched_lines[start_line - 1 : end_line] = replacement_lines

    result = "\n".join(patched_lines).strip()
    if not result:
        raise HTTPException(status_code=502, detail="AI 未產生修正後腳本。")
    return result


async def fix_script_content(
    *,
    script_content: str,
    fix_hints: list[FixHint],
) -> str:
    if not settings.VLLM_MODEL_NAME:
        raise HTTPException(status_code=503, detail="VLLM_MODEL_NAME 未設定。")

    lines = script_content.split("\n")
    numbered = "\n".join(f"{i+1:04d}|{line}" for i, line in enumerate(lines))
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
    content, _metrics = await _call_vllm(payload, timeout=float(settings.VLLM_TIMEOUT))

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="AI 修正腳本格式不是 JSON。") from exc

    logger.info(
        "Teacher Judge script patch response: replacements=%s summary=%s",
        _line_replacement_log_summary(parsed.get("line_replacements")),
        parsed.get("changes_summary"),
    )
    return _apply_line_replacements(
        script_content,
        parsed.get("line_replacements"),
    )


async def build_reviewed_script(
    *,
    rubric_snapshot: dict[str, Any],
    template_key: str,
) -> tuple[str, GateResult, AIReviewResult, TeacherJudgeScriptStatus]:
    attempt_snapshot = dict(rubric_snapshot)

    # Phase 1: initial generation
    script_content = await generate_script_content(
        rubric_snapshot=attempt_snapshot,
        template_key=template_key,
    )
    attempt_records: list[dict[str, object]] = []

    # Phase 2: fix loop (policy + quality only, no AI reviewer yet)
    for attempt in range(1, SCRIPT_GENERATION_MAX_ATTEMPTS + 1):
        safety_check = check_script_policy(script_content)
        quality_check = check_script_quality(script_content)
        gate_result = _merge_gate_results(safety_check, quality_check)

        if gate_result["approved"]:
            break

        fix_hints = (
            safety_check.get("fix_hints", [])
            + quality_check.get("fix_hints", [])
        )
        attempt_record = _gate_attempt_record(
            attempt=attempt,
            safety_check=safety_check,
            quality_check=quality_check,
            fix_hints=fix_hints,
        )
        attempt_records.append(attempt_record)
        logger.warning(
            "Teacher Judge script gate failed on attempt %s/%s: safety_issues=%s quality_issues=%s fix_hints=%s",
            attempt,
            SCRIPT_GENERATION_MAX_ATTEMPTS,
            attempt_record["safety_issues"],
            attempt_record["quality_issues"],
            _fix_hint_log_summary(fix_hints),
        )

        if attempt >= SCRIPT_GENERATION_MAX_ATTEMPTS:
            gate_result["review_attempts"] = attempt_records
            last_ai_review = await review_script_with_ai(
                script_content=script_content,
                rubric_snapshot=attempt_snapshot,
            )
            status = _resolve_status(gate_result, last_ai_review)
            return script_content, gate_result, last_ai_review, status

        if not fix_hints:
            # no structured hints available — fallback to full re-generate
            attempt_snapshot = dict(rubric_snapshot)
            attempt_snapshot["previous_review_feedback"] = {
                "attempt": attempt,
                "policy_approved": gate_result.get("safety_approved"),
                "policy_issues": gate_result.get("safety_issues", []),
                "quality_approved": gate_result.get("quality_approved"),
                "quality_issues": gate_result.get("quality_issues", []),
            }
            script_content = await generate_script_content(
                rubric_snapshot=attempt_snapshot,
                template_key=template_key,
            )
            continue

        # incremental fix via LLM
        try:
            script_content = await fix_script_content(
                script_content=script_content,
                fix_hints=fix_hints,
            )
        except HTTPException as exc:
            logger.warning(
                "Teacher Judge script patch failed; falling back to regenerate: %s",
                exc.detail,
            )
            # fix failed — fallback to full re-generate
            attempt_snapshot = dict(rubric_snapshot)
            attempt_snapshot["previous_review_feedback"] = {
                "attempt": attempt,
                "policy_approved": gate_result.get("safety_approved"),
                "policy_issues": gate_result.get("safety_issues", []),
                "quality_approved": gate_result.get("quality_approved"),
                "quality_issues": gate_result.get("quality_issues", []),
            }
            script_content = await generate_script_content(
                rubric_snapshot=attempt_snapshot,
                template_key=template_key,
            )

    # Phase 3: final AI reviewer (only once)
    final_safety = check_script_policy(script_content)
    final_quality = check_script_quality(script_content)
    final_gate = _merge_gate_results(final_safety, final_quality)
    final_gate["review_attempts"] = attempt_records
    last_ai_review = await review_script_with_ai(
        script_content=script_content,
        rubric_snapshot=rubric_snapshot,
    )

    # if AI reviewer failed, try one fix with AI feedback
    if last_ai_review.get("approved") is not True and last_ai_review.get("issues"):
        ai_fix_hints: list[FixHint] = [
            cast("FixHint", {
                "type": "ai_reviewer_feedback",
                "issues": last_ai_review.get("issues", []),
                "suggested_fix": last_ai_review.get("suggested_fix"),
            })
        ]
        try:
            script_content = await fix_script_content(
                script_content=script_content,
                fix_hints=ai_fix_hints,
            )
            final_safety = check_script_policy(script_content)
            final_quality = check_script_quality(script_content)
            final_gate = _merge_gate_results(final_safety, final_quality)
            final_gate["review_attempts"] = attempt_records
            if final_gate["approved"]:
                last_ai_review = await review_script_with_ai(
                    script_content=script_content,
                    rubric_snapshot=rubric_snapshot,
                )
        except HTTPException:
            pass

    status = _resolve_status(final_gate, last_ai_review)
    return script_content, final_gate, last_ai_review, status


def list_artifacts(
    *,
    session: Session,
    group_id: uuid.UUID,
) -> list[TeacherJudgeScriptArtifactPublic]:
    artifacts = session.exec(
        select(TeacherJudgeScriptArtifact)
        .where(TeacherJudgeScriptArtifact.group_id == group_id)
        .order_by(desc(TeacherJudgeScriptArtifact.created_at))
    ).all()
    return [_artifact_to_public(artifact) for artifact in artifacts]


def get_artifact(
    *,
    session: Session,
    group_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> TeacherJudgeScriptArtifact:
    artifact = session.get(TeacherJudgeScriptArtifact, artifact_id)
    if artifact is None or artifact.group_id != group_id:
        raise HTTPException(status_code=404, detail="Script artifact not found")
    return artifact


def get_artifact_public(
    *,
    session: Session,
    group_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> TeacherJudgeScriptArtifactPublic:
    return _artifact_to_public(
        get_artifact(session=session, group_id=group_id, artifact_id=artifact_id)
    )


def _next_artifact_version(
    *,
    session: Session,
    artifact: TeacherJudgeScriptArtifact,
) -> int:
    max_version = session.exec(
        select(func.max(TeacherJudgeScriptArtifact.version)).where(
            TeacherJudgeScriptArtifact.group_id == artifact.group_id,
            TeacherJudgeScriptArtifact.name == artifact.name,
            TeacherJudgeScriptArtifact.template_key == artifact.template_key,
        )
    ).one()
    return int(max_version or artifact.version) + 1


async def create_artifact(
    *,
    session: Session,
    group_id: uuid.UUID,
    name: str,
    template_key: str,
    rubric_analysis: TeacherJudgeRubricAnalysis,
    created_by: uuid.UUID | None,
    source_file_id: uuid.UUID | None = None,
    template_commands: list[TeacherJudgeTemplateCommand] | None = None,
) -> TeacherJudgeScriptArtifactPublic:
    artifact_name = name.strip()
    if not artifact_name:
        raise HTTPException(status_code=400, detail="腳本名稱不可空白。")

    if template_commands is None:
        template_commands = get_enabled_template_commands(session, template_key)

    rubric_snapshot = _with_template_command_catalog(
        _rubric_snapshot(rubric_analysis, template_key),
        template_commands,
    )
    source_file, source_file_snapshot_json = source_file_snapshot(
        session=session,
        group_id=group_id,
        file_id=source_file_id,
    )
    if source_file is not None:
        source_file.analysis_json = rubric_analysis.model_dump(mode="json")
        source_file.updated_at = _now()
        session.add(source_file)
    script_content, policy_check, ai_review, status = await build_reviewed_script(
        rubric_snapshot=rubric_snapshot,
        template_key=template_key,
    )

    artifact = TeacherJudgeScriptArtifact(
        group_id=group_id,
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
        updated_at=_now(),
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return _artifact_to_public(artifact)


async def regenerate_artifact(
    *,
    session: Session,
    group_id: uuid.UUID,
    artifact_id: uuid.UUID,
    rubric_analysis: TeacherJudgeRubricAnalysis | None,
    created_by: uuid.UUID | None,
    template_commands: list[TeacherJudgeTemplateCommand] | None = None,
) -> TeacherJudgeScriptArtifactPublic:
    artifact = get_artifact(
        session=session,
        group_id=group_id,
        artifact_id=artifact_id,
    )
    if artifact.status == TeacherJudgeScriptStatus.archived:
        raise HTTPException(status_code=400, detail="已封存的腳本不能重新生成。")
    template_key = artifact.template_key
    if template_commands is None:
        template_commands = get_enabled_template_commands(session, template_key)

    rubric_snapshot = _with_template_command_catalog(
        _rubric_snapshot(rubric_analysis, template_key)
        if rubric_analysis is not None
        else artifact.rubric_snapshot_json,
        template_commands,
    )
    source_file_id = artifact.source_file_id
    source_file_snapshot_json = artifact.source_file_snapshot_json
    if source_file_id is not None and rubric_analysis is not None:
        source_file, source_file_snapshot_json = source_file_snapshot(
            session=session,
            group_id=group_id,
            file_id=source_file_id,
        )
        if source_file is not None:
            source_file.analysis_json = rubric_analysis.model_dump(mode="json")
            source_file.updated_at = _now()
            session.add(source_file)
    generation_snapshot = dict(rubric_snapshot)
    previous_feedback = _previous_review_feedback(artifact)
    if previous_feedback:
        generation_snapshot["previous_review_feedback"] = previous_feedback
    script_content, policy_check, ai_review, status = await build_reviewed_script(
        rubric_snapshot=generation_snapshot,
        template_key=template_key,
    )

    if artifact.status == TeacherJudgeScriptStatus.approved:
        next_version = _next_artifact_version(session=session, artifact=artifact)
        next_artifact = TeacherJudgeScriptArtifact(
            group_id=group_id,
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
            updated_at=_now(),
        )
        session.add(next_artifact)
        session.commit()
        session.refresh(next_artifact)
        return _artifact_to_public(next_artifact)

    artifact.rubric_snapshot_json = rubric_snapshot
    artifact.source_file_snapshot_json = source_file_snapshot_json
    artifact.script_content = script_content
    artifact.source = TeacherJudgeScriptSource.regenerated
    artifact.status = status
    artifact.policy_check_result_json = cast("dict[str, Any]", policy_check)
    artifact.ai_review_result_json = cast("dict[str, Any]", ai_review)
    artifact.updated_at = _now()
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return _artifact_to_public(artifact)


def approve_artifact(
    *,
    session: Session,
    group_id: uuid.UUID,
    artifact_id: uuid.UUID,
    approved_by: uuid.UUID | None,
) -> TeacherJudgeScriptArtifactPublic:
    artifact = get_artifact(
        session=session,
        group_id=group_id,
        artifact_id=artifact_id,
    )
    if artifact.status != TeacherJudgeScriptStatus.reviewed:
        raise HTTPException(status_code=400, detail="只有審查通過的腳本可以核准。")
    if artifact.policy_check_result_json.get("approved") is not True:
        raise HTTPException(status_code=400, detail="腳本未通過安全/品質檢查。")
    if artifact.ai_review_result_json.get("approved") is not True:
        raise HTTPException(status_code=400, detail="腳本未通過 AI reviewer。")

    artifact.status = TeacherJudgeScriptStatus.approved
    artifact.approved_by = approved_by
    artifact.approved_at = _now()
    artifact.updated_at = _now()
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return _artifact_to_public(artifact)


def archive_artifact(
    *,
    session: Session,
    group_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> TeacherJudgeScriptArtifactPublic:
    artifact = get_artifact(
        session=session,
        group_id=group_id,
        artifact_id=artifact_id,
    )
    artifact.status = TeacherJudgeScriptStatus.archived
    artifact.updated_at = _now()
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return _artifact_to_public(artifact)


def delete_artifact(
    *,
    session: Session,
    group_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> None:
    artifact = get_artifact(
        session=session,
        group_id=group_id,
        artifact_id=artifact_id,
    )
    session.delete(artifact)
    session.commit()
