"""Split from tests/test_rubric_template_commands.py: python runtime & package checks.

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


@pytest.mark.parametrize(
    ("raw_timeout", "expected"),
    [
        ("5", 5),
        (" 5 ", 5),
        ("5.0", 5),
        (5.0, 5),
        (300, 300),
    ],
)
def test_validate_python_entrypoint_coerces_numeric_timeout(
    raw_timeout: object, expected: int
) -> None:
    items = validate_check_steps(
        "python",
        [
            {
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.run_entrypoint",
                        "parameters": {
                            "cwd": "/home/student/project",
                            "argv": ["python3", "main.py"],
                            "timeout_seconds": raw_timeout,
                            "success_criteria": "exit code 為 0",
                        },
                    }
                ]
            }
        ],
        [_python_entrypoint_command()],
    )

    parameters = items[0]["check_steps"][0]["parameters"]
    assert parameters["timeout_seconds"] == expected
    assert isinstance(parameters["timeout_seconds"], int)


@pytest.mark.parametrize(
    "raw_timeout",
    ["abc", "5.5", 5.5, 0, 301, True, None],
)
def test_validate_python_entrypoint_fills_platform_timeout_when_uncoercible(
    raw_timeout: object,
) -> None:
    items = validate_check_steps(
        "python",
        [
            {
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.run_entrypoint",
                        "parameters": {
                            "cwd": "/home/student/project",
                            "argv": ["python3", "main.py"],
                            "timeout_seconds": raw_timeout,
                            "success_criteria": "exit code 為 0",
                        },
                    }
                ]
            }
        ],
        [_python_entrypoint_command()],
    )

    parameters = items[0]["check_steps"][0]["parameters"]
    assert parameters["timeout_seconds"] == DEFAULT_SYSTEM_COMMAND_TIMEOUT_SECONDS
    assert isinstance(parameters["timeout_seconds"], int)


def test_normalize_rubric_item_accepts_string_timeout_for_python_entrypoint() -> None:
    normalized = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "main.py 執行檢查",
                "detectable": "auto",
                "detection_method": "執行 main.py 並檢查輸出",
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.run_entrypoint",
                        "parameters": {
                            "cwd": "/home/student/project",
                            "argv": ["python3", "main.py"],
                            "timeout_seconds": "5",
                            "success_criteria": "exit code 為 0",
                        },
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[_python_entrypoint_command()],
    )

    assert normalized[0].detectable == "auto"
    assert normalized[0].missing_information == []
    assert normalized[0].check_steps[0].parameters["timeout_seconds"] == 5


def test_normalize_does_not_infer_python_version_intent_from_text() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "檢查 Python 版本",
                "detectable": "manual",
                "check_steps": [],
            }
        ],
        template_key="linux",
        template_commands=[_python_version_command(), GENERAL_COMMAND],
    )

    assert items[0].detectable == "manual"
    assert items[0].check_steps == []


def test_normalize_preserves_structured_python_judgement_mode() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "確認學生環境中安裝的 Python 版本",
                "detectable": "auto",
                "judgement_mode": "ai",
                "detection_method": "執行 Python 版本查詢。",
                "missing_information": [],
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.version",
                        "parameters": {},
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[_python_version_command()],
    )

    assert items[0].detectable == "auto"
    assert items[0].judgement_mode == "ai"
    assert items[0].missing_information == []


def test_normalize_python_version_with_expected_answer_keeps_ai_judgement() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "確認 Python 版本至少為 3.11",
                "detectable": "auto",
                "judgement_mode": "ai",
                "detection_method": "取得版本後與 3.11 比較。",
                "missing_information": [],
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.version",
                        "parameters": {},
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[_python_version_command()],
    )

    assert items[0].detectable == "auto"
    assert items[0].judgement_mode == "ai"


@pytest.mark.asyncio
async def test_python_version_lookup_proposal_preserves_model_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
                "create_checklist_item",
                {
                    "title": "確認學生環境中安裝的 Python 版本",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "執行 Python 版本查詢。",
                    "missing_information": [],
                    "check_steps": [
                        {
                            "template_key": "python",
                            "command_key": "python.version",
                            "parameters": {},
                        }
                    ],
                },
            ),
            reply_message("已建立 Python 版本檢查提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[
            SimpleNamespace(
                role="user",
                content="確認學生環境中安裝的 Python 版本",
            )
        ],
        rubric_context=json.dumps({"items": []}),
        template_key="python",
        template_commands=[_python_version_command()],
    )

    assert len(calls) == 2
    assert proposal is not None
    assert proposal[0]["detectable"] == "auto"
    assert proposal[0]["judgement_mode"] == "ai"
    assert proposal[0]["check_steps"][0]["command_key"] == "python.version"


@pytest.mark.asyncio
async def test_python_version_requirement_forms_proposal_when_model_marks_it_manual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 Python 版本",
                    "detectable": "auto",
                    "judgement_mode": "teacher",
                    "detection_method": "查詢 Python 版本。",
                    "missing_information": [],
                    "check_steps": [
                        {
                            "template_key": "python",
                            "command_key": "python.version",
                            "parameters": {},
                        }
                    ],
                },
            ),
            reply_message(
                "我已把「檢查 Python 版本」整理成提案。請先查看提案內容，確認後再套用。",
                "ready",
            ),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="我想看學生 Python 的版本號")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[_python_version_command(), GENERAL_COMMAND],
    )

    assert len(calls) == 2
    assert (
        reply
        == "我已把「檢查 Python 版本」整理成提案。請先查看提案內容，確認後再套用。"
    )
    assert "腳本取證" not in reply
    assert "AI 或導師判斷" not in reply
    assert proposal is not None
    assert proposal[0]["detectable"] == "auto"
    assert proposal[0]["judgement_mode"] == "teacher"
    assert proposal[0]["check_steps"] == [
        {
            "template_key": "python",
            "command_key": "python.version",
            "command_label": "Python 版本",
            "parameters": {},
        }
    ]


def test_normalize_rejects_unknown_python_package_command() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-torch",
                "title": "檢查 torch 套件安裝情況",
                "detectable": "auto",
                "judgement_mode": "ai",
                "detection_method": "查詢套件安裝狀態。",
                "missing_information": [],
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.package_status",
                        "parameters": {"package": "torch"},
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[GENERAL_COMMAND],
    )

    assert items[0].detectable == "manual"
    assert items[0].check_steps == []


def test_normalize_does_not_replace_python_package_version_requirement() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-torch-version",
                "title": "確認 torch 套件版本至少 2.0",
                "detectable": "auto",
                "judgement_mode": "ai",
                "detection_method": "查詢套件版本。",
                "missing_information": [],
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.package_status",
                        "parameters": {"package": "torch"},
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[GENERAL_COMMAND],
    )

    assert items[0].detectable == "manual"
    assert items[0].check_steps == []


@pytest.mark.asyncio
async def test_python_package_status_forms_proposal_instead_of_system_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 torch 套件安裝情況",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "查詢套件安裝狀態。",
                    "missing_information": [],
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["python3", "-m", "pip", "show", "torch"],
                                "timeout_seconds": 30,
                                "success_criteria": "exit code 為 0",
                            },
                        }
                    ],
                },
            ),
            reply_message(
                "「檢查 torch 套件安裝情況」已整理成提案。"
                "系統會確認套件是否已安裝；請先查看提案，確認後再套用。",
                "ready",
            ),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="檢查 torch 套件安裝情況")],
        rubric_context=json.dumps({"items": []}),
        template_key="python",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 2
    assert "整理成提案" in reply
    assert "重新產生" not in reply
    assert "管理員" not in reply
    assert proposal is not None
    assert proposal[0]["check_steps"][0]["command_key"] == "system.run_command"
    assert proposal[0]["check_steps"][0]["parameters"]["argv"] == [
        "python3",
        "-m",
        "pip",
        "show",
        "torch",
    ]


def test_normalize_preserves_objectively_verifiable_main_py_checkpoint() -> None:
    command = _python_entrypoint_command()
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "title": "main.py 執行結果",
                "detectable": "auto",
                "detection_method": (
                    "以 exit code 與 stderr 判斷錯誤，並精確比對 stdout 是否為整數 20。"
                ),
                "fallback": "請人工執行。",
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.run_entrypoint",
                        "parameters": {
                            "cwd": "/home/student/project",
                            "argv": ["python3", "main.py"],
                            "timeout_seconds": 30,
                            "success_criteria": "exit code 為 0 且 stdout 等於 20",
                        },
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[command],
    )

    assert items[0].detectable == "auto"
    assert items[0].check_steps[0].command_key == "python.run_entrypoint"
    assert "stdout" in (items[0].detection_method or "")
    assert items[0].fallback is None


def test_normalize_marks_missing_python_parameters_as_missing_information() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "title": "main.py 執行結果",
                "detectable": "auto",
                "detection_method": "執行 main.py 並檢查 stdout",
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.run_entrypoint",
                        "parameters": {
                            "argv": ["python3", "main.py"],
                            "timeout_seconds": 30,
                            "success_criteria": "stdout 等於 20",
                        },
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[_python_entrypoint_command()],
    )

    assert items[0].detectable == "partial"
    assert items[0].missing_information == ["main.py 所在的工作目錄"]


def test_normalize_python_code_quality_stays_manual() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [{"title": "評估 main.py 的架構品質", "detectable": "manual"}],
        template_key="python",
        template_commands=[_python_entrypoint_command()],
    )

    assert items[0].detectable == "manual"
    assert items[0].check_steps == []
