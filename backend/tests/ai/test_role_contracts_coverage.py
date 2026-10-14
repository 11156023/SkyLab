"""Coverage tests for app.ai.role_contracts invariants."""

import pytest

from app.ai.role_contracts import (
    AdherenceReason,
    AdherenceResult,
    AdherenceVerdict,
    CandidateDecision,
    OutputMode,
    RoleContract,
    TurnContext,
    candidate_decision_schema,
    validate_candidate_ids,
)


@pytest.mark.parametrize("field", ["role_id", "contract_version", "fallback_key"])
def test_role_contract_rejects_blank_fields(field: str) -> None:
    values = {
        "role_id": "pve_log",
        "output_mode": OutputMode.MODEL_FREE_TEXT,
        "contract_version": "v1",
        "fallback_key": "key",
    }
    values[field] = "  "
    with pytest.raises(ValueError, match=f"{field} must not be blank"):
        RoleContract(**values)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"phase": " "}, "identity fields"),
        ({"target_revision": -1}, "target_revision"),
        ({"candidate_target_ids": ("a", "a")}, "candidate_target_ids"),
        ({"allowed_actions": ("x", "x")}, "allowed_actions"),
    ],
)
def test_turn_context_validation(kwargs: dict, message: str) -> None:
    base = {"role_id": "r", "phase": "p", "scope_ref": "s"}
    base.update(kwargs)
    with pytest.raises(ValueError, match=message):
        TurnContext(**base)


def test_adherence_result_rejects_negative_usage() -> None:
    with pytest.raises(ValueError, match="token usage"):
        AdherenceResult(AdherenceVerdict.ALLOW, AdherenceReason.NONE, prompt_tokens=-1)
    with pytest.raises(ValueError, match="elapsed_seconds"):
        AdherenceResult(
            AdherenceVerdict.ALLOW, AdherenceReason.NONE, elapsed_seconds=-0.5
        )


def test_validate_candidate_ids_limits() -> None:
    decision = CandidateDecision(candidate_ids=["a", "b"])
    with pytest.raises(ValueError, match="max_items"):
        validate_candidate_ids(decision, {"a", "b"}, -1)
    with pytest.raises(ValueError, match="too many"):
        validate_candidate_ids(decision, {"a", "b"}, 1)
    assert validate_candidate_ids(decision, {"a", "b"}, 2) == ("a", "b")


def test_candidate_decision_schema_requires_ids() -> None:
    with pytest.raises(ValueError, match="allowed_ids"):
        candidate_decision_schema([], 3)
    schema = candidate_decision_schema(["a"], 3)
    assert schema["properties"]["candidate_ids"]["maxItems"] == 3
