"""Coverage tests for app.ai.contextual_help.service helper branches."""

from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.contextual_help import service as help_service
from app.ai.contextual_help.schemas import ElementSpec, ExplainRequest, SurfaceSpec
from app.models.user import UserRole

_SURFACE = SurfaceSpec(
    id="demo",
    path="/demo",
    title="示範頁",
    purpose="示範用途。第二句。",
    elements=(
        ElementSpec(
            id="a", role="text", label="欄位甲", constraints=("必填", "最多 5 字")
        ),
        ElementSpec(id="b", role="text", label="欄位乙", help="說明乙。更多。"),
    ),
)


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=None, role=UserRole.student, is_superuser=False)


def _request(**overrides: Any) -> ExplainRequest:
    payload: dict[str, Any] = {
        "question": "這頁在做什麼？",
        "surface_id": "request-form",
    }
    payload.update(overrides)
    return ExplainRequest(**payload)


def test_adherence_facts_collect_blocked_ids_once() -> None:
    facts = help_service.build_help_adherence_facts(
        surface_id="demo",
        intent="validation_help",
        context={
            "target": {"id": "a"},
            "blocked": ["junk", {"id": 1}, {"id": "a"}, {"id": "b"}],
        },
        grounded_in=[],
        context_level=1,
        context_version=0,
    )
    assert facts["turn_context"]["candidate_target_ids"] == ["a", "b"]
    assert facts["turn_context"]["selected_target_id"] == "a"


@pytest.mark.parametrize(
    "response",
    [
        {"choices": "nope"},
        {"choices": [{"message": {"content": "<think>x</think>   "}}]},
    ],
)
def test_parse_model_answer_rejects_bad_shapes(response) -> None:
    with pytest.raises(ValueError):
        help_service._parse_model_answer(response)


def test_deterministic_validation_without_single_reason_defers_to_model() -> None:
    assert (
        help_service._deterministic_answer(
            _SURFACE,
            "validation_help",
            {"blocked": [{}, {}]},
            active_target=None,
            blocked=["a", "b"],
        )
        is None
    )


def test_deterministic_field_help_lists_constraints() -> None:
    answer = help_service._deterministic_answer(
        _SURFACE,
        "field_help",
        {"target": {"constraints": ["必填"]}},
        active_target="a",
        blocked=[],
    )
    assert answer == "「欄位甲」的填寫限制：必填、最多 5 字。"
    # 目標不存在於畫面定義時不自行拼答案
    assert (
        help_service._deterministic_answer(
            _SURFACE,
            "field_help",
            {"target": {"constraints": ["必填"]}},
            active_target="missing",
            blocked=[],
        )
        is None
    )


def test_fallback_answer_lists_blocked_labels() -> None:
    answer = help_service._fallback_answer(
        _SURFACE, "validation_help", active_target=None, blocked=["a", "zzz", "b"]
    )
    assert answer == "以下欄位還沒有通過驗證：欄位甲、欄位乙。"
    # 全部都找不到時退回頁面說明
    overview = help_service._fallback_answer(
        _SURFACE, "validation_help", active_target=None, blocked=["zzz"]
    )
    assert overview.startswith("「示範頁」：示範用途。")


async def test_unknown_active_target_is_ignored(monkeypatch) -> None:
    monkeypatch.setattr(help_service.system_ai_env, "vllm_model_name", "")
    result = await help_service.explain(_request(active_target="nope"), _user())
    assert result.target is None
    assert result.intent == "page_overview"


async def test_question_naming_an_element_targets_it(monkeypatch) -> None:
    monkeypatch.setattr(help_service.system_ai_env, "vllm_model_name", "")
    result = await help_service.explain(
        _request(question="申請原因要填什麼？"), _user()
    )
    assert result.target == "request.reason"
    assert result.intent == "field_help"


async def test_dialog_help_without_dialogs_becomes_overview(monkeypatch) -> None:
    monkeypatch.setattr(help_service.system_ai_env, "vllm_model_name", "")
    monkeypatch.setattr(help_service, "classify", lambda *_a, **_k: "dialog_help")
    monkeypatch.setattr(help_service, "visible_dialogs", lambda *_a, **_k: ())
    result = await help_service.explain(_request(), _user())
    assert result.intent == "page_overview"


async def test_field_help_without_resolved_target_becomes_overview(
    monkeypatch,
) -> None:
    monkeypatch.setattr(help_service.system_ai_env, "vllm_model_name", "")
    monkeypatch.setattr(help_service, "classify", lambda *_a, **_k: "field_help")
    result = await help_service.explain(_request(question="嗯？"), _user())
    assert result.intent == "page_overview"
