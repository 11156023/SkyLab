"""Split from tests/test_rubric_template_commands.py: template-command catalog & check-step validation.

Shared fixtures live in tests.ai.teacher_judge.helpers.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from app.ai.teacher_judge import service as teacher_judge_service
from app.ai.teacher_judge.schemas import TeacherJudgeRubricItem
from app.ai.teacher_judge.template_command_service import (
    DEFAULT_SYSTEM_COMMAND_TIMEOUT_SECONDS,
    GENERAL_COMMAND,
    format_template_commands_for_prompt,
    get_enabled_template_commands,
    validate_check_steps,
    validate_check_steps_with_issues,
)
from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand
from tests.ai.teacher_judge.helpers import (
    make_session,
    make_teacher_judge_file,
    patch_teacher_judge_vllm_settings,
    reply_message,
    requirement_focus,
    scripted_vllm,
    tool_call_message,
)


def _session_with_commands() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(
        TeacherJudgeTemplateCommand(
            template_key="n8n",
            command_key="n8n.port_check",
            command_label="n8n 連接埠檢查",
            category="port",
            command_template="ss -lntp | grep ':5678'",
            description="檢查 n8n 預設 5678 連接埠是否正在監聽。",
        )
    )
    session.add(
        TeacherJudgeTemplateCommand(
            template_key="python",
            command_key="python.version",
            command_label="Python 版本",
            category="runtime",
            command_template="python3 --version",
            description="查看 Python 直譯器版本。",
            enabled=False,
        )
    )
    session.commit()
    return session


def _python_entrypoint_command() -> TeacherJudgeTemplateCommand:
    return TeacherJudgeTemplateCommand(
        template_key="python",
        command_key="python.run_entrypoint",
        command_label="執行 Python 程式入口",
        category="execution",
        command_template="python3 main.py",
        description="受控執行 Python 程式並收集結果。",
        risk_level="executes_code",
        requires_confirmation=True,
    )


def _python_version_command() -> TeacherJudgeTemplateCommand:
    return TeacherJudgeTemplateCommand(
        template_key="python",
        command_key="python.version",
        command_label="Python 版本",
        category="runtime",
        command_template="python3 --version",
        description="查看 Python 直譯器版本。",
        risk_level="read_only",
        requires_confirmation=True,
    )


def test_get_enabled_template_commands_filters_template_and_enabled() -> None:
    session = _session_with_commands()

    commands = get_enabled_template_commands(session, "n8n")

    assert [command.command_key for command in commands] == ["n8n.port_check"]


def test_get_enabled_template_commands_can_include_cross_template_catalog() -> None:
    session = _session_with_commands()
    session.add(
        TeacherJudgeTemplateCommand(
            template_key="python",
            command_key="python.run_entrypoint",
            command_label="執行 Python 程式入口",
            category="execution",
            command_template="python3 main.py",
            description="受控執行 Python 程式入口。",
        )
    )
    session.commit()

    commands = get_enabled_template_commands(
        session, "n8n", include_cross_template=True
    )

    assert [(command.template_key, command.command_key) for command in commands] == [
        ("n8n", "n8n.port_check"),
        ("linux", "system.run_command"),
        ("python", "python.run_entrypoint"),
    ]


def test_cross_template_catalog_includes_generic_controlled_command() -> None:
    session = _session_with_commands()

    commands = get_enabled_template_commands(
        session, "python", include_cross_template=True
    )
    general = next(
        command for command in commands if command.command_key == "system.run_command"
    )

    assert general.template_key == "linux"
    assert general.requires_confirmation is True
    assert "平台已登錄的通用唯讀診斷能力" in general.description
    assert "依檢查目的選擇 Linux 或 Windows" in general.description
    assert "指定工作目錄" in general.description
    assert "stdout" in general.description
    assert "stderr" in general.description
    assert "禁止修改系統狀態" in general.description
    assert "能以低權限取得資訊時不得要求提權" in general.description
    assert "平台會套用安全逾時" in general.description
    assert "不需指定技術參數或新增權限" in general.description
    assert "cat" not in general.description


def test_validate_check_steps_allows_catalog_backed_cross_template_step() -> None:
    python_command = _python_entrypoint_command()

    items = validate_check_steps(
        "n8n",
        [
            {
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.run_entrypoint",
                    },
                    {
                        "template_key": "postgresql",
                        "command_key": "python.run_entrypoint",
                    },
                ]
            }
        ],
        [python_command],
    )

    assert items[0]["check_steps"] == [
        {
            "template_key": "python",
            "command_key": "python.run_entrypoint",
            "command_label": "執行 Python 程式入口",
        }
    ]


def test_validate_generic_command_applies_platform_timeout_default() -> None:
    items = validate_check_steps(
        "linux",
        [
            {
                "check_steps": [
                    {
                        "template_key": "linux",
                        "command_key": "system.run_command",
                        "parameters": {
                            "cwd": r"C:\Users\陳洋\Desktop\Campus-Cloud",
                            "argv": ["cat", ".env"],
                            "success_criteria": "exit code 為 0",
                        },
                    }
                ]
            }
        ],
        [GENERAL_COMMAND],
    )

    parameters = items[0]["check_steps"][0]["parameters"]
    assert parameters == {
        "cwd": r"C:\Users\陳洋\Desktop\Campus-Cloud",
        "argv": ["cat", ".env"],
        "timeout_seconds": 30,
    }


def test_validate_check_steps_reports_model_owned_unknown_command() -> None:
    result = validate_check_steps_with_issues(
        "linux",
        [
            {
                "id": "item-cpu",
                "check_steps": [
                    {"template_key": "linux", "command_key": "invented.cpu"}
                ],
            }
        ],
        [GENERAL_COMMAND],
    )

    assert result.items[0]["check_steps"] == []
    assert [(issue.owner, issue.item_id, issue.code) for issue in result.issues] == [
        ("model", "item-cpu", "unknown_command")
    ]


def test_normalize_repairs_flattened_system_command_shape() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "check-main-log-success",
                "title": "檢查日誌關鍵字",
                "detectable": "auto",
                "judgement_mode": "ai",
                "detection_method": "content_search",
                "missing_information": [],
                "check_steps": [
                    {
                        "command_key": "system.run_command",
                        "argv": [
                            "grep",
                            "successful",
                            "/home/student/main.log",
                        ],
                        "cwd": "/home/student",
                        "success_criteria": "exit_code == 0",
                    }
                ],
            }
        ],
        template_key="n8n",
        template_commands=[GENERAL_COMMAND],
    )

    assert items[0].detectable == "auto"
    assert items[0].missing_information == []
    assert items[0].check_steps[0].template_key == "linux"
    assert items[0].check_steps[0].command_key == "system.run_command"
    assert items[0].check_steps[0].parameters == {
        "argv": ["grep", "successful", "/home/student/main.log"],
        "cwd": "/home/student",
        "timeout_seconds": 30,
    }


def test_normalize_converges_uncatalogued_readonly_argv_to_general_command() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-jq",
                "title": "檢查 jq 工具版本",
                "detectable": "auto",
                "judgement_mode": "ai",
                "detection_method": "執行版本查詢。",
                "missing_information": [],
                "check_steps": [
                    {
                        "template_key": "linux",
                        "command_key": "jq.version",
                        "parameters": {
                            "argv": ["jq", "--version"],
                            "success_criteria": "exit code 為 0",
                        },
                    }
                ],
            }
        ],
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert items[0].detectable == "auto"
    assert items[0].missing_information == []
    assert items[0].check_steps[0].template_key == "linux"
    assert items[0].check_steps[0].command_key == "system.run_command"
    assert items[0].check_steps[0].parameters == {
        "argv": ["jq", "--version"],
        "timeout_seconds": 30,
    }


def test_validate_check_steps_fills_omitted_template_from_unique_command() -> None:
    python_command = _python_version_command()

    items = validate_check_steps(
        "n8n",
        [{"check_steps": [{"command_key": "python.version"}]}],
        [python_command],
    )

    assert items[0]["check_steps"] == [
        {
            "template_key": "python",
            "command_key": "python.version",
            "command_label": "Python 版本",
        }
    ]


def test_prompt_formatter_handles_empty_catalog() -> None:
    assert "沒有 template command catalog" in format_template_commands_for_prompt([])


def test_prompt_formatter_does_not_expose_raw_shell_command() -> None:
    formatted = format_template_commands_for_prompt(
        [
            TeacherJudgeTemplateCommand(
                template_key="n8n",
                command_key="n8n.http_check",
                command_label="n8n HTTP 檢查",
                category="service",
                command_template="curl -I --max-time 5 http://127.0.0.1:5678",
                description="檢查本機 n8n Web 服務是否有 HTTP 回應。",
            )
        ]
    )

    assert "n8n.http_check" in formatted
    assert "template_key: n8n" in formatted
    assert "curl -I" not in formatted
