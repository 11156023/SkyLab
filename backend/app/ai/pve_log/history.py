"""Canonical history validation and merge rules for the PVE chat service."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


class PveHistoryValidationError(ValueError):
    """Raised when a client supplied transcript cannot be safely resumed."""


_VALID_ROLES = frozenset({"system", "user", "assistant", "tool"})
_MAX_HISTORY_MESSAGES = 40


def _invalid(detail: str) -> PveHistoryValidationError:
    return PveHistoryValidationError(f"PVE 對話 history 無效：{detail}")


def _parse_arguments(value: Any, *, call_id: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        raise _invalid(f"tool_call_id={call_id} 的 arguments 必須是 JSON object")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise _invalid(f"tool_call_id={call_id} 的 arguments 不是有效 JSON") from exc
    if not isinstance(parsed, dict):
        raise _invalid(f"tool_call_id={call_id} 的 arguments 必須是 JSON object")
    return parsed


def _canonical_tool_calls(
    raw_calls: Any,
    *,
    seen_ids: set[str],
    allowed_tool_names: set[str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if raw_calls is None:
        return [], {}
    if not isinstance(raw_calls, list):
        raise _invalid("assistant.tool_calls 必須是陣列")

    calls: list[dict[str, Any]] = []
    active: dict[str, str] = {}
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            raise _invalid("assistant.tool_calls 內的項目必須是 object")
        raw_id = raw_call.get("id")
        call_id = raw_id.strip() if isinstance(raw_id, str) else ""
        if not call_id:
            raise _invalid("assistant.tool_calls 缺少 tool_call_id")
        if call_id in seen_ids:
            raise _invalid(f"tool_call_id={call_id} 重複")

        call_type = raw_call.get("type", "function")
        if call_type != "function":
            raise _invalid(f"tool_call_id={call_id} 的 type 必須是 function")
        function = raw_call.get("function")
        if not isinstance(function, dict):
            raise _invalid(f"tool_call_id={call_id} 缺少 function object")
        name = function.get("name")
        if not isinstance(name, str) or name not in allowed_tool_names:
            raise _invalid(f"tool_call_id={call_id} 的工具名稱不被允許")
        arguments = _parse_arguments(function.get("arguments", "{}"), call_id=call_id)

        normalized_call = dict(raw_call)
        normalized_function = dict(function)
        normalized_function["name"] = name
        normalized_function["arguments"] = json.dumps(
            arguments,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        normalized_call["id"] = call_id
        normalized_call["type"] = "function"
        normalized_call["function"] = normalized_function
        calls.append(normalized_call)
        active[call_id] = name
        seen_ids.add(call_id)
    return calls, active


def _validate_history(
    history: list[dict[str, Any]],
    *,
    allowed_tool_names: set[str],
    allow_deferred: bool,
) -> list[dict[str, Any]]:
    if len(history) > _MAX_HISTORY_MESSAGES:
        raise _invalid(f"history 最多只能包含 {_MAX_HISTORY_MESSAGES} 筆訊息")
    normalized: list[dict[str, Any]] = []
    active_calls: dict[str, str] = {}
    seen_call_ids: set[str] = set()
    has_user_turn = False

    for index, raw_message in enumerate(history):
        if not isinstance(raw_message, dict):
            raise _invalid(f"第 {index + 1} 筆訊息必須是 object")
        role = raw_message.get("role")
        if role not in _VALID_ROLES:
            raise _invalid(f"第 {index + 1} 筆訊息 role 不被允許")

        # Client-owned system messages are discarded below. Validate their
        # shape first so malformed data is never silently accepted.
        content = raw_message.get("content")
        if role == "assistant":
            if content is not None and not isinstance(content, str):
                raise _invalid(f"第 {index + 1} 筆 assistant content 必須是字串或 null")
        elif not isinstance(content, str):
            raise _invalid(f"第 {index + 1} 筆 {role} content 必須是字串")

        if role == "system":
            continue

        if role == "user":
            if active_calls:
                raise _invalid("assistant tool-call 尚未完成，不能插入新的 user turn")
            has_user_turn = True

        normalized_message = dict(raw_message)
        if role == "assistant":
            if active_calls:
                raise _invalid("上一個 assistant tool-call 尚未完成")
            tool_calls, new_active = _canonical_tool_calls(
                raw_message.get("tool_calls"),
                seen_ids=seen_call_ids,
                allowed_tool_names=allowed_tool_names,
            )
            if "tool_calls" in raw_message:
                normalized_message["tool_calls"] = tool_calls
            active_calls = new_active

        elif role == "tool":
            tool_call_id = raw_message.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id.strip():
                raise _invalid(f"第 {index + 1} 筆 tool 缺少 tool_call_id")
            tool_call_id = tool_call_id.strip()
            tool_name = active_calls.get(tool_call_id)
            if tool_name is None:
                raise _invalid(f"tool_call_id={tool_call_id} 沒有對應的 assistant tool-call")

            content_text = content if isinstance(content, str) else ""
            try:
                tool_content = json.loads(content_text)
            except json.JSONDecodeError:
                tool_content = None
            if isinstance(tool_content, dict):
                if tool_content.get("pending"):
                    raise _invalid(
                        f"tool_call_id={tool_call_id} 仍是 pending，必須使用 server confirmation state"
                    )
                if tool_content.get("deferred"):
                    if not allow_deferred:
                        raise _invalid(
                            f"tool_call_id={tool_call_id} 的 deferred result 只能由 server continuation resume"
                        )
                    if tool_name != "ssh_exec":
                        raise _invalid("只有 ssh_exec 可以有 deferred result")
                if "confirmation_token" in tool_content and tool_name != "ssh_exec":
                    raise _invalid("confirmation_token 只能附在 ssh_exec tool result")
            active_calls.pop(tool_call_id)

        normalized.append(normalized_message)

    if active_calls:
        ids = ", ".join(active_calls)
        raise _invalid(f"tool-call 尚未收到完整 result：{ids}")
    if not has_user_turn:
        raise _invalid("history 必須至少包含一個 user turn")

    last_message = normalized[-1] if normalized else None
    if (
        last_message is not None
        and last_message.get("role") == "assistant"
        and not last_message.get("tool_calls")
    ):
        raise _invalid("history 不能以已完成的 assistant 回覆結尾，請提供新的 user turn")
    return normalized


def merge_pve_messages(
    *,
    message: str | None,
    history: list[dict[str, Any]] | None,
    server_system_prompt: str,
    scope_prompt: str | None = None,
    allowed_tool_names: Iterable[str],
    allow_deferred: bool = False,
) -> list[dict[str, Any]]:
    """Build the one canonical transcript accepted by the PVE model call.

    ``message`` is a new user turn and ``history`` is an already canonical
    transcript. They are deliberately mutually exclusive; callers must append
    a new user turn before invoking this function in history mode.
    """
    if not isinstance(message, (str, type(None))):
        raise _invalid("message 必須是字串")
    normalized_message = message.strip() if isinstance(message, str) else ""
    has_message = bool(normalized_message)

    if history is not None and not isinstance(history, list):
        raise _invalid("messages 必須是陣列")
    has_history = bool(history)
    if has_message and has_history:
        raise _invalid("message 與 messages 互斥，不能同時傳入")
    if not has_message and not has_history:
        raise _invalid("message 或 messages 至少需要一項")

    if has_history:
        transcript = _validate_history(
            history or [],
            allowed_tool_names=set(allowed_tool_names),
            allow_deferred=allow_deferred,
        )
    else:
        transcript = [{"role": "user", "content": normalized_message}]

    messages = [{"role": "system", "content": server_system_prompt}]
    if scope_prompt:
        messages.append({"role": "system", "content": scope_prompt})
    messages.extend(transcript)
    return messages


__all__ = ["PveHistoryValidationError", "merge_pve_messages"]
