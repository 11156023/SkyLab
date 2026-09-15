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


def _patch_teacher_judge_vllm_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        teacher_judge_service,
        "settings",
        SimpleNamespace(
            VLLM_MODEL_NAME="test-model",
            VLLM_ENABLE_THINKING=False,
            VLLM_TIMEOUT=60,
            VLLM_MAX_TOKENS=4096,
            VLLM_CHAT_MAX_TOKENS=4096,
            VLLM_CHAT_TEMPERATURE=0.2,
            VLLM_TOP_P=1.0,
            VLLM_TOP_K=20,
            VLLM_REPETITION_PENALTY=1.0,
            VLLM_CHAT_MAX_TOOL_ROUNDS=6,
        ),
    )


def _tool_call_message(name: str, arguments: dict[str, object]) -> dict[str, object]:
    """Assistant message that invokes one checklist proposal tool."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        ],
    }


def _reply_message(reply: str, status: str, focus: dict[str, object] | None = None) -> str:
    """Final assistant JSON reply without any tool calls."""
    payload: dict[str, object] = {"reply": reply, "proposal_status": status}
    if focus is not None:
        payload["conversation_focus"] = focus
    return json.dumps(payload, ensure_ascii=False)


def _requirement_focus(status: str, *, key: str, title: str) -> dict[str, object]:
    return {
        "turn_kind": "requirement",
        "requirements": [
            {
                "focus_key": key,
                "status": status,
                "known_information": [title],
                "missing_information": [],
                "target_item_id": None,
            }
        ],
    }


def _scripted_vllm(responses: list[object]):
    """Build a fake `_call_vllm_message` serving a scripted response sequence."""
    calls: list[dict[str, object]] = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        return responses.pop(0), {}

    return calls, fake_call_vllm


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
        "success_criteria": "exit code 為 0",
        "timeout_seconds": 30,
    }


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
        "success_criteria": "exit_code == 0",
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
        "success_criteria": "exit code 為 0",
        "timeout_seconds": 30,
    }


@pytest.mark.asyncio
async def test_uncatalogued_tool_with_complete_argv_still_forms_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 jq 工具版本",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "執行版本查詢。",
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
                },
            ),
            _reply_message(
                "「檢查 jq 工具版本」已整理成提案。"
                "系統會確認指令可以執行；請先查看提案，確認後再套用。",
                "ready",
            ),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="檢查 jq 工具版本")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 2
    assert "整理成提案" in reply
    assert proposal is not None
    assert proposal[0]["check_steps"][0]["command_key"] == "system.run_command"
    assert proposal[0]["check_steps"][0]["parameters"]["argv"] == [
        "jq",
        "--version",
    ]


@pytest.mark.asyncio
async def test_tool_loop_requests_drop_json_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 jq 工具版本",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "執行版本查詢。",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["jq", "--version"],
                                "success_criteria": "exit code 為 0",
                            },
                        }
                    ],
                },
            ),
            _reply_message("已整理成提案。請確認後套用。", "ready"),
        ]
    )
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="檢查 jq 工具版本")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
        rubric_available=True,
    )

    assert calls
    assert all("response_format" not in payload for payload in calls)
    assert "整理成提案" in reply
    assert proposal is not None
    assert proposal[0]["operation"] == "add"


@pytest.mark.asyncio
async def test_no_rubric_chat_keeps_json_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [_reply_message("請先選擇檢查表來源。", "none")]
    )
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="檢查 jq 工具版本")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
        rubric_available=False,
    )

    assert calls
    assert all(
        payload.get("response_format") == {"type": "json_object"} for payload in calls
    )
    assert proposal is None


@pytest.mark.asyncio
async def test_partial_success_reply_summarizes_rejected_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 jq 版本",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "執行版本查詢。",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["jq", "--version"],
                                "success_criteria": "exit code 為 0",
                            },
                        }
                    ],
                },
            ),
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 Web 服務",
                    "detectable": "partial",
                    "judgement_mode": "ai",
                    "missing_information": ["Port 號"],
                },
            ),
            _reply_message("兩個提案都已建立完成。", "ready"),
        ]
    )
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    result = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="檢查 jq 版本與 Web 服務")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
        rubric_available=True,
    )

    assert result.proposal is not None
    assert len(result.proposal) == 1
    assert result.proposal[0]["title"] == "檢查 jq 版本"
    assert "已建立完成" in result.reply
    assert "檢查 Web 服務" in result.reply
    assert "還缺少" in result.reply
    rejected = [
        entry
        for entry in result.tool_calls or []
        if entry.get("status") == "rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0]["title"] == "檢查 Web 服務"
    assert rejected[0]["reason"]


@pytest.mark.asyncio
async def test_partial_failure_note_skips_titles_staged_after_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 Web 服務",
                    "detectable": "partial",
                    "judgement_mode": "ai",
                    "missing_information": ["Port 號"],
                },
            ),
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 Web 服務",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "檢查連接埠。",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["ss", "-lntp"],
                                "success_criteria": "exit code 為 0",
                            },
                        }
                    ],
                },
            ),
            _reply_message("提案已建立完成。", "ready"),
        ]
    )
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    result = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="檢查 Web 服務")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
        rubric_available=True,
    )

    assert result.proposal is not None
    assert len(result.proposal) == 1
    assert "另外" not in result.reply


@pytest.mark.asyncio
async def test_missing_success_criteria_no_longer_rejects_auto_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "必要套件安裝檢查",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "查詢套件安裝狀態。",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["dpkg", "-l", "jq"],
                                "timeout_seconds": 30,
                            },
                        }
                    ],
                },
            ),
            _reply_message("提案已建立完成。", "ready"),
        ]
    )
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    result = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="確認必要套件已安裝")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
        rubric_available=True,
    )

    assert len(calls) == 2
    assert result.proposal is not None
    assert len(result.proposal) == 1
    assert result.proposal[0]["detectable"] == "auto"
    assert "success_criteria" not in result.proposal[0]["check_steps"][0]["parameters"]
    tool_outcomes = result.tool_calls or []
    rejected = [entry for entry in tool_outcomes if entry.get("status") == "rejected"]
    staged = [entry for entry in tool_outcomes if entry.get("status") == "staged"]
    assert len(rejected) == 0
    assert len(staged) == 1


@pytest.mark.asyncio
async def test_teacher_information_gap_rejection_still_defers_to_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 Web 服務",
                    "detectable": "partial",
                    "judgement_mode": "ai",
                    "missing_information": ["服務名稱與 Port 號"],
                },
            ),
            _reply_message("還需要服務名稱與 Port 號。", "needs_information"),
        ]
    )
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    result = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="檢查 Web 服務")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
        rubric_available=True,
    )

    assert len(calls) == 2
    assert result.proposal is None
    rejected = [
        entry
        for entry in result.tool_calls or []
        if entry.get("status") == "rejected"
    ]
    assert len(rejected) == 1
    reason = str(rejected[0]["reason"])
    assert "目前無法形成可套用的提案" in reason
    assert "請改在 reply 中說明缺少的內容" in reason
    assert "可由你自行補齊" not in reason


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


def test_backend_does_not_classify_issue_owner_from_missing_info_prose() -> None:
    normalized = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "讀取環境設定",
                "detectable": "partial",
                "detection_method": "以 exit code 判定檔案是否可讀",
                "missing_information": [
                    "唯讀命令與參數",
                    "1 至 300 秒的逾時限制",
                ],
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
                ],
            }
        ],
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert normalized[0].detectable == "partial"
    assert normalized[0].missing_information == [
        "唯讀命令與參數",
        "1 至 300 秒的逾時限制",
    ]
    assert normalized[0].check_steps[0].parameters["timeout_seconds"] == 30


def test_generic_command_missing_target_uses_teacher_facing_description() -> None:
    normalized = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "讀取資料",
                "detectable": "partial",
                "detection_method": "以 exit code 判定",
                "missing_information": ["唯讀命令與參數"],
                "check_steps": [
                    {
                        "template_key": "linux",
                        "command_key": "system.run_command",
                        "parameters": {"success_criteria": "exit code 為 0"},
                    }
                ],
            }
        ],
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert normalized[0].detectable == "partial"
    assert normalized[0].missing_information == [
        "唯讀命令與參數",
        "要檢查的檔案、服務或記錄範圍"
    ]


def test_backend_does_not_infer_config_semantics_from_teacher_text() -> None:
    normalized = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "確認 Web URL 設定",
                "detectable": "partial",
                "detection_method": "使用 cat 讀取 .env",
                "missing_information": [
                    "客觀成功條件",
                    "「成功條件」尚未定義為「包含 web_URL=True 字樣」",
                ],
                "check_steps": [
                    {
                        "template_key": "linux",
                        "command_key": "system.run_command",
                        "parameters": {
                            "cwd": r"C:\Users\陳洋\Desktop\Campus-Cloud",
                            "argv": ["cat", ".env"],
                        },
                    }
                ],
            }
        ],
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    item = normalized[0]
    assert item.detectable == "partial"
    assert item.missing_information == [
        "客觀成功條件",
        "「成功條件」尚未定義為「包含 web_URL=True 字樣」",
    ]
    assert "success_criteria" not in item.check_steps[0].parameters
    assert item.check_steps[0].parameters["timeout_seconds"] == 30


def test_backend_has_no_text_file_intent_parser() -> None:
    assert not hasattr(teacher_judge_service, "_explicit_text_file_item")


@pytest.mark.asyncio
async def test_explicit_env_assignment_forms_proposal_when_model_claims_missing_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 .env 檔案內容",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "讀取指定檔案並比對設定行。",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["cat", "--", "/home/student/.env"],
                                "timeout_seconds": 30,
                                "success_criteria": "輸出含 web_url=True",
                            },
                        }
                    ],
                },
            ),
            _reply_message(
                "「檢查 .env 檔案內容」已整理成提案。"
                "系統會確認指定設定行是否存在；請先查看提案，確認後再套用。",
                "ready",
            ),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[
            SimpleNamespace(
                role="user",
                content="我想和/home/student/.env 內容有 web_url=True 這行就給過",
            )
        ],
        rubric_context=json.dumps({"items": []}),
        template_key="n8n",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 2
    assert "整理成提案" in reply
    assert "重新產生" not in reply
    assert "管理員" not in reply
    assert proposal is not None
    assert proposal[0]["operation"] == "add"
    assert proposal[0]["check_steps"][0]["parameters"]["argv"] == [
        "cat",
        "--",
        "/home/student/.env",
    ]


@pytest.mark.asyncio
async def test_chat_with_rubric_validates_returned_check_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = TeacherJudgeTemplateCommand(
        template_key="n8n",
        command_key="n8n.http_check",
        command_label="n8n HTTP 檢查",
        category="service",
        command_template="curl -I --max-time 5 http://127.0.0.1:5678",
        description="檢查本機 n8n Web 服務是否有 HTTP 回應。",
    )
    captured_payload = {}
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "get_checklist_item",
                {"id": "item-1"},
            ),
            _tool_call_message(
                "edit_checklist_item",
                {
                    "id": "item-1",
                    "title": "n8n Web UI",
                    "detectable": "auto",
                    "check_steps": [
                        {
                            "template_key": "n8n",
                            "command_key": "n8n.http_check",
                        },
                        {
                            "template_key": "n8n",
                            "command_key": "n8n.missing",
                        },
                    ],
                },
            ),
            _reply_message("已更新", "ready"),
        ],
    )

    async def capture_call_vllm(payload, timeout=60.0):
        captured_payload.update(payload)
        return await fake_call_vllm(payload, timeout=timeout)

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", capture_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, updated_items, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="照這樣改")],
        rubric_context=json.dumps({"items": [{"id": "item-1"}]}),
        is_refine=True,
        template_key="n8n",
        template_commands=[command],
    )

    assert updated_items is not None
    system_prompt = captured_payload["messages"][0]["content"]
    assert "目前主要 template：n8n" in system_prompt
    assert "n8n.http_check" in system_prompt
    assert "curl -I" not in system_prompt
    assert updated_items[0]["check_steps"] == [
            {
                "template_key": "n8n",
                "command_key": "n8n.http_check",
                "command_label": "n8n HTTP 檢查",
                "parameters": {},
            }
    ]


@pytest.mark.asyncio
async def test_chat_prompt_accepts_objectively_verifiable_main_py_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = TeacherJudgeTemplateCommand(
        template_key="python",
        command_key="python.run_entrypoint",
        command_label="執行 Python 程式入口",
        category="execution",
        command_template="python3 main.py",
        description=(
            "在老師提供的工作目錄執行 Python 程式入口，收集 exit code、stdout、stderr；"
            "缺少工作目錄或成功條件時先向老師詢問。"
        ),
        risk_level="executes_code",
        requires_confirmation=True,
    )
    captured_payload = {}

    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "main.py 執行結果",
                    "detectable": "auto",
                    "detection_method": (
                        "執行 main.py，依 exit code 與 stderr 判斷錯誤，"
                        "並精確比對 stdout 是否為整數 20。"
                    ),
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
                },
            ),
            _reply_message("已新增可自動檢查的項目。", "ready"),
        ],
    )

    async def capture_call_vllm(payload, timeout=60.0):
        captured_payload.update(payload)
        return await fake_call_vllm(payload, timeout=timeout)

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", capture_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, updated_items, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[
            SimpleNamespace(
                role="user",
                content=(
                    "幫我新增檢查點：在 /home/student/project 執行 python3 main.py，"
                    "確認無錯誤並輸出整數 20。"
                ),
            )
        ],
        rubric_context=json.dumps({"items": []}),
        template_key="python",
        template_commands=[command],
    )

    system_prompt = captured_payload["messages"][0]["content"]
    assert "`auto` 表示「腳本取證支援完整」" in system_prompt
    assert (
        "缺少無法由上下文得知的工作目錄、檔案、服務名稱、Port 或記錄範圍"
        in system_prompt
    )
    assert "判斷方式預設以自動檢查為目標" in system_prompt
    assert "不得因缺少客觀答案而攔截提案" in system_prompt
    assert "不得主觀替老師決定改交導師檢查" in system_prompt
    assert "catalog 有對應能力時" in system_prompt
    assert "用無關檢查替換原目標" in system_prompt
    assert "`auto` 項目的 `check_steps` 應優先引用該 `command_key`" in system_prompt
    assert "沒有專用項目時使用 `system.run_command`" in system_prompt
    assert "`template_key` 只是環境提示，可以省略" in system_prompt
    assert "不得因老師或模型沒有填 `template_key` 而拒絕提案" in system_prompt
    assert "`checked` 表示是否已達成" in system_prompt
    assert "`auto` 項目不得提供 `fallback` 與 `missing_information`" in system_prompt
    assert "python.run_entrypoint" in system_prompt
    assert updated_items is not None
    assert updated_items[-1]["detectable"] == "auto"
    assert updated_items[-1]["check_steps"][0]["command_key"] == (
        "python.run_entrypoint"
    )


@pytest.mark.asyncio
async def test_chat_prompt_accepts_generic_cat_env_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_payload = {}

    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "讀取環境設定",
                    "detectable": "auto",
                    "detection_method": (
                        "以 argv ['cat', '.env']、指定 cwd 與 timeout 執行，"
                        "並原樣取得 exit code、stdout、stderr。"
                    ),
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "cwd": "/home/student/project",
                                "argv": ["cat", ".env"],
                                "timeout_seconds": 10,
                                "success_criteria": "exit code 為 0",
                            },
                        }
                    ],
                },
            ),
            _reply_message("已新增可自動檢查的項目。", "ready"),
        ],
    )

    async def capture_call_vllm(payload, timeout=60.0):
        captured_payload.update(payload)
        return await fake_call_vllm(payload, timeout=timeout)

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", capture_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, updated_items, _metrics = await teacher_judge_service.chat_with_rubric(
            messages=[
                SimpleNamespace(
                    role="user",
                    content="新增檢查點：在 /home/student/project 執行 cat .env",
                )
            ],
        rubric_context=json.dumps({"items": []}),
        template_key="n8n",
        template_commands=[GENERAL_COMMAND],
    )

    system_prompt = captured_payload["messages"][0]["content"]
    assert "system.run_command" in system_prompt
    assert "這個環境已確認具備、可以優先使用的工具" in system_prompt
    assert "不是允許產出提案的完整清單" in system_prompt
    assert "AI 仍應用 `system.run_command` 規劃其他唯讀診斷工具" in system_prompt
    assert "依檢查目的選擇 Linux 或 Windows" in system_prompt
    assert "終端提示字串已包含目前目錄時" in system_prompt
    assert "timeout_seconds 由平台補齊" in system_prompt
    assert 'file_read_example' not in system_prompt
    assert '["cat", ".env"]' not in system_prompt
    assert updated_items is not None
    assert updated_items[0]["detectable"] == "auto"
    assert updated_items[0]["check_steps"][0]["command_key"] == (
        "system.run_command"
    )

@pytest.mark.asyncio
async def test_follow_up_natural_answer_is_audited_before_repeating_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 answer.txt 內容",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "讀取檔案並逐行檢查內容與行數。",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["cat", "/home/student/answer.txt"],
                                "timeout_seconds": 30,
                                "success_criteria": (
                                    "每一行都是整數，且總行數至少為 20"
                                ),
                            },
                        }
                    ],
                },
            ),
            _reply_message(
                "了解，answer.txt 每一行都要是整數，而且至少要有 20 行。"
                "我已依這個規則整理成提案，請確認後再套用。",
                "ready",
            ),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[
            SimpleNamespace(role="user", content="我要檢查 answer.txt"),
            SimpleNamespace(
                role="assistant",
                content="目前還缺少 answer.txt 的內容判定方式，請補充預期內容。",
            ),
            SimpleNamespace(
                role="user",
                content="只要每一行都是整數，而且至少要有 20 行",
            ),
        ],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 2
    assert "每一行都要是整數，而且至少要有 20 行" in reply
    assert proposal is not None
    assert proposal[0]["detectable"] == "auto"
    assert proposal[0]["missing_information"] == []
    assert proposal[0]["check_steps"][0]["parameters"]["success_criteria"] == (
        "每一行都是整數，且總行數至少為 20"
    )


@pytest.mark.asyncio
async def test_follow_up_audit_asks_only_the_remaining_real_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        reply = (
            "還需要客觀成功條件，請再補充。"
            if len(calls) != 1
            else (
                "我知道你要確認 web 服務正常，但『正常』有兩種檢查方式："
                "你要確認服務程序正在執行，還是網頁可以正常開啟？"
            )
        )
        return (
            json.dumps(
                {
                    "reply": reply,
                    "proposal_status": "needs_information",
                    "updated_items": None,
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[
            SimpleNamespace(role="user", content="檢查 web 服務"),
            SimpleNamespace(role="assistant", content="請告訴我怎樣才算正常。"),
            SimpleNamespace(role="user", content="正常運作就可以"),
        ],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 1
    assert proposal is None
    assert "服務程序正在執行，還是網頁可以正常開啟" in reply
    assert "客觀成功條件" not in reply


@pytest.mark.asyncio
async def test_chat_prompt_treats_attachment_as_concrete_rubric_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_payload = {}
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "服務 Port",
                    "checked": False,
                    "detectable": "auto",
                    "detection_method": "檢查 listening ports。",
                },
            ),
            _reply_message("已依附件整理檢查項目。", "ready"),
        ],
    )

    async def capture_call_vllm(payload, timeout=60.0):
        if not captured_payload:
            captured_payload.update(payload)
        return await fake_call_vllm(payload, timeout=timeout)

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", capture_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, updated_items, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="幫我增加這些項目")],
        rubric_context=json.dumps({"items": []}),
        attachment_context=(
            "--- 附件：rubric.md ---\n"
            "| 審查重點 | AI 可以參考的線索 |\n"
            "| 服務 Port | Listening ports |\n"
            "--- 附件結束 ---"
        ),
    )

    system_prompt = captured_payload["messages"][0]["content"]
    assert "附件中的可讀文字就是老師提供的具體內容" in system_prompt
    assert "不要因目前項目數為 0 就回覆尚未提供內容" in system_prompt
    assert "附件表格的每一列可轉成一個檢查項目" in system_prompt
    assert captured_payload["messages"][-1]["role"] == "user"
    assert "請直接逐條核查，不要求教師再使用「新增」句型" in (
        captured_payload["messages"][-1]["content"]
    )
    assert "請以 create_checklist_item 或 edit_checklist_item 逐項建立提案" in (
        captured_payload["messages"][-1]["content"]
    )
    assert updated_items is not None
    assert updated_items[0]["title"] == "服務 Port"


MULTI_ROW_ATTACHMENT_CONTEXT = (
    "--- 附件：rubric.md ---\n"
    "| 審查重點 | AI 可以參考的線索 |\n"
    "| 確認 Python 版本 | python --version |\n"
    "| 檢查 Port 8080 | Listening ports |\n"
    "| 程式架構品質 | 主觀評分 |\n"
    "--- 附件結束 ---"
)

_ITEMWISE_TITLES = ("確認 Python 版本", "檢查 Port 8080", "程式架構品質")


def _itemwise_ready_tool_call(title: str, judgement_mode: str = "ai") -> dict[str, object]:
    """Round-1 tool call for one itemwise requirement: create a Ready proposal."""
    parameters: dict[str, object] = {
        "cwd": "/home/student/project",
        "argv": ["python3", "--version"],
        "timeout_seconds": 30,
    }
    if judgement_mode != "teacher":
        parameters["success_criteria"] = "stdout 包含 Python 3"
    return _tool_call_message(
        "create_checklist_item",
        {
            "title": title,
            "checked": False,
            "detectable": "auto",
            "judgement_mode": judgement_mode,
            "detection_method": "執行唯讀指令並收集輸出。",
            "check_steps": [
                {
                    "template_key": "linux",
                    "command_key": "system.run_command",
                    "parameters": parameters,
                }
            ],
        },
    )


def _itemwise_extraction_payload() -> str:
    return json.dumps(
        {
            "items": [
                {
                    "source_index": 1,
                    "title": "確認 Python 版本",
                    "evidence_hint": "python --version",
                },
                {"source_index": 2, "title": "檢查 Port 8080"},
                {
                    "source_index": 3,
                    "title": "程式架構品質",
                    "description": "主觀評分",
                },
            ]
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_attachment_itemwise_analysis_checks_each_row_in_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        system_prompt = payload["messages"][0]["content"]
        if "評分表拆解器" in system_prompt:
            return (_itemwise_extraction_payload(), {"total_tokens": 1})
        if payload["messages"][-1]["role"] == "user":
            user_content = payload["messages"][-1]["content"]
            if "確認 Python 版本" in user_content:
                return (
                    _itemwise_ready_tool_call("確認 Python 版本"),
                    {"total_tokens": 1},
                )
            if "檢查 Port 8080" in user_content:
                return (
                    json.dumps(
                        {
                            "reply": "「檢查 Port 8080」還缺少要檢查的服務或連接埠範圍。",
                            "proposal_status": "needs_information",
                            "conversation_focus": {
                                "turn_kind": "requirement",
                                "requirements": [
                                    {
                                        "focus_key": "port-8080",
                                        "status": "needs_information",
                                        "known_information": [],
                                        "missing_information": [
                                            "要檢查的服務或連接埠範圍"
                                        ],
                                    }
                                ],
                            },
                        },
                        ensure_ascii=False,
                    ),
                    {"total_tokens": 1},
                )
            if "程式架構品質" in user_content:
                return (
                    _itemwise_ready_tool_call(
                        "程式架構品質", judgement_mode="teacher"
                    ),
                    {"total_tokens": 1},
                )
        if payload["messages"][-1]["role"] == "tool":
            return (
                _reply_message("已把該需求整理成提案。", "ready"),
                {"total_tokens": 1},
            )
        raise AssertionError("unexpected model call")

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    result = await teacher_judge_service.analyze_attachments_itemwise(
        teacher_message="幫我增加這些項目",
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
        attachment_context=MULTI_ROW_ATTACHMENT_CONTEXT,
        rubric_available=True,
    )

    item_calls = [
        payload
        for payload in calls
        if "評分表拆解器" not in payload["messages"][0]["content"]
    ]
    assert len(item_calls) == 5
    seen_titles = []
    for payload in item_calls:
        joined = "\n".join(
            str(message["content"] or "") for message in payload["messages"]
        )
        present = [title for title in _ITEMWISE_TITLES if title in joined]
        assert len(present) == 1
        seen_titles.append(present[0])
        assert "審查重點" not in joined
        for other in _ITEMWISE_TITLES:
            if other not in present:
                assert other not in joined
    assert set(seen_titles) == set(_ITEMWISE_TITLES)

    assert [row["source_index"] for row in result.item_results] == [1, 2, 3]
    assert [row["status"] for row in result.item_results] == [
        "ready",
        "needs_information",
        "teacher_review",
    ]
    assert result.proposal is not None
    proposal_ids = [operation["id"] for operation in result.proposal]
    assert len(proposal_ids) == 2
    assert len(set(proposal_ids)) == 2
    assert all(item_id.startswith("item-") for item_id in proposal_ids)
    assert result.proposal[0]["title"] == "確認 Python 版本"
    assert result.proposal[1]["judgement_mode"] == "teacher"
    assert result.item_results[0]["operation"]["id"] == proposal_ids[0]
    assert result.item_results[1]["missing_information"] == [
        "要檢查的服務或連接埠範圍"
    ]
    assert "第 2 列" in result.reply
    assert "還缺少資訊" in result.reply


@pytest.mark.asyncio
async def test_attachment_itemwise_single_item_failure_keeps_other_proposals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        system_prompt = payload["messages"][0]["content"]
        if "評分表拆解器" in system_prompt:
            return (
                json.dumps(
                    {"items": [{"title": "第一項"}, {"title": "第二項"}, {"title": "第三項"}]},
                    ensure_ascii=False,
                ),
                {"total_tokens": 1},
            )
        if payload["messages"][-1]["role"] == "user":
            user_content = payload["messages"][-1]["content"]
            if "第二項" in user_content:
                raise HTTPException(status_code=504, detail="AI 服務逾時")
            title = "第一項" if "第一項" in user_content else "第三項"
            return (_itemwise_ready_tool_call(title), {"total_tokens": 1})
        return (_reply_message("已整理成提案。", "ready"), {"total_tokens": 1})

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    result = await teacher_judge_service.analyze_attachments_itemwise(
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
        attachment_context=MULTI_ROW_ATTACHMENT_CONTEXT,
    )

    assert result.proposal is not None
    failure_ids = [operation["id"] for operation in result.proposal]
    assert len(failure_ids) == 2
    assert len(set(failure_ids)) == 2
    assert [row["status"] for row in result.item_results] == [
        "ready",
        "analysis_error",
        "ready",
    ]
    assert "AI 回覆失敗" in result.item_results[1]["detail"]
    assert "第 2 列" in result.reply


@pytest.mark.asyncio
async def test_attachment_itemwise_extraction_error_does_not_create_proposals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        return ("這不是 JSON", {"total_tokens": 1})

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    result = await teacher_judge_service.analyze_attachments_itemwise(
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
        attachment_context=MULTI_ROW_ATTACHMENT_CONTEXT,
    )

    assert len(calls) == 1
    assert result.proposal is None
    assert result.item_results == []
    assert "無法逐項核查附件" in result.reply


@pytest.mark.asyncio
async def test_attachment_itemwise_without_rubric_rows_does_not_create_proposals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_vllm(payload, timeout=60.0):
        assert "評分表拆解器" in payload["messages"][0]["content"]
        return (json.dumps({"items": []}, ensure_ascii=False), {"total_tokens": 1})

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    result = await teacher_judge_service.analyze_attachments_itemwise(
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
        attachment_context="--- 附件：notes.md ---\n這只是課程說明文字。\n--- 附件結束 ---",
    )

    assert result.proposal is None
    assert result.item_results == []
    assert "沒有辨識出可核查的評分列" in result.reply


@pytest.mark.asyncio
async def test_attachment_itemwise_keeps_duplicate_titles_as_separate_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_vllm(payload, timeout=60.0):
        system_prompt = payload["messages"][0]["content"]
        if "評分表拆解器" in system_prompt:
            return (
                json.dumps(
                    {"items": [{"title": "檢查 Port"}, {"title": "檢查 Port"}]},
                    ensure_ascii=False,
                ),
                {"total_tokens": 1},
            )
        if payload["messages"][-1]["role"] == "user":
            return (
                _itemwise_ready_tool_call("檢查 Port"),
                {"total_tokens": 1},
            )
        return (_reply_message("已整理成提案。", "ready"), {"total_tokens": 1})

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    result = await teacher_judge_service.analyze_attachments_itemwise(
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
        attachment_context=MULTI_ROW_ATTACHMENT_CONTEXT,
    )

    assert [row["source_index"] for row in result.item_results] == [1, 2]
    assert [row["title"] for row in result.item_results] == ["檢查 Port", "檢查 Port"]
    assert result.proposal is not None
    duplicate_ids = [operation["id"] for operation in result.proposal]
    assert len(duplicate_ids) == 2
    assert len(set(duplicate_ids)) == 2


def test_normalize_allows_teacher_judgement_when_script_inputs_are_complete() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "收集 main.py 輸出供老師評閱",
                "detectable": "auto",
                "judgement_mode": "teacher",
                "detection_method": "執行程式並收集 stdout 與 stderr",
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.run_entrypoint",
                        "parameters": {
                            "cwd": "/home/student/project",
                            "argv": ["python3", "main.py"],
                            "timeout_seconds": 30,
                        },
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[_python_entrypoint_command()],
    )

    assert items[0].detectable == "auto"
    assert items[0].judgement_mode == "teacher"
    assert items[0].missing_information == []


@pytest.mark.asyncio
async def test_teacher_judgement_requirement_can_form_proposal_without_objective_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "程式架構品質",
                    "checked": False,
                    "detectable": "auto",
                    "judgement_mode": "teacher",
                    "detection_method": "讀取 main.py 內容供導師審核。",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "cwd": "/home/student/project",
                                "argv": ["cat", "main.py"],
                                "timeout_seconds": 30,
                            },
                        }
                    ],
                },
            ),
            _reply_message("已建立取證提案，結果交由導師人工審核。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[
            SimpleNamespace(
                role="user",
                content=(
                    "在 /home/student/project 讀取 main.py，"
                    "把程式碼交給我人工審核架構品質。"
                ),
            )
        ],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 2
    assert proposal is not None
    assert proposal[0]["detectable"] == "auto"
    assert proposal[0]["judgement_mode"] == "teacher"
    assert "success_criteria" not in proposal[0]["check_steps"][0]["parameters"]


def test_normalize_marks_auto_without_valid_check_steps_as_unsupported() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "title": "未知檢查",
                "detectable": "auto",
                "check_steps": [{"template_key": "n8n", "command_key": "missing"}],
            }
        ],
        template_key="n8n",
        template_commands=[],
    )

    assert items == [
        TeacherJudgeRubricItem(
            id="item-1",
            title="未知檢查",
            checked=False,
            detectable="manual",
            detection_method="目前沒有可引用的有效 command_key，缺少自動取得客觀證據的能力",
            fallback="目前平台不支援此項目的安全腳本取證。",
            check_steps=[],
        )
    ]


@pytest.mark.asyncio
async def test_new_item_proposal_does_not_load_current_rubric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 result.txt",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "讀取檔案並確認內容。",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["cat", "/tmp/result.txt"],
                                "timeout_seconds": 30,
                                "success_criteria": "stdout 包含 OK",
                            },
                        }
                    ],
                },
            ),
            _reply_message("已把新的檔案檢查整理成提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="新增檢查 result.txt 包含 OK")],
        rubric_context=json.dumps(
            {"items": [{"id": "item-existing", "title": "既有機密項目"}]},
            ensure_ascii=False,
        ),
        template_commands=[GENERAL_COMMAND],
        analysis_revision=7,
        rubric_available=True,
    )

    assert len(calls) == 2
    assert calls[0]["tool_choice"] == "auto"
    assert "既有機密項目" not in json.dumps(
        calls[0]["messages"], ensure_ascii=False
    )
    assert proposal is not None
    assert proposal[0]["id"].startswith("item-")
    assert proposal[0]["operation"] == "add"


@pytest.mark.asyncio
async def test_existing_item_update_loads_current_rubric_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
                "get_checklist_item",
                {"id": "item-port"},
            ),
            _tool_call_message(
                "edit_checklist_item",
                {
                    "id": "item-port",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["ss", "-ltn"],
                                "timeout_seconds": 30,
                                "success_criteria": "存在 8080 監聽 Port",
                            },
                        }
                    ],
                },
            ),
            _reply_message("已把 Port 調整整理成提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)
    current = {
        "items": [
            {
                "id": "item-port",
                "title": "既有 Port 檢查",
                "detectable": "auto",
                "judgement_mode": "ai",
                "detection_method": "檢查指定 Port。",
                "missing_information": [],
                "check_steps": [
                    {
                        "template_key": "linux",
                        "command_key": "system.run_command",
                        "parameters": {
                            "argv": ["ss", "-ltn"],
                            "timeout_seconds": 30,
                            "success_criteria": "存在 3000 監聽 Port",
                        },
                    }
                ],
            }
        ]
    }

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="把第一項改成 Port 8080")],
        rubric_context=json.dumps(current, ensure_ascii=False),
        template_commands=[GENERAL_COMMAND],
        analysis_revision=7,
        rubric_available=True,
    )

    assert len(calls) == 3
    read_result = json.loads(calls[1]["messages"][-1]["content"])
    assert read_result["analysis_revision"] == 7
    assert read_result["item"]["title"] == "既有 Port 檢查"
    assert calls[1]["tool_choice"] == "auto"
    assert proposal is not None
    assert proposal[0]["id"] == "item-port"
    assert proposal[0]["operation"] == "update"


@pytest.mark.asyncio
async def test_edit_patch_supplying_execution_info_clears_stale_missing_information(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message("get_checklist_item", {"id": "item-python"}),
            _tool_call_message(
                "edit_checklist_item",
                {
                    "id": "item-python",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "執行 main.py 並檢查 stdout",
                    "check_steps": [
                        {
                            "template_key": "python",
                            "command_key": "python.run_entrypoint",
                            "parameters": {
                                "cwd": "/home/owo",
                                "argv": ["python3", "main.py"],
                                "timeout_seconds": 30,
                                "success_criteria": "exit code 為 0",
                            },
                        }
                    ],
                },
            ),
            _reply_message("已補上路徑並整理成提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    current = {
        "items": [
            {
                "id": "item-python",
                "title": "main.py 執行檢查",
                "detectable": "partial",
                "judgement_mode": "ai",
                "detection_method": None,
                "missing_information": [
                    "Python 執行檔的完整路徑（若非預設路徑）",
                    "main.py 所在的完整工作目錄路徑",
                ],
                "check_steps": [],
            }
        ]
    }

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="工作目錄是 /home/owo")],
        rubric_context=json.dumps(current, ensure_ascii=False),
        template_key="python",
        template_commands=[_python_entrypoint_command()],
        analysis_revision=3,
        rubric_available=True,
    )

    assert len(calls) == 3
    staged_result = json.loads(calls[2]["messages"][-1]["content"])
    assert staged_result["staged"] == "update"
    assert proposal is not None
    assert proposal[0]["id"] == "item-python"
    assert proposal[0]["operation"] == "update"
    assert proposal[0]["detectable"] == "auto"
    assert proposal[0]["missing_information"] == []


@pytest.mark.asyncio
async def test_edit_patch_with_incomplete_parameters_returns_retry_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message("get_checklist_item", {"id": "item-env"}),
            _tool_call_message(
                "edit_checklist_item",
                {
                    "id": "item-env",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "讀取 .env 並比對內容",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "cwd": "/home/owo",
                                "success_criteria": "exit code 為 0",
                            },
                        }
                    ],
                },
            ),
            _tool_call_message(
                "edit_checklist_item",
                {
                    "id": "item-env",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "讀取 .env 並比對內容",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "cwd": "/home/owo",
                                "argv": ["cat", ".env"],
                                "timeout_seconds": 30,
                                "success_criteria": "exit code 為 0",
                            },
                        }
                    ],
                },
            ),
            _reply_message("已補上 argv 並整理成提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    current = {
        "items": [
            {
                "id": "item-env",
                "title": "學生 .env 內容收集",
                "detectable": "partial",
                "judgement_mode": "ai",
                "detection_method": None,
                "missing_information": [
                    ".env 檔案所在的完整工作目錄路徑",
                    "唯讀命令與參數",
                ],
                "check_steps": [],
            }
        ]
    }

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="路徑是 /home/owo")],
        rubric_context=json.dumps(current, ensure_ascii=False),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
        analysis_revision=3,
        rubric_available=True,
    )

    assert len(calls) == 4
    rejected_result = json.loads(calls[2]["messages"][-1]["content"])
    assert "可由你自行補齊" in rejected_result["error"]
    assert "argv" in rejected_result["error"]
    assert "請改在 reply 中說明缺少的內容" not in rejected_result["error"]
    staged_result = json.loads(calls[3]["messages"][-1]["content"])
    assert staged_result["staged"] == "update"
    assert proposal is not None
    assert proposal[0]["id"] == "item-env"
    assert proposal[0]["operation"] == "update"
    assert proposal[0]["detectable"] == "auto"
    assert proposal[0]["missing_information"] == []


@pytest.mark.asyncio
async def test_existing_item_proposal_without_tool_is_retried_with_forced_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            # The model tries to edit an existing item without reading it first;
            # the tool rejects the call and the model recovers by reading.
            _tool_call_message(
                "edit_checklist_item",
                {
                    "id": "item-existing",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "讀取指定檔案。",
                    "missing_information": [],
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["cat", "/tmp/result.txt"],
                                "timeout_seconds": 30,
                                "success_criteria": "exit code 為 0",
                            },
                        }
                    ],
                },
            ),
            _tool_call_message("get_checklist_item", {"id": "item-existing"}),
            _tool_call_message(
                "edit_checklist_item",
                {
                    "id": "item-existing",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "讀取指定檔案。",
                    "missing_information": [],
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["cat", "/tmp/result.txt"],
                                "timeout_seconds": 30,
                                "success_criteria": "exit code 為 0",
                            },
                        }
                    ],
                },
            ),
            _reply_message("已整理修改提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="修改既有項目的說明")],
        rubric_context=json.dumps(
            {"items": [{"id": "item-existing", "title": "既有項目"}]},
            ensure_ascii=False,
        ),
        template_commands=[GENERAL_COMMAND],
        analysis_revision=4,
        rubric_available=True,
    )

    assert len(calls) == 4
    rejected_result = json.loads(calls[1]["messages"][-1]["content"])
    assert "list_checklist" in rejected_result["error"]
    assert "get_checklist_item" in rejected_result["error"]
    assert proposal is not None
    assert proposal[0]["id"] == "item-existing"
    assert proposal[0]["operation"] == "update"


@pytest.mark.asyncio
async def test_ready_claim_without_tool_call_is_repaired_by_forced_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            # The model twice claims Ready without calling any proposal tool;
            # the second reminder round forces create_checklist_item so the
            # loop converges through the tool channel instead of retry text.
            _reply_message("我已把 Python 版本檢查整理成提案。", "ready"),
            _reply_message("我已把 Python 版本檢查整理成提案。", "ready"),
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 Python 版本",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "執行唯讀版本查詢。",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["python3", "--version"],
                                "success_criteria": "stdout 包含 Python 3",
                            },
                        }
                    ],
                },
            ),
            _reply_message("提案已建立，請確認後套用。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="檢查 Python 版本")],
        rubric_context=json.dumps({"items": []}, ensure_ascii=False),
        template_commands=[GENERAL_COMMAND],
        rubric_available=True,
    )

    assert len(calls) == 4
    assert calls[2]["tool_choice"] == {
        "type": "function",
        "function": {"name": "create_checklist_item"},
    }
    assert proposal is not None
    assert proposal[0]["operation"] == "add"


@pytest.mark.asyncio
async def test_prose_creation_claim_without_tool_call_is_rewritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            # Prose claims a proposal was created while the structured status
            # says none; the server has no staged op, so the claim is false and
            # the reply must be replaced by the actual outcome explanation.
            _reply_message("我已建立提案「檢查 Port 8080」。", "none"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="檢查 Port 8080")],
        rubric_context=json.dumps({"items": []}, ensure_ascii=False),
        template_commands=[GENERAL_COMMAND],
        rubric_available=True,
    )

    assert len(calls) == 1
    assert proposal is None
    assert "沒有成功整理出可套用的提案" in _reply


@pytest.mark.asyncio
async def test_ready_claim_without_rubric_source_gets_no_source_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            # Without a rubric source the request carries no tools, so a Ready
            # claim can never be satisfied; the reply must say so instead of
            # asking the teacher to retry.
            _reply_message("已建立提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="檢查服務狀態")],
        rubric_context="{}",
        template_commands=None,
        rubric_available=False,
    )

    assert len(calls) == 1
    assert "tools" not in calls[0]
    assert "目前對話尚未選擇檢查表來源" in calls[0]["messages"][0]["content"]
    assert proposal is None
    assert _reply == teacher_judge_service._NO_RUBRIC_READY_REPLY


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


@pytest.mark.asyncio
async def test_complete_manual_system_info_candidate_reselects_generic_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            # The model first submits a complete-looking manual candidate;
            # the tool rejects it for skipping the available generic capability,
            # then the model resubmits as auto + ai with a complete argv step.
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "查看學生系統版本",
                    "detectable": "manual",
                    "judgement_mode": "teacher",
                    "detection_method": "查看系統資訊。",
                    "check_steps": [],
                    "fallback": "由老師自行查看。",
                },
            ),
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "查看學生系統版本",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "執行唯讀指令並收集輸出。",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["uname", "-a"],
                                "success_criteria": "exit code 為 0",
                            },
                        }
                    ],
                },
            ),
            _reply_message(
                "我已把系統版本查詢整理成提案，請確認後再套用。", "ready"
            ),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="查看學生系統版本")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 3
    capability_error = json.loads(calls[1]["messages"][-1]["content"])
    assert "已提供 system.run_command" in capability_error["error"]
    assert "只有確實無法取得任何證據" in capability_error["error"]
    assert "整理成提案" in reply
    assert proposal is not None
    assert proposal[0]["detectable"] == "auto"
    assert proposal[0]["judgement_mode"] == "ai"
    assert proposal[0]["check_steps"][0]["command_key"] == "system.run_command"
    assert proposal[0]["check_steps"][0]["parameters"]["argv"] == ["uname", "-a"]


@pytest.mark.asyncio
async def test_invalid_step_then_manual_uses_distinct_capability_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            # First candidate declares auto with a step that is not in the
            # catalog; the tool rejects it with the validated command list.
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "查看學生系統版本",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "執行唯讀指令並收集輸出。",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.info",
                            "parameters": {},
                        }
                    ],
                },
            ),
            # Second candidate skips the generic capability entirely; the tool
            # rejects it with a distinct capability-review error.
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "查看學生系統版本",
                    "detectable": "manual",
                    "judgement_mode": "teacher",
                    "detection_method": "執行唯讀系統版本查詢。",
                    "check_steps": [],
                    "fallback": "由老師自行查看。",
                },
            ),
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "查看學生系統版本",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "執行唯讀指令並收集輸出。",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["uname", "-a"],
                                "success_criteria": "exit code 為 0",
                            },
                        }
                    ],
                },
            ),
            _reply_message("我已整理成提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="查看學生系統版本")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 4
    step_error = json.loads(calls[1]["messages"][-1]["content"])
    assert "check_steps 沒有通過驗證" in step_error["error"]
    capability_error = json.loads(calls[2]["messages"][-1]["content"])
    assert "已提供 system.run_command" in capability_error["error"]
    assert "整理成提案" in reply
    assert proposal is not None
    assert proposal[0]["check_steps"][0]["command_key"] == "system.run_command"


@pytest.mark.asyncio
async def test_none_response_without_focus_does_not_invent_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        if len(calls) == 1:
            return (
                json.dumps(
                    {
                        "reply": "老師您好，請問有什麼我可以幫您的嗎？",
                        "proposal_status": "none",
                        "updated_items": None,
                    },
                    ensure_ascii=False,
                ),
                {},
            )
        assert payload["temperature"] == 0.0
        assert len(payload["messages"]) == 2
        assert "判斷 turn_kind" in payload["messages"][0]["content"]
        assert payload["messages"][-1]["content"] == "查看學生系統版本"
        return (
            json.dumps(
                {
                    "reply": "我已把系統版本查詢整理成提案，請確認後再套用。",
                    "proposal_status": "ready",
                    "conversation_focus": {
                        "turn_kind": "requirement",
                        "requirements": [
                            {
                                "focus_key": "system-version",
                                "status": "ready",
                                "known_information": ["查看學生系統版本"],
                                "missing_information": [],
                                "target_item_id": None,
                            }
                        ],
                    },
                    "updated_items": [
                        {
                            "operation": "add",
                            "id": "item-system-version",
                            "title": "查看學生系統版本",
                            "checked": False,
                            "detectable": "auto",
                            "judgement_mode": "teacher",
                            "detection_method": "執行唯讀系統版本查詢。",
                            "missing_information": [],
                            "check_steps": [
                                {
                                    "template_key": "linux",
                                    "command_key": "system.run_command",
                                    "parameters": {"argv": ["uname", "-a"]},
                                }
                            ],
                            "fallback": None,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="查看學生系統版本")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 1
    assert "有什麼我可以幫您" in reply
    assert proposal is None


@pytest.mark.asyncio
async def test_empty_question_classification_remains_plain_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        if len(calls) == 1:
            return (
                json.dumps(
                    {
                        "reply": "老師您好，請問有什麼我可以幫您的嗎？",
                        "proposal_status": "none",
                        "conversation_focus": {
                            "turn_kind": "question",
                            "requirements": [],
                        },
                        "updated_items": None,
                    },
                    ensure_ascii=False,
                ),
                {},
            )
        assert payload["temperature"] == 0.0
        assert "判斷 turn_kind" in payload["messages"][0]["content"]
        assert payload["messages"][-1]["content"] == "查看學生系統版本"
        return (
            json.dumps(
                {
                    "reply": "我已把系統版本查詢整理成提案，請確認後再套用。",
                    "proposal_status": "ready",
                    "conversation_focus": {
                        "turn_kind": "requirement",
                        "requirements": [
                            {
                                "focus_key": "system-version",
                                "status": "ready",
                                "known_information": ["查看學生系統版本"],
                                "missing_information": [],
                                "target_item_id": None,
                            }
                        ],
                    },
                    "updated_items": [
                        {
                            "operation": "add",
                            "id": "item-system-version",
                            "title": "查看學生系統版本",
                            "checked": False,
                            "detectable": "auto",
                            "judgement_mode": "teacher",
                            "detection_method": "執行唯讀系統版本查詢。",
                            "missing_information": [],
                            "check_steps": [
                                {
                                    "template_key": "linux",
                                    "command_key": "system.run_command",
                                    "parameters": {"argv": ["uname", "-a"]},
                                }
                            ],
                            "fallback": None,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="查看學生系統版本")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 1
    assert proposal is None


@pytest.mark.asyncio
async def test_boolean_detectable_and_flat_generic_argv_form_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = _scripted_vllm(
        [
            # Legacy-style tool arguments: boolean detectable and a flat
            # check_step with argv directly on the step object.
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 Python 版本",
                    "checked": False,
                    "detectable": True,
                    "judgement_mode": "teacher",
                    "detection_method": "command_output",
                    "missing_information": [],
                    "check_steps": [
                        {
                            "command_key": "system.run_command",
                            "argv": ["python3", "--version"],
                        }
                    ],
                },
            ),
            _reply_message("我已把 Python 版本檢查整理成提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="再發提案")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 2
    assert proposal is not None
    assert proposal[0]["detectable"] == "auto"
    assert proposal[0]["check_steps"][0]["command_key"] == "system.run_command"
    assert proposal[0]["check_steps"][0]["parameters"]["argv"] == [
        "python3",
        "--version",
    ]


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
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
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
            _reply_message("已建立 Python 版本檢查提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

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
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
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
            _reply_message(
                "我已把「檢查 Python 版本」整理成提案。請先查看提案內容，確認後再套用。",
                "ready",
            ),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="我想看學生 Python 的版本號")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[_python_version_command(), GENERAL_COMMAND],
    )

    assert len(calls) == 2
    assert reply == "我已把「檢查 Python 版本」整理成提案。請先查看提案內容，確認後再套用。"
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
    calls, fake_call_vllm = _scripted_vllm(
        [
            _tool_call_message(
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
            _reply_message(
                "「檢查 torch 套件安裝情況」已整理成提案。"
                "系統會確認套件是否已安裝；請先查看提案，確認後再套用。",
                "ready",
            ),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

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


def test_unavailable_reply_explains_invalid_catalog_reference() -> None:
    raw_items = [
        {
            "id": "item-1",
            "title": "未知檢查",
            "detectable": "auto",
            "check_steps": [
                {"template_key": "n8n", "command_key": "missing.command"}
            ],
        }
    ]
    normalized = teacher_judge_service._normalize_rubric_items(
        raw_items,
        template_key="n8n",
        template_commands=[],
    )

    reply = teacher_judge_service._proposal_unavailable_reply(
        normalized,
        raw_items,
        [],
    )

    assert "缺少可執行的檢查內容" in reply
    assert "n8n/missing.command" in reply
    assert "工具清單只是優先建議，不會限制提案" in reply
    assert "沒有提供完整 argv" in reply
    assert "不是老師需要補充答案" in reply


def test_unavailable_reply_explains_missing_result_in_plain_language() -> None:
    item = TeacherJudgeRubricItem(
        id="item-answer",
        title="檢查 answer.txt 內容",
        detectable="partial",
        judgement_mode="ai",
        detection_method="讀取 answer.txt 並檢查內容。",
        missing_information=["客觀成功條件"],
    )

    reply = teacher_judge_service._proposal_unavailable_reply([item], [item.model_dump()])

    assert "「檢查 answer.txt 內容」" in reply
    assert "目前還缺少通過方式" in reply
    assert "請補充預期文字或內容" in reply
    assert "沒有固定答案時，也可以先收集結果讓你查看" in reply
    assert "檢查位置" not in reply
    assert "完整路徑" not in reply
    assert "我還不知道怎樣才算通過" not in reply
    assert "補充後，我會重新確認並建立提案給你查看" not in reply
    assert "客觀成功條件" not in reply


def test_unavailable_reply_only_asks_for_location_when_location_is_missing() -> None:
    item = TeacherJudgeRubricItem(
        id="item-log",
        title="檢查服務日誌",
        detectable="partial",
        judgement_mode="teacher",
        detection_method="收集服務日誌供老師查看。",
        missing_information=["服務日誌的檔案位置"],
    )

    reply = teacher_judge_service._proposal_unavailable_reply([item], [item.model_dump()])

    assert "檢查服務日誌" in reply
    assert "檢查位置" in reply
    assert "完整路徑" in reply
    assert "服務、連接埠或記錄範圍" not in reply
    assert "通過方式" not in reply
    assert "預期結果" not in reply


def test_unavailable_reply_hides_platform_fields_from_teacher() -> None:
    item = TeacherJudgeRubricItem(
        id="item-internal",
        title="檢查服務",
        detectable="partial",
        judgement_mode="ai",
        detection_method="收集服務資訊。",
        missing_information=[
            "腳本取證方式",
            "1 至 300 秒的逾時限制",
            "有效的檢查能力：system.run_command",
            "proposal_status",
        ],
    )

    reply = teacher_judge_service._proposal_unavailable_reply([item], [item.model_dump()])

    assert "檢查服務" in reply
    assert "會影響檢查範圍或判定的資訊" in reply
    assert "腳本取證" not in reply
    assert "逾時" not in reply
    assert "proposal_status" not in reply


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
