"""TypedDict contracts for Teacher Judge internal data flow."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

# ── Fix Hint ──────────────────────────────────────────────────────────────────
# Each fix_type populates a different subset; total=False allows partial keys.


class FixHint(TypedDict, total=False):
    type: str
    description: str
    pattern: NotRequired[str]
    field: NotRequired[str]
    value: NotRequired[str | int | bool]
    function: NotRequired[str]
    command: NotRequired[str]
    param: NotRequired[str]
    exception: NotRequired[str]
    path: NotRequired[str]
    name: NotRequired[str]
    current: NotRequired[str]
    call: NotRequired[str]
    mode: NotRequired[str]
    issues: NotRequired[list[str]]
    suggested_fix: NotRequired[str | None]
    lineno: NotRequired[int]
    end_lineno: NotRequired[int]
    snippet: NotRequired[str]
    target: NotRequired[str]
    required_pattern: NotRequired[str]


# ── Policy / Quality Check Result ─────────────────────────────────────────────
# Returned by check_script_policy() and check_script_quality().


class CheckResult(TypedDict):
    approved: bool
    blocked: bool
    risk_level: Literal["low", "high"]
    issues: list[str]
    fix_hints: list[FixHint]
    # 非阻斷提示（文案類建議）；不影響 approved/blocked 判定。
    warnings: NotRequired[list[str]]


# ── AI Review Result ──────────────────────────────────────────────────────────
# Returned by _normalize_ai_review() and review_script_with_ai().


class AIReviewResult(TypedDict):
    approved: bool
    risk_level: Literal["low", "medium", "high"]
    issues: list[str]
    suggested_fix: str | None


# ── Gate Merge Result ─────────────────────────────────────────────────────────
# Returned by _merge_gate_results(); combines safety + quality into one verdict.


class GateResult(TypedDict):
    approved: bool
    blocked: bool
    risk_level: Literal["low", "high"]
    issues: list[str]
    safety_approved: bool
    safety_issues: list[str]
    quality_approved: bool
    quality_issues: list[str]
    quality_warnings: NotRequired[list[str]]
    coverage: NotRequired[dict[str, Any]]
    review_attempts: NotRequired[list[dict[str, object]]]
    retry_summary: NotRequired[dict[str, object]]
    generation_error: NotRequired[str]


# ── Rubric Coverage ───────────────────────────────────────────────────────────
# Generated alongside the script; maps each record_check to rubric item ids.


class CoverageMapping(TypedDict):
    check_id: str
    rubric_item_ids: list[str]


class CoverageResult(TypedDict):
    approved: bool
    issues: list[str]
    fix_hints: list[FixHint]
    mappings: list[CoverageMapping]
    uncovered_items: list[dict[str, str]]


# ── Previous Review Feedback ──────────────────────────────────────────────────
# Returned by _previous_review_feedback(); fed back into regeneration prompt.


class PreviousReviewFeedback(TypedDict, total=False):
    policy_approved: bool | None
    policy_issues: list[str]
    quality_approved: bool | None
    quality_issues: list[str]
    coverage_approved: bool | None
    coverage_issues: list[str]
    uncovered_rubric_items: list[dict[str, str]]
    ai_review_approved: bool | None
    ai_review_issues: list[str]
    ai_review_suggested_fix: str | None


# ── VLLM Metrics ──────────────────────────────────────────────────────────────
# Returned by _call_vllm(); passed through chat and summary workflows.


class VLLMMetrics(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    elapsed_seconds: float
    tokens_per_second: float


# ── Template Command Snapshot ─────────────────────────────────────────────────
# Returned by _template_commands_snapshot(); injected into rubric snapshot JSON.


class TemplateCommandSnapshot(TypedDict):
    command_key: str
    command_label: str
    category: str
    command_template: str
    description: str
    risk_level: str
    requires_confirmation: bool


# ── Script Validation Output ──────────────────────────────────────────────────
# Returned by validate_managed_script_output().


class ScriptValidationResult(TypedDict, total=False):
    valid: bool
    error: str | None
    schema_version: str
    checks_count: NotRequired[int]
