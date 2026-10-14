"""導覽 AI 推薦配置：依記憶產生合理配置、不虛構 GPU／機型、失敗時退回規則配置。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.ai.template_recommendation import options_service
from app.ai.template_recommendation import recommendation_service as svc
from app.ai.template_recommendation.chat_service import (
    build_chat_adherence_facts,
    build_chat_payload,
)
from app.ai.template_recommendation.schemas import (
    ChatMessage,
    ChatRequest,
    IntakeFactsContext,
    RecommendationRequest,
)
from app.api.routes import ai_template_recommendation as route

_GPU = {
    "mapping_id": "gpu-a",
    "description": "NVIDIA L4",
    "model": "L4",
    "available_count": 1,
}
_RESOURCES: dict[str, Any] = {
    "lxc_os_images": [
        {"value": "local:vztmpl/debian-12.tar.zst", "label": "debian-12"}
    ],
    "vm_operating_systems": [
        {"template_id": 9001, "label": "Windows 11"},
        {"template_id": 9002, "label": "Ubuntu 24.04 Desktop"},
    ],
    "application_templates": [
        {
            "template_id": 300,
            "name": "WordPress",
            "description": "WordPress + MariaDB",
            "resource_type": "lxc",
            "cores": 2,
            "memory_mb": 2048,
            "disk_gb": 16,
        }
    ],
    "gpu_options": [_GPU],
}


def _chat(*texts: str, facts: IntakeFactsContext | None = None) -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage(role="user", content=text) for text in texts],
        intake_facts=facts,
    )


def _memory(goal: str, facts: list[str], latest: str) -> str:
    """與前端 withTaskMemory 相同的格式。"""
    return f"原始目標：{goal}\n" + "\n".join(facts) + f"\n本輪要求：{latest}"


# --- 需求判斷：配置模式的答案要真的被用上 ------------------------------------


def test_unsure_gpu_with_ai_training_purpose_requires_gpu() -> None:
    facts = IntakeFactsContext(purpose="AI 訓練", gpu="不確定，你幫我判斷")
    intent = svc.infer_intent_from_chat(_chat("我想做專題", facts=facts))
    assert intent.requires_gpu is True


def test_unsure_gpu_with_web_purpose_does_not_require_gpu() -> None:
    facts = IntakeFactsContext(purpose="架設網站", gpu="不確定，你幫我判斷")
    intent = svc.infer_intent_from_chat(_chat("我要架網站", facts=facts))
    assert intent.requires_gpu is False


def test_graphical_desktop_answer_sets_desktop_not_windows() -> None:
    facts = IntakeFactsContext(display="需要圖形桌面")
    intent = svc.infer_intent_from_chat(_chat("期末專題", facts=facts))
    assert intent.needs_desktop is True
    assert intent.needs_windows is False


def test_windows_answer_sets_windows_and_desktop() -> None:
    facts = IntakeFactsContext(display="需要 Windows")
    intent = svc.infer_intent_from_chat(_chat("上課要用", facts=facts))
    assert intent.needs_windows is True
    assert intent.needs_desktop is True


def test_later_gpu_refusal_in_memory_block_turns_gpu_off() -> None:
    # 原始目標提過 GPU，之後改口：只看第一次出現會永遠關不掉
    memory = _memory(
        "想用 GPU 跑模型", ["加速需求（使用者已提供）：不需要 GPU"], "直接產生配置"
    )
    intent = svc.infer_intent_from_chat(_chat("想用 GPU 跑模型", memory))
    assert intent.requires_gpu is False


def test_gpu_workload_hint_without_gpu_keyword() -> None:
    intent = svc.infer_intent_from_chat(_chat("我要跑 AI 訓練，顯示卡越大越好"))
    assert intent.requires_gpu is True


# --- 配置說明不可出現清單外的 GPU 或機型 ---------------------------------------


def test_summary_is_server_rendered_and_ignores_model_hardware_claims() -> None:
    result = svc.normalize_ai_result(
        {
            "summary": "建議使用 RTX 4090 與 Dell R760 伺服器",
            "application_target": {
                "service_name": "網站",
                "environment_reason": "因為 RTX 4090 最快",
            },
            "form_prefill": {"resource_type": "lxc", "cores": 2},
        },
        RecommendationRequest(goal="架設網站"),
        [],
        resource_options=_RESOURCES,
    )
    plan = result["final_plan"]
    for text in (
        result["summary"],
        plan["summary"],
        plan["application_target"]["environment_reason"],
    ):
        assert "RTX" not in text
        assert "Dell" not in text
    assert "debian-12" in result["summary"]
    assert result["fallback"] is False


def test_model_gpu_choice_is_ignored_when_gpu_not_required() -> None:
    result = svc.normalize_ai_result(
        {"form_prefill": {"resource_type": "vm", "gpu_mapping_id": "gpu-a"}},
        RecommendationRequest(goal="架設網站"),
        [],
        resource_options=_RESOURCES,
    )
    assert result["final_plan"]["form_prefill"]["gpu_mapping_id"] == ""
    assert "L4" not in result["summary"]


def test_required_gpu_with_available_card_forces_vm_and_names_real_gpu() -> None:
    result = svc.normalize_ai_result(
        {"form_prefill": {"resource_type": "lxc"}},
        RecommendationRequest(goal="訓練 YOLO 模型", requires_gpu=True),
        [],
        resource_options=_RESOURCES,
    )
    prefill = result["final_plan"]["form_prefill"]
    assert prefill["resource_type"] == "vm"
    assert prefill["gpu_mapping_id"] == "gpu-a"
    assert "NVIDIA L4" in result["summary"]
    # 沒指定 VM 來源時不要盲選第一個（Windows）
    assert prefill["vm_os_choice"] == "Ubuntu 24.04 Desktop"


def test_required_gpu_without_available_card_says_so() -> None:
    resources = {**_RESOURCES, "gpu_options": [{**_GPU, "available_count": 0}]}
    result = svc.normalize_ai_result(
        {},
        RecommendationRequest(goal="訓練 YOLO 模型", requires_gpu=True),
        [],
        resource_options=resources,
    )
    assert result["final_plan"]["form_prefill"]["gpu_mapping_id"] == ""
    assert svc.t("recommendation.summary.gpuUnavailable") in result["summary"]


# --- 規則配置 ------------------------------------------------------------------


def test_rule_based_plan_picks_matching_application_template() -> None:
    request = RecommendationRequest(goal="我要架 WordPress 部落格")
    result = svc.normalize_ai_result(
        svc.rule_based_ai_result(request, resource_options=_RESOURCES),
        request,
        [],
        resource_options=_RESOURCES,
        fallback=True,
    )
    prefill = result["final_plan"]["form_prefill"]
    assert prefill["resource_type"] == "lxc"
    assert prefill["lxc_template_id"] == 300
    assert prefill["disk_gb"] >= 16
    assert "WordPress" in result["summary"]
    assert svc.t("recommendation.summary.fallback") in result["summary"]


def test_rule_based_plan_for_windows_uses_windows_vm() -> None:
    request = RecommendationRequest(goal="上課要用 Office", needs_windows=True)
    result = svc.normalize_ai_result(
        svc.rule_based_ai_result(request, resource_options=_RESOURCES),
        request,
        [],
        resource_options=_RESOURCES,
        fallback=True,
    )
    prefill = result["final_plan"]["form_prefill"]
    assert prefill["resource_type"] == "vm"
    assert prefill["vm_template_id"] == 9001
    assert prefill["memory_mb"] >= 4096


# --- 路由：模型失敗時回規則配置，不是錯誤 --------------------------------------


class _SettingsWithModel:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    VLLM_MODEL_NAME = "test-model"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("timed out"),
        svc.UpstreamServiceError("bad json"),
    ],
)
async def test_recommend_returns_rule_based_plan_when_planner_fails(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.setattr(route, "settings", _SettingsWithModel(route.settings))
    monkeypatch.setattr(
        options_service, "resolve_recommend_gpu_options", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        options_service, "resolve_resource_options", lambda *_a, **_k: _RESOURCES
    )

    async def live_nodes() -> list[Any]:
        return []

    async def failing_plan(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    records: list[dict[str, Any]] = []

    async def record(**kwargs: Any) -> None:
        records.append(kwargs)

    monkeypatch.setattr(options_service, "get_live_device_nodes_safely", live_nodes)
    monkeypatch.setattr(route, "generate_ai_plan", failing_plan)
    monkeypatch.setattr(route, "_record_template_call", record)

    request = _chat(
        "我想架 WordPress",
        facts=IntakeFactsContext(purpose="架設網站", gpu="不需要 GPU"),
    )
    result = await route.recommend(
        request=request, current_user=SimpleNamespace(id="u1"), session=object()
    )

    assert result["fallback"] is True
    assert result["final_plan"]["form_prefill"]["lxc_template_id"] == 300
    assert [item["status"] for item in records] == ["error"]


async def test_recommend_still_raises_client_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(route, "settings", _SettingsWithModel(route.settings))
    monkeypatch.setattr(
        options_service, "resolve_recommend_gpu_options", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        options_service, "resolve_resource_options", lambda *_a, **_k: _RESOURCES
    )

    async def bad_request(*_args: Any, **_kwargs: Any) -> Any:
        raise svc.AppError("too long", 400)

    async def record(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(route, "generate_ai_plan", bad_request)
    monkeypatch.setattr(route, "_record_template_call", record)
    with pytest.raises(svc.AppError):
        await route.recommend(
            request=_chat("我要架網站"),
            current_user=SimpleNamespace(id="u1"),
            session=object(),
        )


# --- 一般問答：一律附上平台清單 ------------------------------------------------


def test_chat_payload_always_lists_inventory_and_empty_gpu() -> None:
    payload = build_chat_payload(
        _chat("有哪些作業系統可以選？"),
        gpu_options=[],
        model_name="test-model",
        resource_options=_RESOURCES,
        inventory_loaded=True,
    )
    system = payload["messages"][0]["content"]
    assert "(none available)" in system
    assert "debian-12" in system
    assert "Windows 11" in system
    assert "WordPress" in system
    assert "No Service Template Rule" not in system


def test_chat_payload_marks_failed_lookups_as_unconfirmed() -> None:
    payload = build_chat_payload(
        _chat("還有 GPU 嗎？"),
        gpu_options=None,
        model_name="test-model",
        resource_options=None,
        inventory_loaded=True,
    )
    system = payload["messages"][0]["content"]
    assert "GPU availability is 待確認" in system
    assert "availability is 待確認" in system


def test_chat_checker_sees_same_inventory() -> None:
    facts = build_chat_adherence_facts(
        _chat("有哪些範本？"), gpu_options=[_GPU], resource_options=_RESOURCES
    )
    inventory = facts["evidence"]["platform_inventory"]
    assert inventory["application_templates"][0]["name"] == "WordPress"
    assert inventory["lxc_os_images"] == ["debian-12"]


# --- 英數關鍵字整字比對 ----------------------------------------------------------


def test_wordpress_is_not_mistaken_for_remote_desktop() -> None:
    intent = svc.infer_intent_from_chat(_chat("我想架 WordPress 網站"))
    assert intent.needs_windows is False
    assert intent.needs_desktop is False


def test_intake_does_not_treat_wordpress_as_display_answer() -> None:
    from app.ai.navigation.intake import read_intake
    from app.ai.navigation.schemas import NavigationMessage

    state = read_intake(
        [NavigationMessage(role="user", content="我要架 WordPress 網站")]
    )
    assert state.facts.display == "Linux 指令列就好"
    assert "display" in state.facts.inferred


def test_keyword_spans_match_whole_ascii_words_only() -> None:
    from app.ai.utils import keyword_spans

    assert keyword_spans("WordPress", "rdp") == []
    assert keyword_spans("用 RDP 連線", "rdp") == [(2, 5)]
    assert keyword_spans("mongodb", "db") == []
    assert keyword_spans("跑yolo模型", "yolo") == [(1, 5)]
    assert keyword_spans("要用顯卡也要顯卡", "顯卡") == [(2, 4), (6, 8)]


async def test_recommend_still_plans_when_pve_option_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(route, "settings", _SettingsWithModel(route.settings))
    monkeypatch.setattr(
        options_service, "resolve_recommend_gpu_options", lambda *_a, **_k: []
    )

    def pve_down(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise svc.AppError("Proxmox unreachable", 502)

    async def live_nodes() -> list[Any]:
        return []

    async def failing_plan(*_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectError("vLLM down")

    async def record(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(options_service, "resolve_resource_options", pve_down)
    monkeypatch.setattr(options_service, "get_live_device_nodes_safely", live_nodes)
    monkeypatch.setattr(route, "generate_ai_plan", failing_plan)
    monkeypatch.setattr(route, "_record_template_call", record)

    result = await route.recommend(
        request=_chat("我要架網站"),
        current_user=SimpleNamespace(id="u1"),
        session=object(),
    )

    prefill = result["final_plan"]["form_prefill"]
    assert result["fallback"] is True
    assert prefill["resource_type"] == "lxc"
    assert prefill["cores"] >= 1
    assert svc.t("recommendation.summary.sourceMissing") in result["summary"]
