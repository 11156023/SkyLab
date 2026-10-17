"""Coverage tests for app.ai.pve_log.chat helpers and agent-loop branches."""

import json
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.ai.pve_log import chat as chat_mod
from app.ai.pve_log import collector
from app.ai.pve_log import ssh_exec as ssh_mod
from app.ai.pve_log.guest_diagnostics import (
    ERROR_CODE_CONNECTION_FAILED,
    GUEST_DIAGNOSTIC_PROBES,
    ProbeResult,
)
from app.ai.pve_log.history import PveHistoryValidationError
from app.ai.pve_log.schemas import SSHExecRequest
from app.ai.role_contracts import AdherenceReason, AdherenceResult, AdherenceVerdict


@pytest.fixture(autouse=True)
def _fresh_ssh_stores(monkeypatch):
    monkeypatch.setattr(ssh_mod, "_pending_store", {})
    monkeypatch.setattr(ssh_mod, "_completed_store", {})


def _settings(**overrides: Any) -> SimpleNamespace:
    values = {
        "VLLM_BASE_URL": "http://vllm/v1",
        "VLLM_MODEL_NAME": "test-model",
        "VLLM_TIMEOUT": 30,
        "VLLM_CHAT_MAX_TOKENS": 4096,
        "VLLM_ENABLE_THINKING": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _patch_model(monkeypatch, responses: list[Any], *, adherence_tokens: int = 0):
    monkeypatch.setattr(chat_mod, "settings", _settings())

    async def fake_completion(_payload, *, profile, timeout, request_id=None):
        del profile, timeout, request_id
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def allow(*_args, **_kwargs):
        return AdherenceResult(
            AdherenceVerdict.ALLOW,
            AdherenceReason.NONE,
            completion_tokens=adherence_tokens,
        )

    monkeypatch.setattr(chat_mod.vllm_client, "create_chat_completion", fake_completion)
    monkeypatch.setattr(chat_mod, "check_adherence", allow)


def _tool_response(name: str, arguments: str = "{}", call_id: str = "call-1", **extra):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                }
            }
        ],
        **extra,
    }


def _text_response(text: str = "完成", **extra):
    return {"choices": [{"message": {"role": "assistant", "content": text}}], **extra}


# ── 工具執行 helpers ─────────────────────────────────────────────────────


def test_execute_tool_sync_wraps_non_dict_results_with_warning() -> None:
    context = SimpleNamespace(execute=lambda *_a, **_k: [1, 2], errors=["boom"])
    result = chat_mod._execute_tool_sync(context, "get_nodes", {})
    assert result["data"] == [1, 2]
    assert result["error"]


async def test_execute_ssh_tool_validates_args_and_scope() -> None:
    missing = await chat_mod._execute_ssh_tool({"vmid": "x"})
    assert missing["pending"] is False
    assert missing["error"]

    blocked = await chat_mod._execute_ssh_tool(
        {"vmid": 5, "command": "ls"}, allowed_vmids={6}
    )
    assert blocked["blocked"] is True


async def test_guest_diagnostics_without_context_still_probes(monkeypatch) -> None:
    async def failed_batch(vmid, probes, **_kwargs):
        del vmid
        return {
            probe.name: ProbeResult(
                name=probe.name, error_code=ERROR_CODE_CONNECTION_FAILED
            )
            for probe in probes
        }

    monkeypatch.setattr(ssh_mod, "run_guest_probe_batch", failed_batch)
    result = await chat_mod._execute_guest_diagnostics_tool({"vmid": 7}, context=None)
    assert result["resource"]["error_code"] == "contextUnavailable"
    assert len(GUEST_DIAGNOSTIC_PROBES) > 0
    assert len(result["warnings"]) >= 2


def test_deferred_ssh_result_without_valid_args() -> None:
    result = chat_mod._deferred_ssh_result({})
    assert result["deferred"] is True
    assert "vmid" not in result


