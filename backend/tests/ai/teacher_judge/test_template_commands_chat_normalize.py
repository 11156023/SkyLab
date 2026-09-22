"""Split from tests/test_rubric_template_commands.py: normalize / edit-patch / repair flows."""

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
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
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
            reply_message("已建立取證提案，結果交由導師人工審核。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
async def test_edit_patch_supplying_execution_info_clears_stale_missing_information(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message("get_checklist_item", {"id": "item-python"}),
            tool_call_message(
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
            reply_message("已補上路徑並整理成提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message("get_checklist_item", {"id": "item-env"}),
            tool_call_message(
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
            tool_call_message(
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
            reply_message("已補上 argv 並整理成提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
async def test_ready_claim_without_tool_call_is_repaired_by_forced_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            # The model twice claims Ready without calling any proposal tool;
            # the second reminder round forces create_checklist_item so the
            # loop converges through the tool channel instead of retry text.
            reply_message("我已把 Python 版本檢查整理成提案。", "ready"),
            reply_message("我已把 Python 版本檢查整理成提案。", "ready"),
            tool_call_message(
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
            reply_message("提案已建立，請確認後套用。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
async def test_finalizer_repairs_legacy_plan_before_accepting_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_item = {
        "id": "item-legacy",
        "title": "讀取 main.log",
        "detectable": "auto",
        "judgement_mode": "teacher",
        "detection_method": "讀取 main.log 供老師檢查。",
        "missing_information": [],
        "check_steps": [
            {
                "id": "step-1",
                "title": "讀取 main.log",
                "argv": ["cat", "/home/student/main.log"],
                "timeout_seconds": 30,
            }
        ],
    }
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message("list_checklist", {}),
            tool_call_message("get_checklist_item", {"id": "item-legacy"}),
            reply_message("我已將 legacy step 轉成 typed proposal。", "ready"),
            tool_call_message(
                "edit_checklist_item",
                {
                    "id": "item-legacy",
                    "check_steps": [
                        {
                            "id": "step-1",
                            "title": "讀取 main.log",
                            "collector": {
                                "type": "command",
                                "argv": ["cat", "/home/student/main.log"],
                                "timeout_seconds": 30,
                            },
                        }
                    ],
                },
            ),
            reply_message("typed proposal 已建立。", "ready"),
        ]
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="請核對完整檢查表")],
        rubric_context=json.dumps({"items": [legacy_item]}, ensure_ascii=False),
        is_refine=True,
        template_commands=[],
        rubric_available=True,
    )

    assert len(calls) == 5
    assert calls[3]["tool_choice"] == {
        "type": "function",
        "function": {"name": "edit_checklist_item"},
    }
    repair_message = calls[3]["messages"][-1]["content"]
    assert "item-legacy" in repair_message
    assert "flat legacy shape" in repair_message
    assert proposal is not None
    assert proposal[0]["operation"] == "update"
    assert proposal[0]["check_steps"][0]["collector"]["type"] == "command"


@pytest.mark.asyncio
async def test_finalizer_stops_after_same_contract_repair_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_item = {
        "id": "item-legacy",
        "title": "讀取 main.log",
        "detectable": "auto",
        "judgement_mode": "teacher",
        "detection_method": "讀取 main.log 供老師檢查。",
        "missing_information": [],
        "check_steps": [
            {
                "id": "step-1",
                "title": "讀取 main.log",
                "argv": ["cat", "/home/student/main.log"],
                "timeout_seconds": 30,
            }
        ],
    }
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message("list_checklist", {}),
            tool_call_message("get_checklist_item", {"id": "item-legacy"}),
            reply_message("已完成 typed 轉換。", "ready"),
            reply_message("已完成 typed 轉換。", "ready"),
        ]
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="請核對完整檢查表")],
        rubric_context=json.dumps({"items": [legacy_item]}, ensure_ascii=False),
        is_refine=True,
        template_commands=[],
        rubric_available=True,
    )

    assert len(calls) == 4
    assert calls[3]["tool_choice"] == {
        "type": "function",
        "function": {"name": "edit_checklist_item"},
    }
    assert proposal == []


@pytest.mark.asyncio
async def test_prose_creation_claim_without_tool_call_is_rewritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            # Prose claims a proposal was created while the structured status
            # says none; the server has no staged op, so the claim is false and
            # the reply must be replaced by the actual outcome explanation.
            reply_message("我已建立提案「檢查 Port 8080」。", "none"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    calls, fake_call_vllm = scripted_vllm(
        [
            # Without a rubric source the request carries no tools, so a Ready
            # claim can never be satisfied; the reply must say so instead of
            # asking the teacher to retry.
            reply_message("已建立提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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


@pytest.mark.asyncio
async def test_complete_manual_system_info_candidate_reselects_generic_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            # The model first submits a complete-looking manual candidate;
            # the tool rejects it for skipping the available generic capability,
            # then the model resubmits as auto + ai with a complete argv step.
            tool_call_message(
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
            tool_call_message(
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
            reply_message("我已把系統版本查詢整理成提案，請確認後再套用。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    calls, fake_call_vllm = scripted_vllm(
        [
            # First candidate declares auto with a step that is not in the
            # catalog; the tool rejects it with the validated command list.
            tool_call_message(
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
            tool_call_message(
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
            tool_call_message(
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
            reply_message("我已整理成提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="查看學生系統版本")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 1
    assert proposal is None
