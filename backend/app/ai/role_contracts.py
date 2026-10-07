"""System AI 角色輸出契約。

這裡只保存跨功能共用的不變量；候選內容、權限、scope 與 renderer 仍由各功能
模組負責。契約值只能由後端程式建立，不能由 request 或模型輸出覆寫。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class OutputMode(str, Enum):
    SERVER_RENDERED = "server_rendered"
    MODEL_FREE_TEXT = "model_free_text"
    MODEL_ACTION = "model_action"


class AdherenceVerdict(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    INSUFFICIENT_CONTEXT = "insufficient_context"


class AdherenceReason(str, Enum):
    NONE = "none"
    ROLE_DRIFT = "role_drift"
    UNRELATED_ANSWER = "unrelated_answer"
    TARGET_MISMATCH = "target_mismatch"
    ACTION_NOT_REQUESTED = "action_not_requested"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    CHECK_FAILED = "check_failed"


@dataclass(frozen=True, slots=True)
class RoleContract:
    role_id: str
    output_mode: OutputMode
    contract_version: str
    fallback_key: str

    def __post_init__(self) -> None:
        for field_name in ("role_id", "contract_version", "fallback_key"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be blank")


@dataclass(frozen=True, slots=True)
class TurnContext:
    """由後端可信狀態組成的單輪角色、scope 與動作邊界。"""

    role_id: str
    phase: str
    scope_ref: str
    selected_target_id: str | None = None
    target_revision: int | None = None
    candidate_target_ids: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = ()
    pending_question_key: str | None = None

    def __post_init__(self) -> None:
        if not self.role_id.strip() or not self.phase.strip() or not self.scope_ref.strip():
            raise ValueError("turn context identity fields must not be blank")
        if self.target_revision is not None and self.target_revision < 0:
            raise ValueError("target_revision must not be negative")
        if len(self.candidate_target_ids) != len(set(self.candidate_target_ids)):
            raise ValueError("candidate_target_ids must be unique")
        if len(self.allowed_actions) != len(set(self.allowed_actions)):
            raise ValueError("allowed_actions must be unique")

    def as_facts(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "phase": self.phase,
            "scope_ref": self.scope_ref,
            "selected_target_id": self.selected_target_id,
            "target_revision": self.target_revision,
            "candidate_target_ids": list(self.candidate_target_ids),
            "allowed_actions": list(self.allowed_actions),
            "pending_question_key": self.pending_question_key,
        }


class CandidateDecision(BaseModel):
    """模型唯一可回傳的窄角色決策形狀。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    candidate_ids: list[str] = Field(default_factory=list, max_length=10)


@dataclass(frozen=True, slots=True)
class AdherenceResult:
    verdict: AdherenceVerdict
    reason_code: AdherenceReason

    def __post_init__(self) -> None:
        if self.verdict is AdherenceVerdict.ALLOW:
            valid = self.reason_code is AdherenceReason.NONE
        elif self.verdict is AdherenceVerdict.INSUFFICIENT_CONTEXT:
            valid = self.reason_code in {
                AdherenceReason.INSUFFICIENT_CONTEXT,
                AdherenceReason.CHECK_FAILED,
            }
        else:
            valid = self.reason_code not in {
                AdherenceReason.NONE,
                AdherenceReason.INSUFFICIENT_CONTEXT,
                AdherenceReason.CHECK_FAILED,
            }
        if not valid:
            raise ValueError("verdict and reason_code do not form a valid pair")

    @property
    def allowed(self) -> bool:
        return self.verdict is AdherenceVerdict.ALLOW


def parse_candidate_decision(value: Any) -> CandidateDecision:
    """完整驗證模型結果；不做 fuzzy 修復或擷取局部 JSON。"""

    try:
        return CandidateDecision.model_validate(value)
    except ValidationError as exc:
        raise ValueError("invalid candidate decision") from exc


def validate_candidate_ids(
    decision: CandidateDecision,
    allowed_ids: set[str] | frozenset[str],
    max_items: int,
) -> tuple[str, ...]:
    """驗證 candidate ID 的集合、順序、唯一性及數量。"""

    if max_items < 0:
        raise ValueError("max_items must not be negative")
    if len(decision.candidate_ids) > max_items:
        raise ValueError("too many candidate ids")
    if len(decision.candidate_ids) != len(set(decision.candidate_ids)):
        raise ValueError("duplicate candidate ids")
    if any(not item or item not in allowed_ids for item in decision.candidate_ids):
        raise ValueError("unknown candidate id")
    return tuple(decision.candidate_ids)


def candidate_decision_schema(allowed_ids: list[str], max_items: int) -> dict[str, Any]:
    """建立 request-scoped JSON Schema；空候選時呼叫端不得送模型。"""

    if not allowed_ids:
        raise ValueError("allowed_ids must not be empty")
    return {
        "type": "object",
        "properties": {
            "candidate_ids": {
                "type": "array",
                "items": {"type": "string", "enum": allowed_ids},
                "maxItems": max_items,
                "uniqueItems": True,
            }
        },
        "required": ["candidate_ids"],
        "additionalProperties": False,
    }