def test_next_deferred_ssh_call_edge_cases() -> None:
    messages = [
        {"role": "tool", "tool_call_id": "x", "content": "not json"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "c1", "function": {"name": "get_nodes", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"deferred": true}'},
    ]
    assert chat_mod._next_deferred_ssh_call(messages) is None


def test_canonicalize_tool_calls_edge_cases() -> None:
    no_list = chat_mod._canonicalize_model_tool_calls(
        {"content": "x", "tool_calls": "bad"}, reserved_ids=set()
    )
    assert "tool_calls" not in no_list

    result = chat_mod._canonicalize_model_tool_calls(
        {
            "tool_calls": [
                "not-a-dict",
                {"id": "taken", "function": {"name": "get_nodes", "arguments": "{}"}},
                {"id": "  ", "function": {"name": "get_storage", "arguments": {}}},
            ]
        },
        reserved_ids={"taken"},
    )
    ids = [call["id"] for call in result["tool_calls"]]
    assert len(ids) == 2
    assert "taken" not in ids
    assert all(call_id.startswith("call_") for call_id in ids)


def test_small_parsers() -> None:
    assert chat_mod._parse_tool_arguments({"a": 1}) == {"a": 1}
    assert chat_mod._parse_tool_arguments(5) is None
    assert chat_mod._completion_tokens_used({"usage": {}}, 9) == 9
    assert (
        chat_mod._completion_tokens_used({"usage": {"completion_tokens": "x"}}, 9) == 9
    )
    assert chat_mod._latest_user_request([{"role": "assistant", "content": "x"}]) == ""
    evidence = chat_mod._pve_tool_evidence(
        [{"role": "tool", "tool_call_id": "c1", "content": "raw text"}]
    )
    assert evidence[0]["result"] == {"output": "raw text"}


# ── 確認紀錄驗證 ─────────────────────────────────────────────────────────


_RESULT = {"vmid": 1, "command": "ls", "exit_code": 0, "stdout": "ok"}


def _record(token: str = "tok", **overrides: Any) -> dict[str, Any]:
    record = {
        "token": token,
        "created_at": time.monotonic(),
        "request": SSHExecRequest(vmid=1, command="ls"),
        "tool_call_id": "c1",
        "requester_id": None,
        "scope_type": None,
        "scope_id": None,
        "allowed_vmids": None,
        "result": dict(_RESULT),
        "consumed": False,
    }
    record.update(overrides)
    ssh_mod._completed_store[token] = record
    return record


def _history(
    content: Any,
    *,
    arguments: str = '{"vmid": 1, "command": "ls"}',
    tool_call_id: Any = "c1",
    extra_calls: tuple = (),
) -> list[dict[str, Any]]:
    return [
        {
            "role": "assistant",
            "tool_calls": [
                *extra_calls,
                {
                    "id": "c1",
                    "function": {"name": "ssh_exec", "arguments": arguments},
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content if isinstance(content, str) else json.dumps(content),
        },
    ]


def _validate(messages, allowed_vmids=None) -> None:
    chat_mod._validate_confirmation_history(
        messages,
        requester_id=None,
        scope_type=None,
        scope_id=None,
        allowed_vmids=allowed_vmids,
    )


def test_confirmation_history_accepts_and_skips_unrelated_items() -> None:
    _record()
    messages = _history(
        {**_RESULT, "reason": "r", "confirmation_token": "tok"},
        extra_calls=("not-a-dict",),
    )
    messages += [
        {"role": "tool", "tool_call_id": "c7", "content": "not json"},
        {"role": "tool", "tool_call_id": "c8", "content": "[1]"},
        {"role": "tool", "tool_call_id": None, "content": "{}"},
    ]
    _validate(messages)
    assert ssh_mod._completed_store["tok"]["consumed"] is True
    assert "confirmation_token" not in json.loads(messages[1]["content"])


@pytest.mark.parametrize(
    ("record_overrides", "history_kwargs", "content_overrides", "allowed"),
    [
        ({}, {"tool_call_id": None}, {}, None),
        ({"requester_id": "someone"}, {}, {}, None),
        ({"allowed_vmids": {1}}, {}, {}, None),
        ({"tool_call_id": "other"}, {}, {}, None),
        ({"tool_call_id": "c9"}, {"tool_call_id": "c9"}, {}, None),
        ({}, {"arguments": '{"vmid": "x", "command": "ls"}'}, {}, None),
        ({}, {"arguments": '{"vmid": 1, "command": "pwd"}'}, {}, None),
        ({"result": "bad"}, {}, {}, None),
        ({}, {}, {"evil": True}, None),
        ({}, {}, {"stdout": "tampered"}, None),
    ],
)
def test_confirmation_history_rejects_mismatches(
    record_overrides, history_kwargs, content_overrides, allowed
) -> None:
    _record(**record_overrides)
    content = {**_RESULT, "confirmation_token": "tok", **content_overrides}
    with pytest.raises(PveHistoryValidationError):
        _validate(_history(content, **history_kwargs), allowed_vmids=allowed)


def test_confirmation_history_accepts_already_consumed_result_without_token() -> None:
    _record(consumed=True)
    messages = _history({**_RESULT, "reason": "r"})
    _validate(messages)
    assert json.loads(messages[1]["content"])["reason"] == "r"


def test_confirmation_history_rejects_non_object_server_result() -> None:
    _record()
    with pytest.raises(PveHistoryValidationError, match="JSON object"):
        _validate(_history("[1]"))


def test_confirmation_history_rejects_concurrent_consumption(monkeypatch) -> None:
    _record()
    monkeypatch.setattr(ssh_mod, "consume_completed_confirmation", lambda _t: None)
    with pytest.raises(PveHistoryValidationError, match="其他請求"):
        _validate(_history({**_RESULT, "confirmation_token": "tok"}))


# ── chat() agent loop ────────────────────────────────────────────────────


async def test_chat_requires_vllm_configuration(monkeypatch) -> None:
    monkeypatch.setattr(chat_mod, "settings", _settings(VLLM_BASE_URL=""))
    result = await chat_mod.chat(message="hi")
    assert result.error


async def test_deferred_resume_records_tool_failure(monkeypatch) -> None:
    _patch_model(monkeypatch, [_text_response("結束")])

    async def broken_ssh(*_args, **_kwargs):
        raise RuntimeError("ssh broke")

    monkeypatch.setattr(chat_mod, "_execute_ssh_tool", broken_ssh)
    history = [
        {"role": "user", "content": "看兩台"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "s1",
                    "type": "function",
                    "function": {
                        "name": "ssh_exec",
                        "arguments": '{"vmid":1,"command":"ls","reason":"a"}',
                    },
                },
                {
                    "id": "s2",
                    "type": "function",
                    "function": {
                        "name": "ssh_exec",
                        "arguments": '{"vmid":1,"command":"pwd","reason":"b"}',
                    },
                },
            ],
        },
        {"role": "tool", "tool_call_id": "s1", "content": '{"exit_code":0}'},
        {
            "role": "tool",
            "tool_call_id": "s2",
            "content": '{"deferred":true,"pending":false}',
        },
    ]
    result = await chat_mod.chat(history=history)
    assert result.reply == "結束"
    assert result.tools_called[0].result == {"error": "ssh broke"}


async def test_chat_token_budget_exhausted_before_next_round(monkeypatch) -> None:
    _patch_model(
        monkeypatch,
        [_tool_response("get_nodes", usage={"completion_tokens": 10})],
        adherence_tokens=20_000,
    )
    monkeypatch.setattr(collector.PveToolContext, "execute", lambda self, *_a, **_k: [])
    result = await chat_mod.chat(message="節點")
    assert result.reply == chat_mod.PVE_ADHERENCE_FALLBACK


async def test_chat_upstream_http_error(monkeypatch) -> None:
    request = httpx.Request("POST", "http://vllm/v1/chat/completions")
    error = httpx.HTTPStatusError(
        "bad",
        request=request,
        response=httpx.Response(503, text="down", request=request),
    )
    _patch_model(monkeypatch, [error])
    result = await chat_mod.chat(message="hi")
    assert "503" in result.error


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("slow"), RuntimeError("connection reset")]
)
async def test_chat_connection_failure(monkeypatch, error) -> None:
    _patch_model(monkeypatch, [error])
    result = await chat_mod.chat(message="hi")
    assert result.error
    assert result.reply == ""


