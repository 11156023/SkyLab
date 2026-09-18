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


def _valid_timeout(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 300


def missing_step_information(
    step: TeacherJudgeRubricCheckStep,
    *,
    judgement_mode: str = "ai",
) -> list[str]:
    """Return required structured inputs missing from a parameterized command step."""
    parameters = step.parameters
    missing: list[str] = []

    if step.command_key == "python.run_entrypoint":
        if not isinstance(parameters.get("cwd"), str) or not str(
            parameters.get("cwd")
        ).strip():
            missing.append("main.py 所在的工作目錄")
        if not _non_empty_argv(parameters.get("argv")):
            missing.append("實際 Python 命令與參數")
        if not _valid_timeout(parameters.get("timeout_seconds")):
            missing.append("1 至 300 秒的逾時限制")
        if judgement_mode == "ai" and (
            not isinstance(parameters.get("success_criteria"), str)
            or not str(parameters.get("success_criteria")).strip()
        ):
            missing.append("客觀成功條件")
    elif step.command_key == "system.run_command":
        if not _non_empty_argv(parameters.get("argv")):
            missing.append("要檢查的檔案、服務或記錄範圍")
        if judgement_mode == "ai" and (
            not isinstance(parameters.get("success_criteria"), str)
            or not str(parameters.get("success_criteria")).strip()
        ):
            missing.append("客觀成功條件")

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
        missing.extend(
            missing_step_information(step, judgement_mode=item.judgement_mode)
        )
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
