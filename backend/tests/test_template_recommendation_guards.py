"""回歸測試：AI 範本推薦對模型輸出與使用者輸入的防護。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.ai import utils as ai_utils
from app.ai.template_recommendation import recommendation_service as svc
from app.ai.template_recommendation.prompt import build_chat_runtime_context
from app.ai.template_recommendation.schemas import (
    ChatMessage,
    ChatRequest,
    RecommendationFormContext,
    RecommendationRequest,
)
from app.exceptions import AppError, BadRequestError, UpstreamServiceError


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def create_chat_completion(
        self, payload: dict[str, Any], *, request_id: str | None = None
    ) -> dict[str, Any]:
        self.calls += 1
        return {"choices": [{"message": {"content": self.content}}], "usage": {}}


@pytest.fixture
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        svc,
        "settings",
        SimpleNamespace(
            VLLM_MODEL_NAME="test-model",
            VLLM_MAX_TOKENS=256,
            VLLM_TEMPERATURE=0.1,
            VLLM_TOP_P=1.0,
            VLLM_TOP_K=5,
            VLLM_MIN_P=0.0,
            VLLM_PRESENCE_PENALTY=0.0,
            VLLM_REPETITION_PENALTY=1.0,
            VLLM_ENABLE_THINKING=False,
        ),
    )


# --- 模型輸出 null／型別錯誤不可變成 500 -------------------------------


def test_normalize_tolerates_null_and_wrongly_typed_fields() -> None:
    result = svc.normalize_ai_result(
        {
            "application_target": None,
            "form_prefill": None,
            "decision_factors": "abc",
            "summary": None,
        },
        RecommendationRequest(goal="架設網站"),
        [],
    )

    target = result["final_plan"]["application_target"]
    assert target["service_name"] == "架設網站"
    assert target["environment_reason"]
    assert result["rule_basis"]["reasons"] == []
    assert result["summary"] == ""


def test_normalize_tolerates_string_form_prefill() -> None:
    result = svc.normalize_ai_result(
        {"form_prefill": "not-an-object"},
        RecommendationRequest(goal="架設網站"),
        [],
    )
    assert result["final_plan"]["form_prefill"]["resource_type"] == "lxc"


def test_immediate_no_end_string_false_keeps_end_time() -> None:
    end = datetime(2026, 10, 1, 12, tzinfo=UTC) + timedelta(days=7)
    request = RecommendationRequest(
        goal="架設網站",
        form_context=RecommendationFormContext(mode="immediate", end_at=end),
    )
    result = svc.normalize_ai_result(
        {"form_prefill": {"mode": "immediate", "immediate_no_end": "false"}},
        request,
        [],
    )
    prefill = result["final_plan"]["form_prefill"]
    assert prefill["end_at"] == end.isoformat()
    assert prefill["immediate_no_end"] is False


def test_immediate_no_end_missing_keeps_default_no_end() -> None:
    end = datetime(2026, 10, 1, 12, tzinfo=UTC)
    request = RecommendationRequest(
        goal="架設網站",
        form_context=RecommendationFormContext(
            mode="immediate", end_at=end, immediate_no_end=True
        ),
    )
    result = svc.normalize_ai_result(
        {"form_prefill": {"mode": "immediate"}}, request, []
    )
    assert result["final_plan"]["form_prefill"]["end_at"] == ""


async def test_generate_ai_plan_rejects_non_object_json(
    monkeypatch: pytest.MonkeyPatch, fake_settings: None
) -> None:
    fake = _FakeClient("[]")
    monkeypatch.setattr(svc, "client", fake)

    with pytest.raises(UpstreamServiceError) as exc_info:
        await svc.generate_ai_plan(RecommendationRequest(goal="架設網站"), [])

    assert exc_info.value.status_code == 502
    assert fake.calls == 1


# --- service 用 AppError、usage_metrics，且不把上游細節回給前端 -----


async def test_generate_ai_plan_missing_model_raises_app_error(
    monkeypatch: pytest.MonkeyPatch, fake_settings: None
) -> None:
    monkeypatch.setattr(svc.settings, "VLLM_MODEL_NAME", "")
    fake = _FakeClient("{}")
    monkeypatch.setattr(svc, "client", fake)

    with pytest.raises(AppError) as exc_info:
        await svc.generate_ai_plan(RecommendationRequest(goal="架設網站"), [])

    assert exc_info.value.status_code == 503
    assert fake.calls == 0


async def test_generate_ai_plan_lets_httpx_errors_reach_the_route(
    monkeypatch: pytest.MonkeyPatch, fake_settings: None
) -> None:
    class _FailingClient:
        async def create_chat_completion(
            self, payload: dict[str, Any], *, request_id: str | None = None
        ) -> dict[str, Any]:
            raise httpx.ConnectError("connect failed: http://litellm:4000/v1")

    monkeypatch.setattr(svc, "client", _FailingClient())

    with pytest.raises(httpx.HTTPError):
        await svc.generate_ai_plan(RecommendationRequest(goal="架設網站"), [])


async def test_generate_ai_plan_hides_raw_parse_error(
    monkeypatch: pytest.MonkeyPatch, fake_settings: None
) -> None:
    monkeypatch.setattr(svc, "client", _FakeClient("not-json SECRET-FRAGMENT"))

    with pytest.raises(UpstreamServiceError) as exc_info:
        await svc.generate_ai_plan(RecommendationRequest(goal="架設網站"), [])

    assert "SECRET-FRAGMENT" not in exc_info.value.message


async def test_generate_ai_plan_metrics_fall_back_to_summed_total_tokens(
    monkeypatch: pytest.MonkeyPatch, fake_settings: None
) -> None:
    class _UsageClient:
        async def create_chat_completion(
            self, payload: dict[str, Any], *, request_id: str | None = None
        ) -> dict[str, Any]:
            return {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 5},
                "model": "served-model",
            }

    monkeypatch.setattr(svc, "client", _UsageClient())

    _, metrics = await svc.generate_ai_plan(
        RecommendationRequest(goal="架設網站"), [], request_id="req-1"
    )

    assert metrics["request_id"] == "req-1"
    assert metrics["prompt_tokens"] == 7
    assert metrics["completion_tokens"] == 5
    assert metrics["total_tokens"] == 12
    assert metrics["usage_reported"] is True
    assert metrics["response_model"] == "served-model"
    assert "tokens_per_second" in metrics
    assert metrics["started_at"] <= metrics["completed_at"]


# --- 表單快照不能繞過 prompt 大小上限 ------------------------------------


def test_form_context_free_text_is_clipped() -> None:
    context = RecommendationFormContext(
        hostname="h" * 10_000, reason="x" * 100_000, storage="s" * 10_000
    )
    assert context.hostname is not None and len(context.hostname) == 255
    assert context.reason is not None and len(context.reason) == 8000
    assert context.storage is not None and len(context.storage) == 255


def test_chat_runtime_context_rejects_oversized_form_snapshot() -> None:
    oversized = {"reason": "x" * (ai_utils.MAX_FORM_CONTEXT_CHARS + 1)}
    with pytest.raises(BadRequestError) as exc_info:
        build_chat_runtime_context(form_context=oversized)
    # 訊息要有翻譯，不能直接把 i18n key 回給使用者
    assert exc_info.value.message != "ai_guard.form_context_too_long"
    assert str(ai_utils.MAX_FORM_CONTEXT_CHARS) in exc_info.value.message


def test_recommendation_form_context_guard_runs_without_model() -> None:
    # 路由在記錄用量的 try 之前呼叫；不需要模型設定就能擋下
    context = RecommendationFormContext(
        reason="x" * 8000,
        schedule_options=[
            {
                "start_at": datetime(2026, 10, 1, 8, tzinfo=UTC),
                "end_at": datetime(2026, 10, 1, 9, tzinfo=UTC),
                "summary": "s" * 500,
                "recommended_nodes": ["n" * 255] * 64,
            }
        ]
        * 12,
    )
    with pytest.raises(BadRequestError):
        svc.ensure_recommendation_form_context_within_limits(
            RecommendationRequest(goal="架設網站", form_context=context)
        )
    # 正常大小與沒有快照都放行
    svc.ensure_recommendation_form_context_within_limits(
        RecommendationRequest(
            goal="架設網站", form_context=RecommendationFormContext(reason="課程網站")
        )
    )
    svc.ensure_recommendation_form_context_within_limits(
        RecommendationRequest(goal="架設網站")
    )


def test_prompt_form_context_excludes_client_option_lists() -> None:
    request = RecommendationRequest(
        goal="架設網站", form_context=RecommendationFormContext(reason="課程網站")
    )
    dumped = svc.prompt_form_context(request)
    assert dumped is not None
    assert dumped["reason"] == "課程網站"
    for key in (
        "gpu_options",
        "lxc_os_options",
        "vm_os_options",
        "resource_options_from_client",
    ):
        assert key not in dumped
    assert svc.prompt_form_context(RecommendationRequest(goal="架設網站")) is None


def test_chat_runtime_context_accepts_normal_form_snapshot() -> None:
    text = build_chat_runtime_context(form_context={"reason": "課程網站"})
    assert "課程網站" in text


async def test_generate_ai_plan_rejects_oversized_form_context_before_model_call(
    monkeypatch: pytest.MonkeyPatch, fake_settings: None
) -> None:
    fake = _FakeClient("{}")
    monkeypatch.setattr(svc, "client", fake)
    # 逐欄截斷後仍可能透過排程選項堆出很大的快照
    start = datetime(2026, 10, 1, 8, tzinfo=UTC)
    context = RecommendationFormContext(
        reason="x" * 8000,
        schedule_options=[
            {
                "start_at": start,
                "end_at": start + timedelta(hours=1),
                "summary": "s" * 500,
                "recommended_nodes": ["n" * 255] * 64,
            }
        ]
        * 12,
    )

    with pytest.raises(BadRequestError):
        await svc.generate_ai_plan(
            RecommendationRequest(goal="架設網站", form_context=context), []
        )

    assert fake.calls == 0


# --- 過短的使用者訊息不可讓 /recommend 變成 500 --------------------------


@pytest.mark.parametrize("text", ["hi", "好的", "vm", "a"])
def test_short_user_text_yields_valid_goal(text: str) -> None:
    intent = svc.infer_intent_from_chat(
        ChatRequest(messages=[ChatMessage(role="user", content=text)])
    )
    assert len(intent.goal_summary) >= 3
    assert text in intent.goal_summary
    RecommendationRequest(goal=intent.goal_summary)


def test_empty_user_text_uses_default_goal() -> None:
    intent = svc.infer_intent_from_chat(
        ChatRequest(messages=[ChatMessage(role="assistant", content="您好")])
    )
    assert intent.goal_summary == "請依目前表單內容提供完整配置建議"


# --- 資源選項 bundle 的 GPU 清單一律由呼叫端依權限填入 -------------------


def test_resource_option_bundle_always_returns_empty_gpu_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai.template_recommendation import node_service

    monkeypatch.setattr(
        node_service.provisioning_service,
        "get_lxc_templates",
        lambda: [SimpleNamespace(volid="local:vztmpl/debian-12.tar.zst")],
    )
    monkeypatch.setattr(
        node_service.provisioning_service,
        "get_vm_templates",
        lambda: [SimpleNamespace(vmid=9000, name="ubuntu", node="pve1")],
    )

    bundle = node_service.build_resource_option_bundle()

    assert bundle["gpu_options"] == []
    assert bundle["lxc_os_images"] == [
        {"value": "local:vztmpl/debian-12.tar.zst", "label": "debian-12"}
    ]
    assert bundle["vm_operating_systems"] == [
        {"template_id": 9000, "label": "ubuntu", "node": "pve1"}
    ]