async def test_chat_empty_choices(monkeypatch) -> None:
    _patch_model(monkeypatch, [{"choices": []}])
    result = await chat_mod.chat(message="hi")
    assert result.error


async def test_chat_truncated_model_output(monkeypatch) -> None:
    _patch_model(
        monkeypatch,
        [{"choices": [{"finish_reason": "length", "message": {"content": "x"}}]}],
    )
    result = await chat_mod.chat(message="hi")
    assert result.reply == chat_mod.PVE_ADHERENCE_FALLBACK


async def test_chat_final_reply_without_adherence_budget(monkeypatch) -> None:
    _patch_model(
        monkeypatch,
        [_text_response("x", usage={"completion_tokens": 12_200})],
    )
    result = await chat_mod.chat(message="hi")
    assert result.reply == chat_mod.PVE_ADHERENCE_FALLBACK


async def test_chat_runs_guest_diagnostics_tool(monkeypatch) -> None:
    _patch_model(
        monkeypatch,
        [
            _tool_response("get_guest_diagnostic_summary", '{"vmid":7}'),
            _text_response(),
        ],
    )

    async def fake_guest(args, **_kwargs):
        return {"vmid": args["vmid"], "collection_status": "ok"}

    monkeypatch.setattr(chat_mod, "_execute_guest_diagnostics_tool", fake_guest)
    result = await chat_mod.chat(message="診斷 7")
    assert result.reply == "完成"
    assert result.tools_called[0].result["vmid"] == 7


async def test_chat_tool_failure_is_reported_to_model(monkeypatch) -> None:
    _patch_model(monkeypatch, [_tool_response("get_nodes"), _text_response()])
    monkeypatch.setattr(chat_mod, "PveToolContext", lambda: None)
    result = await chat_mod.chat(message="節點")
    assert result.reply == "完成"
    assert "context" in result.tools_called[0].result["error"]


async def test_chat_without_any_rounds_is_unreachable(monkeypatch) -> None:
    _patch_model(monkeypatch, [])
    monkeypatch.setattr(chat_mod, "_MAX_TOOL_ROUNDS", -1)
    with pytest.raises(AssertionError, match="unreachable"):
        await chat_mod.chat(message="hi")
