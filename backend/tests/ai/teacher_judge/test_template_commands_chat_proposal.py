"""Split from tests/test_rubric_template_commands.py: chat proposal core (tools, partial success, new/existing items)."""

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


@pytest.mark.asyncio
async def test_proposal_canonicalizes_executor_and_peer_p_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
                "create_checklist_item",
                {
                    "title": "P2 可連通 P1",
                    "target_node_key": "P2",
                    "peer_node_key": "P1",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "由 P2 執行 ping 觀察 P1。",
                    "check_steps": [
                        {
                            "argv": ["ping", "-c", "4", "{{peer.ip}}"],
                            "timeout_seconds": 30,
                        }
                    ],
                },
            ),
            reply_message("已整理成提案。請確認後套用。", "ready"),
        ]
    )
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="在 P2 ping P1")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
        machine_entries=[
            {"display_label": "P1", "node_key": "web"},
            {"display_label": "P2", "node_key": "db"},
        ],
        rubric_available=True,
    )

    assert len(calls) == 2
    assert proposal is not None
    assert proposal[0]["target_node_key"] == "db"
    assert proposal[0]["peer_node_key"] == "web"
    assert proposal[0]["check_steps"][0]["argv"][-1] == "{{peer.ip}}"


@pytest.mark.asyncio
async def test_uncatalogued_tool_with_complete_argv_still_forms_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
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
            reply_message(
                "「檢查 jq 工具版本」已整理成提案。"
                "系統會確認指令可以執行；請先查看提案，確認後再套用。",
                "ready",
            ),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
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
            reply_message("已整理成提案。請確認後套用。", "ready"),
        ]
    )
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    calls, fake_call_vllm = scripted_vllm(
        [reply_message("請先選擇檢查表來源。", "none")]
    )
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
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
            tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 Web 服務",
                    "detectable": "partial",
                    "judgement_mode": "ai",
                    "missing_information": ["Port 號"],
                },
            ),
            reply_message("兩個提案都已建立完成。", "ready"),
        ]
    )
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
        entry for entry in result.tool_calls or [] if entry.get("status") == "rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0]["title"] == "檢查 Web 服務"
    assert rejected[0]["reason"]


@pytest.mark.asyncio
async def test_partial_failure_note_skips_titles_staged_after_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 Web 服務",
                    "detectable": "partial",
                    "judgement_mode": "ai",
                    "missing_information": ["Port 號"],
                },
            ),
            tool_call_message(
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
            reply_message("提案已建立完成。", "ready"),
        ]
    )
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
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
            reply_message("提案已建立完成。", "ready"),
        ]
    )
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 Web 服務",
                    "detectable": "partial",
                    "judgement_mode": "ai",
                    "missing_information": ["服務名稱與 Port 號"],
                },
            ),
            reply_message("還需要服務名稱與 Port 號。", "needs_information"),
        ]
    )
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
        entry for entry in result.tool_calls or [] if entry.get("status") == "rejected"
    ]
    assert len(rejected) == 1
    reason = str(rejected[0]["reason"])
    assert "目前無法形成可套用的提案" in reason
    assert "請改在 reply 中說明缺少的內容" in reason
    assert "可由你自行補齊" not in reason


@pytest.mark.asyncio
async def test_explicit_env_assignment_forms_proposal_when_model_claims_missing_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
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
            reply_message(
                "「檢查 .env 檔案內容」已整理成提案。"
                "系統會確認指定設定行是否存在；請先查看提案，確認後再套用。",
                "ready",
            ),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
                "get_checklist_item",
                {"id": "item-1"},
            ),
            tool_call_message(
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
            reply_message("已更新", "ready"),
        ],
    )

    async def capture_call_vllm(payload, timeout=60.0):
        captured_payload.update(payload)
        return await fake_call_vllm(payload, timeout=timeout)

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", capture_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
async def test_new_item_proposal_does_not_load_current_rubric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
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
            reply_message("已把新的檔案檢查整理成提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    assert "既有機密項目" not in json.dumps(calls[0]["messages"], ensure_ascii=False)
    assert proposal is not None
    assert proposal[0]["id"].startswith("item-")
    assert proposal[0]["operation"] == "add"


@pytest.mark.asyncio
async def test_existing_item_update_loads_current_rubric_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
                "get_checklist_item",
                {"id": "item-port"},
            ),
            tool_call_message(
                "edit_checklist_item",
                {
                    "id": "item-port",
                    "detection_method": "檢查指定 Port 8080。",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["ss", "-ltn"],
                                "timeout_seconds": 30,
                            },
                        }
                    ],
                },
            ),
            reply_message("已把 Port 調整整理成提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)
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
async def test_existing_item_proposal_without_tool_is_retried_with_forced_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            # The model tries to edit an existing item without reading it first;
            # the tool rejects the call and the model recovers by reading.
            tool_call_message(
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
            tool_call_message("get_checklist_item", {"id": "item-existing"}),
            tool_call_message(
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
            reply_message("已整理修改提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
async def test_boolean_detectable_and_flat_generic_argv_form_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake_call_vllm = scripted_vllm(
        [
            # Legacy-style tool arguments: boolean detectable and a flat
            # check_step with argv directly on the step object.
            tool_call_message(
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
            reply_message("我已把 Python 版本檢查整理成提案。", "ready"),
        ],
    )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
