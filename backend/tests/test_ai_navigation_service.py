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
        "/class-setup",
        "/class-setup",
        "/class-setup",
        "/class-setup",
    ]
    assert "已發布" in result.steps[2].detail
    assert "容量" in result.steps[4].detail


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


@pytest.mark.asyncio
async def test_multiple_flows_and_side_answer_are_preserved_and_validated(monkeypatch):
    _use_model(monkeypatch, '{"action":"guide","flow_ids":["share_template","prepare_environment","open_class","open_class","fake","review_requests"],"answer":"可以重用已發布環境。"}')
    result = await navigation_service.resolve_navigation("先做範本、建立教學環境再開班，可以重用嗎？", _user(UserRole.teacher))
    assert [f.flow_id for f in result.flows] == ["share_template", "prepare_environment", "open_class"]
    assert result.answer == "可以重用已發布環境。"
    assert result.flow_id == "share_template"


@pytest.mark.asyncio
async def test_explanation_does_not_restart_active_flow(monkeypatch):
    _use_model(monkeypatch, '{"action":"answer","flow_ids":["open_class"],"answer":"機器範本是教學環境的來源之一。"}')
    result = await navigation_service.resolve_navigation("機器範本跟班級的關係？", _user(UserRole.teacher), active_flow_id="open_class")
    assert result.action == "answer"
    assert not result.flows
    assert not result.steps


@pytest.mark.asyncio
async def test_offline_multiple_teaching_tasks_keep_requested_order(monkeypatch):
    monkeypatch.setattr(navigation_service.system_ai_env, "vllm_model_name", "")
    result = await navigation_service.resolve_navigation("先建立範本，再建立教學環境，最後開班", _user(UserRole.teacher))
    assert [f.flow_id for f in result.flows] == ["share_template", "prepare_environment", "open_class"]
    assert result.answer is None  # No unsolicited relationship essay before the steps.


@pytest.mark.asyncio
async def test_screen_context_keeps_wizard_state_and_drops_unknown_fields(monkeypatch):
    from app.ai.contextual_help.schemas import ElementState

    seen = _use_model(monkeypatch, '{"action":"answer","answer":"在第 3 步選教學環境。"}')
    await navigation_service.resolve_navigation("下一步", _user(UserRole.teacher),
        current_path="/class-setup?classId=42&step=3", surface_id="class-setup",
        screen_state={"classsetup.current_step": ElementState(value="3. 教學環境"), "secret": ElementState(value="never-send-this")},
        active_flow_id="open_class", pending_flow_ids=["share_template", "review_requests", "fake"])
    prompt = seen[0]["messages"][0]["content"]
    assert "3. 教學環境" in prompt
    assert "never-send-this" not in prompt
    assert '"pending_flow_ids": ["share_template"]' in prompt


def test_screen_context_rejects_wrong_page_and_sensitive_values():
    from app.ai.contextual_help.schemas import ElementState

    user = _user(UserRole.student)
    assert navigation_service._screen_context(user, "/class-setup", "class-setup", {}) == {}
    assert navigation_service._screen_context(user, "/my-resources", "request-form", {}) == {}
    context = navigation_service._screen_context(user, "/my-requests", "request-form", {"request.password": ElementState(value="secret")})
    assert "request.password" not in context["state"]


@pytest.mark.asyncio
async def test_offline_continue_uses_current_wizard_step_without_completing_it(monkeypatch):
    from app.ai.contextual_help.schemas import ElementState

    monkeypatch.setattr(navigation_service.system_ai_env, "vllm_model_name", "")
    result = await navigation_service.resolve_navigation("下一步", _user(UserRole.teacher),
        current_path="/class-setup?classId=42&step=3", surface_id="class-setup",
        screen_state={"classsetup.current_step": ElementState(value="3. 教學環境")}, active_flow_id="open_class")
    assert result.action == "answer"
    assert "已發布環境" in result.answer
    assert not result.steps


@pytest.mark.asyncio
async def test_environment_creation_targets_editor_tabs_not_list_or_class(monkeypatch):
    monkeypatch.setattr(navigation_service.system_ai_env, "vllm_model_name", "")
    result = await navigation_service.resolve_navigation("我要建立教學環境", _user(UserRole.teacher))
    assert result.flow_id == "prepare_environment"
    assert [step.path for step in result.steps] == ["/course-template-management/new"] * 3
    assert [step.state["environmentTab"] for step in result.steps] == ["basic", "machines", "machines"]
    assert "套用方式" in result.steps[0].detail
    assert "鎖定" in result.steps[-1].detail


@pytest.mark.asyncio
@pytest.mark.parametrize(("values", "expected"), [
    ({"status": "draft", "tab": "basic", "name": ""}, "環境名稱"),
    ({"status": "draft", "tab": "basic", "name": "Linux"}, "查看機器配置"),
    ({"status": "draft", "tab": "machines", "name": "Linux", "node_count": "0"}, "目前還沒有機器"),
    ({"status": "draft", "tab": "machines", "name": "Linux", "node_count": "2"}, "發布並鎖定"),
    ({"status": "published", "usage_scope": "quick_practice", "return_to_class": "false"}, "可供快速練習使用"),
    ({"status": "published", "usage_scope": "course", "return_to_class": "true"}, "返回原班級"),
])
async def test_continue_environment_uses_real_editor_state(monkeypatch, values, expected):
    from app.ai.contextual_help.schemas import ElementState

    seen = _use_model(monkeypatch, '{"action":"guide","flow_id":"prepare_environment"}')
    result = await navigation_service.resolve_navigation("下一步", _user(UserRole.teacher),
        current_path="/course-template-management/env-42?tab=machines", surface_id="course-template-editor",
        screen_state={f"coursetpl.{key}": ElementState(value=value) for key, value in values.items()},
        active_flow_id="prepare_environment")
    assert result.action == "answer"
    assert expected in result.answer
    assert not result.steps
    assert not seen  # The supplied state already determines the answer.


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "expected"), [
    ("建立課程", "open_class"),
    ("建立課堂", "open_class"),
    ("建立班級", "open_class"),
    ("我想開一個新班級", "open_class"),
    ("幫我建立一門課程", "open_class"),
    ("我要開課", "open_class"),
    ("建立教學環境", "prepare_environment"),
    ("建立課程環境", "prepare_environment"),
    ("建立環境", "prepare_environment"),
])
async def test_explicit_creation_overrides_old_environment_conversation(monkeypatch, query, expected):
    seen = _use_model(monkeypatch, '{"action":"answer","answer":"先完成環境，請選 LXC 或 VM。"}')
    result = await navigation_service.resolve_navigation(query, _user(UserRole.teacher),
        current_path="/course-template-management/new", active_flow_id="prepare_environment",
        history=[NavigationMessage(role="assistant", content="正在建立教學環境，請選 LXC 或 VM。")])
    assert result.flow_id == expected
    assert result.answer is None
    assert not seen


@pytest.mark.asyncio
async def test_explicit_creation_still_checks_permissions(monkeypatch):
    monkeypatch.setattr(navigation_service.system_ai_env, "vllm_model_name", "")
    result = await navigation_service.resolve_navigation("建立課程", _user(UserRole.student))
    assert not result.flows


def test_all_workflow_cards_keep_details_short():
    from app.ai.navigation.flows import all_flows

    for flow in all_flows():
        for step in flow.steps:
            assert len(step.detail) <= 28, (flow.flow_id, step.title)
