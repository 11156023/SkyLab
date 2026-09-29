"""Teacher Judge regressions: reply-payload parsing, focus coercion, recovered-title check."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.ai.teacher_judge import service as teacher_judge_service
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricCheckStep,
    TeacherJudgeRubricItem,
)
from tests.ai.teacher_judge.helpers import (
    patch_teacher_judge_vllm_settings,
    reply_message,
    scripted_vllm,
    tool_call_message,
)

# --- flat argv proposals are not "recovered" --------------------------


def _flat_raw(title: str) -> dict[str, object]:
    return {
        "id": "item-1",
        "title": title,
        "detectable": "auto",
        "detection_method": "執行 python3 --version 取得版本。",
        "check_steps": [{"argv": ["python3", "--version"]}],
    }


def test_flat_argv_item_is_not_counted_as_recovered() -> None:
    raw = _flat_raw("檢查 Python 版本")
    items = teacher_judge_service._normalize_rubric_items(
        [raw], template_key="linux", template_commands=[]
    )
    assert items and items[0].detectable == "auto"
    assert all(step.command_key is None for step in items[0].check_steps)

    assert teacher_judge_service._recovered_catalog_item_titles(items, [raw]) == []


def test_unknown_command_key_recovered_into_general_command_is_flagged() -> None:
    item = TeacherJudgeRubricItem(
        id="item-1",
        title="檢查 nginx 設定",
        detectable="auto",
        detection_method="執行 nginx -t",
        check_steps=[
            TeacherJudgeRubricCheckStep(
                template_key="linux",
                command_key="system.run_command",
                parameters={"argv": ["nginx", "-t"], "timeout_seconds": 30},
            )
        ],
    )
    raw = {
        "id": "item-1",
        "title": "檢查 nginx 設定",
        "detectable": "auto",
        "check_steps": [
            {
                "template_key": "linux",
                "command_key": "nginx.check_config",
                "parameters": {"argv": ["nginx", "-t"]},
            }
        ],
    }

    assert teacher_judge_service._recovered_catalog_item_titles([item], [raw]) == [
        "檢查 nginx 設定"
    ]


@pytest.mark.asyncio
async def test_flat_argv_proposal_keeps_model_reply_about_other_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_reply = (
        "已把「檢查 Python 版本」整理成提案；「檢查 nginx 設定」還需要設定檔完整路徑。"
    )
    _calls, fake_call_vllm = scripted_vllm(
        [
            tool_call_message(
                "create_checklist_item",
                {
                    "title": "檢查 Python 版本",
                    "checked": False,
                    "detectable": "auto",
                    "detection_method": "執行 python3 --version 取得版本。",
                    "check_steps": [{"argv": ["python3", "--version"]}],
                },
            ),
            reply_message(model_reply, "ready"),
        ]
    )
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[
            SimpleNamespace(
                role="user",
                content="檢查 Python 版本，另外檢查 nginx 設定。",
            )
        ],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[],
        rubric_available=True,
    )

    assert proposal is not None and len(proposal) == 1
    assert "設定檔完整路徑" in reply


# --- reply-payload extraction ----------------------------------------


def test_reply_payload_object_keeps_trailing_prose_intact() -> None:
    leftover, parsed = teacher_judge_service._reply_payload_object(
        '前言 {"proposal_status":"needs_information"} 請補充 /etc/nginx 路徑'
    )
    assert parsed == {"proposal_status": "needs_information"}
    assert "請補充 /etc/nginx 路徑" in leftover
    assert leftover.startswith("前言")


def test_parse_chat_reply_payload_never_returns_raw_json() -> None:
    reply, status = teacher_judge_service._parse_chat_reply_payload(
        '{"reply":"","proposal_status":"none"}'
    )
    assert "proposal_status" not in reply
    assert reply == ""
    assert status == "none"


@pytest.mark.asyncio
async def test_empty_payload_reply_gets_neutral_sentence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _calls, fake_call_vllm = scripted_vllm([reply_message("", "none")])
    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="你好")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[],
        rubric_available=True,
    )

    assert proposal is None
    assert reply.strip()
    assert "proposal_status" not in reply


# --- conversation_focus type coercion --------------------------------


def test_conversation_focus_coerces_string_and_scalar_fields() -> None:
    content = json.dumps(
        {
            "reply": "x",
            "proposal_status": "needs_information",
            "conversation_focus": {
                "turn_kind": "requirement",
                "requirements": [
                    {
                        "focus_key": "nginx",
                        "status": "needs_information",
                        "missing_information": "設定檔路徑",
                        "known_information": True,
                    }
                ],
            },
        },
        ensure_ascii=False,
    )

    focus = teacher_judge_service._conversation_focus_from_content(
        content, proposal=None
    )

    assert focus is not None
    requirement = focus["requirements"][0]
    assert requirement["missing_information"] == ["設定檔路徑"]
    assert requirement["known_information"] == []
    assert requirement["status"] == "needs_information"


def test_structured_requirement_helpers_tolerate_non_list_fields() -> None:
    numeric_gap = json.dumps(
        {
            "reply": "x",
            "proposal_status": "ready",
            "conversation_focus": {
                "turn_kind": "requirement",
                "requirements": [
                    {"focus_key": "a", "status": "ready", "missing_information": 3}
                ],
            },
        }
    )
    assert teacher_judge_service._structured_requirement_needs_candidate(numeric_gap)

    scalar_requirements = json.dumps(
        {
            "reply": "x",
            "proposal_status": "ready",
            "conversation_focus": {"turn_kind": "requirement", "requirements": 5},
        }
    )
    assert (
        teacher_judge_service._structured_requirement_target_item(scalar_requirements)
        is None
    )
