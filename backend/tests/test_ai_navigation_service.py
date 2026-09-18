from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.navigation import service as navigation_service
from app.ai.navigation.schemas import NavigationMessage
from app.models.user import UserRole


def _user(role: UserRole, *, is_superuser: bool = False) -> SimpleNamespace:
    return SimpleNamespace(role=role, is_superuser=is_superuser)


def _model_reply(payload_json: str):
    async def _fake_create_chat_completion(_payload, *, timeout: float):
        return {"choices": [{"message": {"content": payload_json}}]}

    return _fake_create_chat_completion


def _use_model(monkeypatch: pytest.MonkeyPatch, payload_json: str) -> list[dict[str, Any]]:
    """Point the service at a stub model and capture the payloads it sends."""
    seen: list[dict[str, Any]] = []

    async def _capture(payload, *, timeout: float):
        seen.append(payload)
        return {"choices": [{"message": {"content": payload_json}}]}

    monkeypatch.setattr(
        navigation_service.system_ai_env, "vllm_model_name", "Qwen/test-model"
    )
    monkeypatch.setattr(
        navigation_service.navigation_client, "create_chat_completion", _capture
    )
    return seen


# ----------------------------------------------------------- 單頁導覽


@pytest.mark.asyncio
async def test_resolve_navigation_uses_keyword_fallback_when_model_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(navigation_service.system_ai_env, "vllm_model_name", "")

    result = await navigation_service.resolve_navigation(
        "我要看 AI API token 用量",
        _user(UserRole.student),
    )

    assert result.primary is not None
    assert result.primary.path == "/ai-api"
    assert result.action in {"navigate", "suggest"}


@pytest.mark.asyncio
async def test_resolve_navigation_filters_out_inaccessible_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_model(
        monkeypatch,
        '{"intent":"看管理頁","confidence":0.91,'
        '"action":"navigate","primary_path":"/audit",'
        '"suggested_paths":["/my-resources"],'
        '"reason":"看管理設定","clarification_question":""}',
    )

    result = await navigation_service.resolve_navigation(
        "我要看管理設定",
        _user(UserRole.student),
    )

    assert result.action == "suggest"
    assert result.primary is not None
    assert result.primary.path == "/my-resources"
    assert all(not item.path == "/audit" for item in result.suggestions)


# ------------------------------------------------------------- 流程導覽


@pytest.mark.asyncio
async def test_whole_task_falls_back_to_a_step_by_step_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型不在時，整件事的描述仍要走完整流程，而不是丟一個頁面。"""
    monkeypatch.setattr(navigation_service.system_ai_env, "vllm_model_name", "")

    result = await navigation_service.resolve_navigation(
        "我要申請一台機器",
        _user(UserRole.student),
    )

    assert result.action == "guide"
    assert result.flow_id == "request_machine"
    assert [step.status for step in result.steps] == [
        "current", "todo", "todo", "todo",
    ]
    # 先把人帶到表單，規劃才拿得到表單上的真實候選（GPU、時段、作業系統）。
    assert result.steps[0].path == "/my-requests"
    assert result.steps[0].state == {"create": True}
    # 第二步才是規劃，而且是就地填進表單，不是導到別頁。
    assert result.steps[1].action == "recommend"


@pytest.mark.asyncio
async def test_visiting_a_page_does_not_complete_previous_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(navigation_service.system_ai_env, "vllm_model_name", "")

    result = await navigation_service.resolve_navigation(
        "我想把網站公開出去",
        _user(UserRole.student),
        current_path="/reverse-proxy",
    )

    assert result.action == "guide"
    assert result.flow_id == "publish_service"
    assert result.active_step == 0
    assert [step.status for step in result.steps] == ["current", "todo", "todo"]


@pytest.mark.asyncio
async def test_model_selected_flow_is_expanded_from_the_server_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_model(
        monkeypatch,
        '{"intent":"開班","confidence":0.93,"action":"guide",'
        '"flow_id":"open_class","primary_path":"","suggested_paths":[],'
        '"reason":"要走完整開班流程","clarification_question":""}',
    )

    result = await navigation_service.resolve_navigation(
        "我想開一個新班級",
        _user(UserRole.teacher),
    )

    assert result.action == "guide"
    assert result.flow_id == "open_class"
    assert [step.path for step in result.steps] == [
        "/class-setup",
        "/class-management",
        "/class-management",
    ]


@pytest.mark.asyncio
async def test_flow_the_user_may_not_use_is_not_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """學生問到教師流程時退回單頁判斷，不能拿到教師的步驟清單。"""
    _use_model(
        monkeypatch,
        '{"intent":"開班","confidence":0.93,"action":"guide",'
        '"flow_id":"open_class","primary_path":"/courses",'
        '"suggested_paths":[],"reason":"","clarification_question":""}',
    )

    result = await navigation_service.resolve_navigation(
        "我想開一個新班級",
        _user(UserRole.student),
    )

    assert result.action != "guide"
    assert result.flow_id is None
    assert not result.steps


@pytest.mark.asyncio
async def test_student_keyword_fallback_never_reaches_a_staff_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(navigation_service.system_ai_env, "vllm_model_name", "")

    result = await navigation_service.resolve_navigation(
        "我要開一個班級",
        _user(UserRole.student),
    )

    assert result.flow_id is None


@pytest.mark.asyncio
async def test_unknown_flow_id_does_not_invent_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_model(
        monkeypatch,
        '{"intent":"做某件事","confidence":0.9,"action":"guide",'
        '"flow_id":"totally_made_up","primary_path":"/my-resources",'
        '"suggested_paths":[],"reason":"","clarification_question":""}',
    )

    result = await navigation_service.resolve_navigation(
        "幫我處理機器的事",
        _user(UserRole.student),
    )

    assert result.action == "suggest"
    assert not result.steps
    assert result.primary is not None
    assert result.primary.path == "/my-resources"


# --------------------------------------------------------------- 記憶


@pytest.mark.asyncio
async def test_history_is_forwarded_to_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _use_model(
        monkeypatch,
        '{"intent":"下一步","confidence":0.9,"action":"navigate",'
        '"primary_path":"/my-resources","suggested_paths":[],'
        '"reason":"","clarification_question":""}',
    )

    await navigation_service.resolve_navigation(
        "然後呢？",
        _user(UserRole.student),
        history=[
            NavigationMessage(role="user", content="我要申請一台機器"),
            NavigationMessage(role="assistant", content="先去填申請單"),
        ],
    )

    messages = seen[0]["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[1]["content"] == "我要申請一台機器"
    assert messages[-1]["content"] == "然後呢？"


@pytest.mark.asyncio
async def test_blank_history_entries_are_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _use_model(
        monkeypatch,
        '{"intent":"x","confidence":0.9,"action":"navigate",'
        '"primary_path":"/my-resources","suggested_paths":[],'
        '"reason":"","clarification_question":""}',
    )

    await navigation_service.resolve_navigation(
        "帶我到我的機器",
        _user(UserRole.student),
        history=[NavigationMessage(role="user", content="   ")],
    )

    assert len(seen[0]["messages"]) == 2


@pytest.mark.asyncio
async def test_current_path_is_given_to_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _use_model(
        monkeypatch,
        '{"intent":"x","confidence":0.9,"action":"navigate",'
        '"primary_path":"/my-resources","suggested_paths":[],'
        '"reason":"","clarification_question":""}',
    )

    await navigation_service.resolve_navigation(
        "這裡可以做什麼",
        _user(UserRole.student),
        current_path="/my-requests",
    )

    assert "/my-requests" in seen[0]["messages"][0]["content"]
