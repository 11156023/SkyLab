"""Deterministic script-generation readiness checks for Teacher Judge rubrics."""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import HTTPException

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

    return missing


def _item_missing_information(
    item: TeacherJudgeRubricItem,
    *,
    valid_commands: set[tuple[str, str]],
) -> list[str]:
    missing = list(item.missing_information)
    if not item.detection_method or not item.detection_method.strip():
        missing.append("檢測方式與判定條件")
    if not item.check_steps:
        missing.append("平台支援的檢查步驟")
    for step in item.check_steps:
        if (step.template_key, step.command_key) not in valid_commands:
            missing.append(f"有效的檢查能力：{step.command_key}")
        missing.extend(missing_step_information(step))
    return list(dict.fromkeys(value for value in missing if value))


def get_script_generation_blockers(
    analysis: TeacherJudgeRubricAnalysis,
    commands: list[TeacherJudgeTemplateCommand],
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
            blockers.append(
                {
                    "item_id": item.id,
                    "title": item.title,
                    "status": "manual",
                    "missing_information": [],
                    "reason_code": "automatic_detection_unsupported",
                }
            )
            continue

        if item.detectable == "partial":
            missing = list(item.missing_information)
            if not missing:
                missing = [
                    item.fallback.strip()
                    if item.fallback and item.fallback.strip()
                    else "完整的服務名稱、程式位置、連接埠或成功條件"
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

        missing = _item_missing_information(item, valid_commands=valid_commands)
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

    return blockers


def ensure_script_generation_supported(
    analysis: TeacherJudgeRubricAnalysis,
    commands: list[TeacherJudgeTemplateCommand],
) -> None:
    blockers = get_script_generation_blockers(analysis, commands)
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
