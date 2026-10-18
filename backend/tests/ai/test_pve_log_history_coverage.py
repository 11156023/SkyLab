"""Coverage tests for app.ai.pve_log.history validation and compaction."""

import json

import pytest

from app.ai.pve_log import history as pve_history
from app.ai.pve_log.history import (
    PveHistoryValidationError,
    merge_pve_messages,
)

_TOOLS = {"get_nodes", "ssh_exec"}


def _call(call_id: str = "c1", name: str = "get_nodes", arguments="{}") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _assistant(*calls: dict) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


def _tool(content: str, call_id: str = "c1") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _merge(history, *, message=None, allow_deferred=False):
    return merge_pve_messages(
        message=message,
        history=history,
        server_system_prompt="sys",
        allowed_tool_names=_TOOLS,
        allow_deferred=allow_deferred,
    )


_USER = {"role": "user", "content": "hi"}


def test_bounded_value_drops_oversized_scalars() -> None:
    assert pve_history._bounded_value(123456789, 3) is None


def test_non_json_tool_content_is_kept_verbatim() -> None:
    messages = _merge([_USER, _assistant(_call()), _tool("plain text")])
    assert messages[-1]["content"] == "plain text"


@pytest.mark.parametrize(
    "history",
    [
        [_USER, _assistant(_call(arguments=5)), _tool("{}")],
        [_USER, _assistant(_call(arguments="not json")), _tool("{}")],
        [_USER, _assistant(_call(arguments="[1]")), _tool("{}")],
        [_USER, {"role": "assistant", "content": None, "tool_calls": "x"}],
        [_USER, {"role": "assistant", "content": None, "tool_calls": ["x"]}],
        [_USER, _assistant({"function": {"name": "get_nodes"}})],
        [_USER, _assistant(_call("dup"), _call("dup"))],
        [_USER, _assistant({**_call(), "type": "other"})],
        [_USER, _assistant({"id": "c1", "type": "function"})],
        [_USER, _assistant(_call(name="rm_rf"))],
        [_USER] * 41,
        [_USER, "not-a-dict"],
        [_USER, {"role": "hacker", "content": "x"}],
        [_USER, {"role": "assistant", "content": 5}],
        [{"role": "user", "content": None}],
        [_USER, _assistant(_call()), _USER],
        [_USER, _assistant(_call()), _assistant(_call("c2"))],
        [_USER, _assistant(_call()), {"role": "tool", "content": "{}"}],
        [_USER, _assistant(_call()), _tool('{"deferred": true}')],
        [_USER, _assistant(_call()), _tool('{"confirmation_token": "t"}')],
        [_USER, _assistant(_call())],
        [_assistant(_call()), _tool("{}")],
    ],
)
def test_invalid_histories_are_rejected(history) -> None:
    with pytest.raises(PveHistoryValidationError):
        _merge(history, allow_deferred=True)


def test_message_must_be_string() -> None:
    with pytest.raises(PveHistoryValidationError, match="message 必須是字串"):
        _merge(None, message=123)


def test_history_must_be_list() -> None:
    with pytest.raises(PveHistoryValidationError, match="messages 必須是陣列"):
        _merge({"role": "user"})


def test_message_and_history_are_mutually_exclusive() -> None:
    with pytest.raises(PveHistoryValidationError, match="互斥"):
        _merge([_USER], message="hi")


def test_message_or_history_is_required() -> None:
    with pytest.raises(PveHistoryValidationError, match="至少需要一項"):
        _merge(None, message="  ")


def test_oversized_non_ssh_tool_content_is_compacted() -> None:
    big = json.dumps({"rows": ["y" * 100 for _ in range(200)]})
    messages = _merge([_USER, _assistant(_call()), _tool(big)])
    assert json.loads(messages[-1]["content"])["truncated"] is True
