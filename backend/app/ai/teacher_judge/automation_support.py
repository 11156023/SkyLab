"""Deterministic script-generation readiness checks for Teacher Judge rubrics."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from fastapi import HTTPException

from app.ai.teacher_judge.machine_context import rubric_item_machine_issues
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricCheckStep,
    TeacherJudgeRubricItem,
)
from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand


class AutomationSupportBlocker(TypedDict):
    item_id: str | None
    title: str
    status: str
    missing_information: list[str]
    reason_code: str
    detail: NotRequired[str]


_TEACHER_RESULT_GAP_MARKERS = (
    "成功條件",
    "客觀成功條件",
    "判定條件",
    "預期答案",
    "預期結果",
    "預期內容",
    "通過方式",
)


def _execution_missing_information(item: TeacherJudgeRubricItem) -> list[str]:
    """Keep collection gaps, but never block teacher judgement on an answer key."""
    missing = list(item.missing_information)
    if item.judgement_mode != "teacher":
        return missing
    return [
        value
        for value in missing
        if not any(marker.casefold() in value.casefold() for marker in _TEACHER_RESULT_GAP_MARKERS)
    ]


def _non_empty_argv(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(part, str) and bool(part.strip()) for part in value)
    )


def _gap_text(
    parameters: dict[str, Any],
    key: str,
    missing_text: str,
    invalid_text: str,
) -> str:
    """Distinguish an absent field from one present with an unusable value."""
    return invalid_text if parameters.get(key) is not None else missing_text


def missing_step_information(
    step: TeacherJudgeRubricCheckStep,
) -> list[str]:
    """Return required structured inputs missing from a parameterized command step."""
    if step.collector is not None:
        collector = step.collector.model_dump(mode="json")
        collector_type = str(collector.get("type") or "")
        typed_missing: list[str] = []
        if collector_type == "command":
            if not _non_empty_argv(collector.get("argv")):
                typed_missing.append("collector.argv 必須是非空的字串陣列")
            timeout = collector.get("timeout_seconds")
            if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 300:
                typed_missing.append("collector.timeout_seconds 必須是 1-300 的整數")
        elif collector_type in {"file_text", "file_stat"}:
            if not isinstance(collector.get("path"), str) or not collector["path"].strip():
                typed_missing.append("collector.path")
        elif collector_type == "localhost_http":
            if not isinstance(collector.get("url"), str) or not collector["url"].strip():
                typed_missing.append("collector.url")
        elif collector_type == "peer_ping":
            timeout = collector.get("timeout_seconds")
            if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 60:
                typed_missing.append("collector.timeout_seconds 必須是 1-60 的整數")
        return typed_missing
    parameters = step.parameters
    missing: list[str] = []

    if step.command_key == "python.run_entrypoint":
        cwd = parameters.get("cwd")
        if not isinstance(cwd, str) or not cwd.strip():
            missing.append(
                _gap_text(
                    parameters,
                    "cwd",
                    "main.py 所在的工作目錄",
                    "main.py 所在的工作目錄（cwd 必須是非空字串）",
                )
            )
        if not _non_empty_argv(parameters.get("argv")):
            missing.append(
                _gap_text(
                    parameters,
                    "argv",
                    "實際 Python 命令與參數",
                    "實際 Python 命令與參數（argv 必須是非空字串 list）",
                )
            )
    elif step.command_key == "system.run_command":
        if not _non_empty_argv(parameters.get("argv")):
            missing.append(
                _gap_text(
                    parameters,
                    "argv",
                    "要檢查的檔案、服務或記錄範圍",
                    "要檢查的檔案、服務或記錄範圍（argv 必須是非空字串 list）",
                )
            )

    if not step.command_key:
        if not _non_empty_argv(parameters.get("argv")):
            missing.append(
                _gap_text(
                    parameters,
                    "argv",
                    "瑕佹鏌ョ殑妾旀銆佹湇鍕欐垨瑷橀寗绡勫湇",
                    "argv 必須是非空的字串陣列",
                )
            )
        timeout = parameters.get("timeout_seconds")
        if not (
            isinstance(timeout, int)
            and not isinstance(timeout, bool)
            and 1 <= timeout <= 300
        ):
            missing.append(
                _gap_text(
                    parameters,
                    "timeout_seconds",
                    "鍛戒护閫炬檪绉掓暩",
                    "timeout_seconds 必須是 1-300 的整數",
                )
            )

    return missing


def _item_missing_information(
    item: TeacherJudgeRubricItem,
    *,
    valid_commands: set[tuple[str, str]],
    require_target_node: bool = False,
) -> list[str]:
    missing = _execution_missing_information(item)
    if not item.detection_method or not item.detection_method.strip():
        missing.append("檢測方式與結果解讀")
    if not item.check_steps:
        missing.append("平台支援的檢查步驟")
    if (
        require_target_node
        and item.detectable.strip().lower() == "auto"
        and not item.target_node_key
    ):
        missing.append("target_node_key")
    missing.extend(rubric_item_machine_issues(item.model_dump(mode="json")))
    for step in item.check_steps:
        if step.collector is None and (step.template_key or step.command_key) and (
            step.template_key,
            step.command_key,
        ) not in valid_commands:
            missing.append(f"有效的檢查能力：{step.command_key}")
        missing.extend(missing_step_information(step))
    return list(dict.fromkeys(value for value in missing if value))


def get_script_generation_blockers(
    analysis: TeacherJudgeRubricAnalysis,
    commands: list[TeacherJudgeTemplateCommand],
    *,
    require_target_node: bool = False,
    require_typed_plan: bool = False,
) -> list[AutomationSupportBlocker]:
    """Explain why the current rubric cannot safely enter script generation."""
    blockers: list[AutomationSupportBlocker] = []
    valid_commands = {
        (command.template_key, command.command_key) for command in commands
    }

    if not analysis.items:
        blockers.append(
            {
                "item_id": None,
                "title": "尚未新增檢查項目",
                "status": "missing_info",
                "missing_information": ["至少一個具備可執行取證步驟的檢查項目"],
                "reason_code": "automatic_detection_items_missing",
            }
        )

    if analysis.detectability_needs_review:
        blockers.append(
            {
                "item_id": None,
                "title": "腳本取證支援待更新",
                "status": "missing_info",
                "missing_information": ["重新確認異動項目的腳本取證方式"],
                "reason_code": "automation_support_needs_review",
            }
        )

    for item in analysis.items:
        if item.detectable == "manual":
            detail = (item.fallback or "").strip()
            if not detail:
                if item.judgement_mode == "teacher" and item.check_steps:
                    detail = (
                        "目前的檢查方式尚未形成通過安全驗證的取證步驟；"
                        "若要建立腳本，請補充可讀取的檔案、服務或命令結果"
                    )
                else:
                    detail = (
                        "目前沒有可安全執行的唯讀取證方式；"
                        "請改由導師查看，或補充可讀取的檔案、服務或命令結果"
                    )
            blockers.append(
                {
                    "item_id": item.id,
                    "title": item.title,
                    "status": "manual",
                    "missing_information": [],
                    "reason_code": "automatic_detection_unsupported",
                    "detail": detail,
                }
            )
            continue

        if item.detectable == "partial":
            missing = list(item.missing_information)
            if not missing:
                missing = [
                    item.fallback.strip()
                    if item.fallback and item.fallback.strip()
                    else "完整的服務名稱、程式位置、連接埠或取證範圍"
                ]
            blockers.append(
                {
                    "item_id": item.id,
                    "title": item.title,
                    "status": "missing_info",
                    "missing_information": missing,
                    "reason_code": "automatic_detection_information_missing",
                }
            )
            continue

        missing = _item_missing_information(
            item,
            valid_commands=valid_commands,
            require_target_node=require_target_node,
        )
        if missing:
            blockers.append(
                {
                    "item_id": item.id,
                    "title": item.title,
                    "status": "missing_info",
                    "missing_information": missing,
                    "reason_code": "automatic_detection_information_missing",
                }
            )

    if require_typed_plan and not blockers:
        from app.ai.teacher_judge.deterministic_compiler import (
            CheckPlanContractError,
            canonicalize_check_plan,
        )

        try:
            canonicalize_check_plan(
                analysis,
                require_target_node=require_target_node,
            )
        except CheckPlanContractError as exc:
            for issue in exc.issues:
                item_id = str(issue.get("item_id") or "").strip() or None
                step_id = str(issue.get("step_id") or "").strip()
                detail = str(issue.get("message") or "Check Plan 驗證失敗").strip()
                blockers.append(
                    {
                        "item_id": item_id,
                        "title": (
                            f"{item_id} / {step_id}"
                            if item_id and step_id
                            else item_id or "檢查計畫契約"
                        ),
                        "status": "analysis_error",
                        "missing_information": [],
                        "reason_code": "check_plan_contract_invalid",
                        "detail": detail,
                    }
                )

    return blockers


def ensure_script_generation_supported(
    analysis: TeacherJudgeRubricAnalysis,
    commands: list[TeacherJudgeTemplateCommand],
    *,
    require_target_node: bool = False,
    require_typed_plan: bool = False,
) -> None:
    blockers = get_script_generation_blockers(
        analysis,
        commands,
        require_target_node=require_target_node,
        require_typed_plan=require_typed_plan,
    )
    if blockers:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_script_not_ready",
                "message": "所有檢查項目都具備可執行的取證步驟後，才能製作檢查腳本。",
                "items": blockers,
            },
        )


__all__ = [
    "AutomationSupportBlocker",
    "ensure_script_generation_supported",
    "get_script_generation_blockers",
    "missing_step_information",
]
