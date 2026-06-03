"""Shared utility functions for AI modules — LLM response processing & safe type coercion."""

from __future__ import annotations

from typing import Any


def strip_think_tags(text: str) -> str:
    """Keep only content after </think> marker; return text as-is if tag absent."""
    marker = "</think>"
    idx = text.find(marker)
    if idx != -1:
        return text[idx + len(marker) :].strip()
    return text.strip()


def apply_thinking_control(payload: dict[str, Any], enable_thinking: bool) -> dict[str, Any]:
    """Inject *enable_thinking* into the vLLM chat_template_kwargs payload."""
    payload["chat_template_kwargs"] = {
        **dict(payload.get("chat_template_kwargs") or {}),
        "enable_thinking": enable_thinking,
    }
    return payload


def safe_int(
    value: Any,
    default: int = 0,
    *,
    minimum: int | None = None,
    extract_digits: bool = False,
) -> int:
    """Safely coerce *value* to int with configurable fallback, floor, and digit extraction.

    Args:
        value: Raw input (``None``, ``str``, ``int``, ``float``, etc.).
        default: Returned when coercion fails or *value* is ``None``.
        minimum: When not ``None``, clamp result to ``>= minimum``.
        extract_digits: When ``True``, strip non-digit characters from strings
            before parsing (e.g. ``"2 vCPU"`` → ``2``).
    """
    if value is None:
        return default
    try:
        if extract_digits and isinstance(value, str):
            digits = "".join(ch for ch in value if ch.isdigit())
            parsed = int(digits) if digits else int(value)
        else:
            parsed = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and parsed < minimum:
        return minimum
    return parsed


def safe_float(value: Any, default: float = 0.0) -> float:
    """Safely coerce *value* to float; return *default* on failure or ``None``."""
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def safe_bool(value: Any, default: bool = False) -> bool:
    """Safely coerce *value* to bool with fallback.

    Returns ``True`` for truthy values (e.g., ``True``, ``1``, ``"true"``, ``"yes"``, ``"on"``, ``"checked"``)
    and ``False`` for falsy values (e.g., ``False``, ``0``, ``"false"``, ``"no"``, ``"off"``, ``"unchecked"``).
    Otherwise returns *default*.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "on", "checked", "done"}:
            return True
        if text in {"false", "0", "no", "n", "off", "unchecked", "todo"}:
            return False
    return default

