"""Split from tests/test_rubric_template_commands.py: attachment item-wise rubric analysis.

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

MULTI_ROW_ATTACHMENT_CONTEXT = (
    "--- 附件：rubric.md ---\n"
    "| 審查重點 | AI 可以參考的線索 |\n"
    "| 確認 Python 版本 | python --version |\n"
    "| 檢查 Port 8080 | Listening ports |\n"
    "| 程式架構品質 | 主觀評分 |\n"
    "--- 附件結束 ---"
)


_ITEMWISE_TITLES = ("確認 Python 版本", "檢查 Port 8080", "程式架構品質")


def _itemwise_ready_tool_call(
    title: str, judgement_mode: str = "ai"
) -> dict[str, object]:
    """Round-1 tool call for one itemwise requirement: create a Ready proposal."""
    parameters: dict[str, object] = {
        "cwd": "/home/student/project",
        "argv": ["python3", "--version"],
        "timeout_seconds": 30,
    }
    if judgement_mode != "teacher":
        parameters["success_criteria"] = "stdout 包含 Python 3"
    return tool_call_message(
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
                    _itemwise_ready_tool_call("程式架構品質", judgement_mode="teacher"),
                    {"total_tokens": 1},
                )
        if payload["messages"][-1]["role"] == "tool":
            return (
                reply_message("已把該需求整理成提案。", "ready"),
                {"total_tokens": 1},
            )
        raise AssertionError("unexpected model call")

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    assert result.item_results[1]["missing_information"] == ["要檢查的服務或連接埠範圍"]
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
                    {
                        "items": [
                            {"title": "第一項"},
                            {"title": "第二項"},
                            {"title": "第三項"},
                        ]
                    },
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
        return (reply_message("已整理成提案。", "ready"), {"total_tokens": 1})

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    patch_teacher_judge_vllm_settings(monkeypatch)

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
    patch_teacher_judge_vllm_settings(monkeypatch)

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
        return (reply_message("已整理成提案。", "ready"), {"total_tokens": 1})

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

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


@pytest.mark.asyncio
async def test_attachment_itemwise_results_carry_source_item_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P3: item_results keep the server-owned id for history dedup tracking."""

    async def fake_call_vllm(payload, timeout=60.0):
        system_prompt = payload["messages"][0]["content"]
        if "評分表拆解器" in system_prompt:
            return (_itemwise_extraction_payload(), {"total_tokens": 1})
        if payload["messages"][-1]["role"] == "user":
            user_content = payload["messages"][-1]["content"]
            for title in _ITEMWISE_TITLES:
                if title in user_content:
                    return (
                        _itemwise_ready_tool_call(title),
                        {"total_tokens": 1},
                    )
        return (reply_message("已整理成提案。", "ready"), {"total_tokens": 1})

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

    result = await teacher_judge_service.analyze_attachments_itemwise(
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
        attachment_context=MULTI_ROW_ATTACHMENT_CONTEXT,
        rubric_available=True,
    )

    assert result.item_results
    for row in result.item_results:
        assert str(row.get("source_item_id") or "").startswith("src-")
    assert len({row["source_item_id"] for row in result.item_results}) == len(
        result.item_results
    )
