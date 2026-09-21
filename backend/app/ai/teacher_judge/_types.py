"""TypedDict contracts for Teacher Judge internal data flow."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

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


# ── VLLM Metrics ──────────────────────────────────────────────────────────────
# Returned by _call_vllm(); passed through chat and summary workflows.


class VLLMMetrics(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    elapsed_seconds: float
    tokens_per_second: float


# ── Script Validation Output ──────────────────────────────────────────────────
# Returned by validate_managed_script_output().


class ScriptValidationResult(TypedDict, total=False):
    valid: bool
    error: str | None
    schema_version: str
    checks_count: NotRequired[int]
