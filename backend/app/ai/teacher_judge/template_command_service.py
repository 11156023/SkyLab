"""Template command catalog helpers for Teacher Judge rubric analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, select

from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand

SUPPORTED_TEMPLATE_KEYS = {"linux", "python", "n8n", "postgresql"}
DEFAULT_SYSTEM_COMMAND_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300
_RETIRED_CHECK_STEP_PARAMETER_KEYS = frozenset({"success_criteria"})


def sanitize_check_step_parameters(value: Any) -> Any:
    """Drop retired rubric fields while keeping unknown future parameters readable."""
    if not isinstance(value, dict):
        return value
    return {
        key: parameter
        for key, parameter in value.items()
        if key not in _RETIRED_CHECK_STEP_PARAMETER_KEYS
    }


def coerce_timeout_seconds(value: Any) -> int | None:
    """Best-effort coercion of LLM-provided timeout values to a valid int.

    Accepts int (bool excluded), integral floats, and numeric strings such as
    "5" or "5.0"; returns None for anything that is not a whole number
    within 1-300.
    """
    if isinstance(value, bool):
        return None
    coerced: int
    if isinstance(value, int):
        coerced = value
    elif isinstance(value, float) and value.is_integer():
        coerced = int(value)
    elif isinstance(value, str):
        text = value.strip()
        try:
            coerced = int(text)
        except ValueError:
            try:
                as_float = float(text)
            except ValueError:
                return None
            if not as_float.is_integer():
                return None
            coerced = int(as_float)
    else:
        return None
    return coerced if 1 <= coerced <= MAX_TIMEOUT_SECONDS else None


@dataclass(frozen=True, slots=True)
class CheckStepIssue:
    owner: str
    item_id: str
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class CheckStepValidationResult:
    items: list[dict[str, Any]]
    issues: list[CheckStepIssue]

GENERAL_COMMAND = TeacherJudgeTemplateCommand(
    template_key="linux",
    command_key="system.run_command",
    command_label="通用受控指令",
    category="inspection",
    command_template="argv + cwd + timeout",
    description=(
        "平台已登錄的通用唯讀診斷能力。依檢查目的選擇 Linux 或 Windows "
        "常見 CLI，以單一 argv 在指定工作目錄執行，並收集 exit code、stdout、"
        "stderr。禁止修改系統狀態、高風險或破壞性操作；能以低權限取得資訊時"
        "不得要求提權。平台會套用安全逾時，老師不需指定技術參數或新增權限。"
    ),
    risk_level="executes_command",
    requires_confirmation=True,
)


def get_enabled_template_commands(
    session: Session,
    template_key: str,
    *,
    include_cross_template: bool = False,
) -> list[TeacherJudgeTemplateCommand]:
    """Return enabled commands, optionally including controlled cross-template capabilities."""
    statement = (
        select(TeacherJudgeTemplateCommand)
        .where(TeacherJudgeTemplateCommand.enabled == True)  # noqa: E712
        .order_by(
            TeacherJudgeTemplateCommand.template_key,
            TeacherJudgeTemplateCommand.category,
            TeacherJudgeTemplateCommand.command_key,
        )
    )
    if not include_cross_template:
        statement = statement.where(
            TeacherJudgeTemplateCommand.template_key == template_key
        )
    commands = list(session.exec(statement).all())
    if include_cross_template:
        if not any(
            command.command_key == GENERAL_COMMAND.command_key for command in commands
        ):
            commands.append(GENERAL_COMMAND)
        commands.sort(
            key=lambda command: (
                command.template_key != template_key,
                command.template_key,
                command.category,
                command.command_key,
            )
        )
    return commands


def format_template_commands_for_prompt(
    commands: list[TeacherJudgeTemplateCommand],
) -> str:
    """Format enabled commands for LLM prompt injection."""
    if not commands:
        return "目前沒有 template command catalog；請不要產生 check_steps。"

    lines = []
    for command in commands:
        lines.append(
            "\n".join(
                [
                    f"- template_key: {command.template_key}",
                    f"  command_key: {command.command_key}",
                    f"  command_label: {command.command_label}",
                    f"  category: {command.category}",
                    f"  description: {command.description}",
                    f"  risk_level: {command.risk_level}",
                    f"  requires_confirmation: {command.requires_confirmation}",
                    *(
                        [
                            "  parameters_schema: argv 是非空字串陣列；cwd 可選；"
                            "timeout_seconds 由平台補齊"
                        ]
                        if command.command_key == "system.run_command"
                        else []
                    ),
                ]
            )
        )
    return "\n".join(lines)


def validate_check_steps(
    template_key: str,
    items: list[dict[str, Any]],
    commands: list[TeacherJudgeTemplateCommand],
) -> list[dict[str, Any]]:
    """
    Resolve enabled commands without making ``template_key`` a proposal gate.

    This helper accepts item-shaped dictionaries so tests and callers can validate
    raw LLM payloads without needing to instantiate Pydantic schemas first. The
    template key is a preferred environment hint; an omitted or mismatched hint is
    repaired when the command key identifies exactly one enabled capability.
    """
    return validate_check_steps_with_issues(template_key, items, commands).items


def validate_check_steps_with_issues(
    template_key: str,
    items: list[dict[str, Any]],
    commands: list[TeacherJudgeTemplateCommand],
) -> CheckStepValidationResult:
    """Normalize command references and retain why a model candidate was rejected."""
    valid_commands = {
        (command.template_key, command.command_key): command for command in commands
    }
    normalized_items: list[dict[str, Any]] = []
    issues: list[CheckStepIssue] = []
    for item in items:
        next_item = dict(item)
        item_id = str(item.get("id") or "")
        valid_steps: list[dict[str, Any]] = []
        raw_steps = item.get("check_steps")
        if isinstance(raw_steps, list):
            for raw_step in raw_steps:
                if not isinstance(raw_step, dict):
                    issues.append(
                        CheckStepIssue("model", item_id, "invalid_step", "檢查步驟不是物件")
                    )
                    continue
                step_template_key = str(raw_step.get("template_key") or template_key).strip()
                command_key = str(raw_step.get("command_key") or "").strip()
                command = valid_commands.get((step_template_key, command_key))
                if command is None and command_key:
                    matching_commands = [
                        candidate
                        for (_, candidate_key), candidate in valid_commands.items()
                        if candidate_key == command_key
                    ]
                    if len(matching_commands) == 1:
                        command = matching_commands[0]
                if command is None:
                    issues.append(
                        CheckStepIssue(
                            "model",
                            item_id,
                            "unknown_command",
                            f"未啟用的 command_key: {command_key or '(empty)'}",
                        )
                    )
                    continue
                step: dict[str, Any] = {
                    "template_key": command.template_key,
                    "command_key": command.command_key,
                    "command_label": command.command_label,
                }
                raw_parameters = raw_step.get("parameters")
                if isinstance(raw_parameters, dict) or (
                    command.command_key == "system.run_command"
                ):
                    parameters = (
                        dict(raw_parameters) if isinstance(raw_parameters, dict) else {}
                    )
                    parameters = sanitize_check_step_parameters(parameters)
                    timeout = parameters.get("timeout_seconds")
                    if command.command_key == "system.run_command" and (
                        not isinstance(timeout, int)
                        or isinstance(timeout, bool)
                        or not 1 <= timeout <= 300
                    ):
                        parameters["timeout_seconds"] = (
                            DEFAULT_SYSTEM_COMMAND_TIMEOUT_SECONDS
                        )
                    elif command.command_key != "system.run_command":
                        coerced = coerce_timeout_seconds(timeout)
                        if coerced is not None:
                            parameters["timeout_seconds"] = coerced
                        elif command.command_key == "python.run_entrypoint":
                            parameters["timeout_seconds"] = (
                                DEFAULT_SYSTEM_COMMAND_TIMEOUT_SECONDS
                            )
                    step["parameters"] = parameters
                if step not in valid_steps:
                    valid_steps.append(step)
        next_item["check_steps"] = valid_steps
        normalized_items.append(next_item)
    return CheckStepValidationResult(items=normalized_items, issues=issues)


__all__ = [
    "DEFAULT_SYSTEM_COMMAND_TIMEOUT_SECONDS",
    "GENERAL_COMMAND",
    "SUPPORTED_TEMPLATE_KEYS",
    "coerce_timeout_seconds",
    "format_template_commands_for_prompt",
    "get_enabled_template_commands",
    "sanitize_check_step_parameters",
    "validate_check_steps",
]
