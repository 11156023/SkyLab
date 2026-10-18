"""Coverage for app.ai.template_recommendation recommendation_service/prompt/schemas/chat_service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.ai.template_recommendation import chat_service, prompt
from app.ai.template_recommendation.recommendation_service import (
    normalize_ai_result,
)
from app.ai.template_recommendation.schemas import (
    ChatMessage,
    ChatRequest,
    RecommendationRequest,
)


def _chat(*texts: str, focus_hint: str | None = None) -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage(role="user", content=t) for t in texts],
        focus_hint=focus_hint,
    )


def _vm_options() -> dict[str, Any]:
    return {
        "lxc_os_images": [],
        "vm_operating_systems": [{"template_id": 9000, "label": "ubuntu"}],
        "gpu_options": [
            {"mapping_id": "g-busy", "available_count": 0},
            {
                "mapping_id": "g-desc",
                "description": "RTX 4090",
                "available_count": 0,
            },
        ],
    }


def test_normalize_invalid_type_hostname_mode_and_gpu_insufficient() -> None:
    request = RecommendationRequest(
        goal="需要 GPU 的課程", requires_gpu=True, needs_windows=True
    )
    result = normalize_ai_result(
        {
            "form_prefill": {
                "resource_type": "container",
                "hostname": "___",
                "mode": "weird",
                "start_at": datetime(2026, 7, 1, 10, tzinfo=UTC),
                "end_at": "not-a-date",
            }
        },
        request,
        [],
        resource_options=_vm_options(),
    )
    prefill = result["final_plan"]["form_prefill"]
    assert prefill["resource_type"] == "vm"
    assert prefill["hostname"] == "ai-generated-host"
    assert prefill["mode"] == "scheduled"
    assert prefill["gpu_mapping_id"] == ""
    assert prefill["start_at"] == ""


def test_normalize_gpu_label_with_description() -> None:
    request = RecommendationRequest(goal="gpu workload", requires_gpu=True)
    options = _vm_options()
    options["gpu_options"][0].update({"available_count": 1, "node": "pve1"})
    options["gpu_options"][1]["available_count"] = 1
    options["gpu_options"][1]["total_vram_mb"] = 24576
    result = normalize_ai_result(
        {"form_prefill": {"resource_type": "vm"}},
        request,
        [],
        resource_options=options,
    )
    assert result["final_plan"]["form_prefill"]["gpu_mapping_id"] == "g-desc"


def test_prompt_runtime_context_vram_and_node() -> None:
    text = prompt.build_chat_runtime_context(
        resource_type="vm",
        gpu_options=[
            {
                "mapping_id": "g1",
                "model": "A100",
                "vram": "80G",
                "node": "pve1",
                "available_count": 1,
                "capacity_count": 2,
            }
        ],
    )
    assert "VRAM: 80G" in text
    assert "node: pve1" in text
    assert "mapping_id: g1" in text
    assert "Ask about exactly this" in prompt.build_intake_focus_block("storage")


def test_schemas_focus_hint_and_preset_inference() -> None:
    assert _chat("hello", focus_hint=None).focus_hint is None
    teacher = RecommendationRequest(
        goal="class service", role="teacher", course_context="teaching"
    )
    assert teacher.preset == "teaching_class_service"
    team = RecommendationRequest(goal="team project", sharing_scope="shared")
    assert team.preset == "student_team_project"


def test_chat_payload_with_focus_hint() -> None:
    payload = chat_service.build_chat_payload(
        _chat("hi", focus_hint="  cpu cores  "),
        gpu_options=[],
        model_name="m",
    )
    system = payload["messages"][0]["content"]
    assert "Ask about exactly this, and nothing else: cpu cores" in system
