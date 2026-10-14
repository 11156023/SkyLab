"""Coverage tests for app.ai.navigation.service fallback branches."""

from types import SimpleNamespace

import pytest

from app.ai.navigation import service as navigation_service
from app.models.user import UserRole


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=None, role=UserRole.student, is_superuser=False)


def test_render_candidates_with_unknown_kind_falls_back() -> None:
    result = navigation_service._render_candidates(
        ("x",), {"x": ("mystery", None)}, query="q"
    )
    assert result.action == "clarify"
    assert result.clarification_question == navigation_service.NAVIGATION_FALLBACK


def _state(**values: str) -> dict:
    return {f"coursetpl.{key}": {"value": value} for key, value in values.items()}


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ({"surface": {"id": "other"}}, None),
        (
            {"surface": {"id": "course-template-new"}, "state": {}},
            "請等環境資料載入完成。",
        ),
        (
            {
                "surface": {"id": "course-template-editor"},
                "state": _state(status="archived"),
            },
            "目前不是草稿，無法修改機器配置。",
        ),
        (
            {
                "surface": {"id": "course-template-editor"},
                "state": _state(status="draft"),
            },
            "請先在「基本資料」確認環境名稱。",
        ),
    ],
)
def test_environment_next_step_branches(context, expected) -> None:
    assert navigation_service._environment_next_step(context) == expected


async def test_blank_query_asks_for_a_request() -> None:
    result = await navigation_service.resolve_navigation("   ", _user())
    assert result.action == "clarify"
    assert result.clarification_question == "請先輸入你目前想完成的需求。"


async def test_no_allowed_routes_asks_to_check_permissions(monkeypatch) -> None:
    monkeypatch.setattr(navigation_service, "get_routes_for_user", lambda _u: [])
    result = await navigation_service.resolve_navigation("看用量", _user())
    assert result.clarification_question == "目前沒有可導覽的頁面，請先確認帳號權限。"


@pytest.mark.parametrize(
    "choice",
    [
        {"finish_reason": "length", "message": {"content": "{}"}},
        {"message": {"content": "{}", "tool_calls": [{"id": "t"}]}},
    ],
)
async def test_truncated_or_tool_call_decision_uses_fallback(
    monkeypatch, choice
) -> None:
    async def _respond(_payload, *, profile, timeout, request_id=None):
        del profile, timeout, request_id
        return {"choices": [choice]}

    monkeypatch.setattr(
        navigation_service.system_ai_env, "vllm_model_name", "Qwen/test-model"
    )
    monkeypatch.setattr(
        navigation_service.navigation_client, "create_chat_completion", _respond
    )
    result = await navigation_service.resolve_navigation("我要看用量", _user())
    assert result.action == "clarify"
    assert result.clarification_question == navigation_service.NAVIGATION_FALLBACK
