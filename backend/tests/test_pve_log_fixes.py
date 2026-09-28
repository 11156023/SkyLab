"""Regression tests for ai/pve_log bug fixes.

- deferred ssh_exec results resume from a normal history continuation
- _ssh_exec_sync drains output before waiting for the exit status
- output redaction stays linear on long unbroken runs of characters
- the tool-round limit never leaves an unanswered tool_call in history
- model tool calls are canonicalized with the history validator's rules
- PveToolContext aggregates every enabled PVE connection
- live IP lookup runs off the event loop
- confirmation-history check coerces vmid/ssh_port like _execute_ssh_tool
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.pve_log import chat as pve_chat_module
from app.ai.pve_log import collector
from app.ai.pve_log import ssh_exec as ssh_exec_module
from app.ai.pve_log.history import merge_pve_messages
from app.ai.pve_log.schemas import SSHConfirmRequest, SSHExecRequest, SSHExecResult


def _patch_vllm(monkeypatch: pytest.MonkeyPatch, responder) -> list[dict]:
    payloads: list[dict] = []

    async def fake_completion(payload, *, timeout, request_id=None):
        del timeout, request_id
        payloads.append(copy.deepcopy(payload))
        return responder(len(payloads) - 1)

    monkeypatch.setattr(
        pve_chat_module,
        "settings",
        SimpleNamespace(
            VLLM_BASE_URL="http://vllm/v1",
            VLLM_MODEL_NAME="test-model",
            VLLM_TIMEOUT=30,
        ),
    )
    monkeypatch.setattr(
        pve_chat_module.vllm_client, "create_chat_completion", fake_completion
    )
    return payloads


def _tool_call_message(*calls: tuple[str, str, str]) -> dict[str, Any]:
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
                        for call_id, name, arguments in calls
                    ],
                }
            }
        ]
    }


def _merge_next_turn(messages: list[dict[str, Any]], **kwargs: Any) -> list[dict]:
    return merge_pve_messages(
        message=None,
        history=[*copy.deepcopy(messages), {"role": "user", "content": "下一題"}],
        server_system_prompt="server",
        allowed_tool_names=pve_chat_module._ALLOWED_TOOL_NAMES,
        **kwargs,
    )


def _cleanup_tokens(*tokens: str) -> None:
    for token in tokens:
        ssh_exec_module._pending_store.pop(token, None)
        ssh_exec_module._completed_store.pop(token, None)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


async def test_deferred_ssh_resumes_after_first_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_vllm(
        monkeypatch,
        lambda _index: _tool_call_message(
            ("ssh-1", "ssh_exec", '{"vmid":101,"command":"df -h","reason":"a"}'),
            ("ssh-2", "ssh_exec", '{"vmid":101,"command":"free -m","reason":"b"}'),
        ),
    )

    async def fake_do_exec(req, **_kwargs):
        return SSHExecResult(vmid=req.vmid, command=req.command, exit_code=0, stdout="ok")

    monkeypatch.setattr(ssh_exec_module, "_do_exec", fake_do_exec)

    tokens: list[str] = []
    try:
        first = await pve_chat_module.chat(message="看磁碟和記憶體")
        assert first.needs_confirmation is True
        tool_messages = [m for m in first.messages if m.get("role") == "tool"]
        pending = json.loads(tool_messages[0]["content"])
        deferred = json.loads(tool_messages[1]["content"])
        assert pending["pending"] is True
        assert deferred["deferred"] is True
        token = pending["confirm_token"]
        tokens.append(token)

        confirmed = await ssh_exec_module.confirm_exec(
            SSHConfirmRequest(token=token, approved=True)
        )
        history = copy.deepcopy(first.messages)
        for item in history:
            if item.get("role") == "tool" and item.get("tool_call_id") == "ssh-1":
                item["content"] = json.dumps(
                    {
                        **confirmed.model_dump(mode="json"),
                        "reason": "a",
                        "confirmation_token": token,
                    }
                )

        # The route calls chat(history=...) without extra flags; it must not 422.
        resumed = await pve_chat_module.chat(history=history)
        assert resumed.needs_confirmation is True
        second = next(
            json.loads(m["content"])
            for m in resumed.messages
            if m.get("role") == "tool" and m.get("tool_call_id") == "ssh-2"
        )
        assert second["pending"] is True
        assert second["command"] == "free -m"
        tokens.append(second["confirm_token"])
    finally:
        _cleanup_tokens(*tokens)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


class _WindowedChannel:
    """Mimics paramiko: the exit status only arrives after output is drained."""

    def __init__(self) -> None:
        self.streams: list[_ChunkedStream] = []

    def recv_exit_status(self) -> int:
        if not all(stream.drained for stream in self.streams):
            raise AssertionError("recv_exit_status() called before output was drained")
        return 3


class _ChunkedStream:
    def __init__(self, channel: _WindowedChannel, total: int) -> None:
        self.channel = channel
        self._remaining = total
        self.drained = False
        channel.streams.append(self)

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            self.drained = True
            return b""
        chunk = min(self._remaining, 65536 if size < 0 else size)
        self._remaining -= chunk
        return b"x" * chunk


def test_ssh_exec_sync_drains_large_output_before_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = _WindowedChannel()
    stdout = _ChunkedStream(channel, 3 * 1024 * 1024)
    stderr = _ChunkedStream(channel, 10)
    closed: list[bool] = []

    class _Client:
        def exec_command(self, *_args, **_kwargs):
            return None, stdout, stderr

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(
        ssh_exec_module, "create_key_client", lambda *_a, **_k: _Client()
    )

    exit_code, out_text, err_text = ssh_exec_module._ssh_exec_sync(
        "10.0.0.1", 22, "root", "key", "journalctl --no-pager", 5
    )

    assert exit_code == 3
    assert stdout.drained and stderr.drained
    assert len(out_text) == ssh_exec_module._MAX_EXEC_OUTPUT_BYTES
    assert err_text == "x" * 10
    assert closed == [True]
    _text, truncated = ssh_exec_module._redact_and_truncate(out_text)
    assert truncated is True


@pytest.mark.parametrize(
    "payload",
    [
        "x" * ssh_exec_module._MAX_EXEC_OUTPUT_BYTES,
        "a-" * (ssh_exec_module._MAX_EXEC_OUTPUT_BYTES // 2),
    ],
    ids=["alnum-run", "dash-run"],
)
def test_redaction_stays_linear_on_long_unbroken_output(payload: str) -> None:
    # Unbounded scheme/flag prefixes backtracked over the whole run from every
    # start position: 256 KB took minutes and pinned the SSH worker thread.
    started = time.monotonic()
    redacted, truncated = ssh_exec_module._redact_and_truncate(payload)
    assert time.monotonic() - started < 10
    assert truncated is True
    assert redacted.startswith(payload[:100])


def test_redaction_still_masks_flags_and_uri_credentials() -> None:
    text = (
        "--db-password=hunter2 --api-key s3cr3t "
        "postgresql://admin:pw123@db:5432/app TOKEN=abc"
    )
    redacted, _ = ssh_exec_module._redact_and_truncate(text)
    for secret in ("hunter2", "s3cr3t", "pw123", "abc"):
        assert secret not in redacted


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


async def test_tool_round_limit_returns_resumable_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_vllm(
        monkeypatch,
        lambda index: _tool_call_message((f"nodes-{index}", "get_nodes", "{}")),
    )
    monkeypatch.setattr(
        collector.PveToolContext, "execute", lambda self, *_a, **_k: []
    )

    response = await pve_chat_module.chat(message="一直查節點")

    assert response.error
    last = response.messages[-1]
    assert last["role"] == "tool"
    _merge_next_turn(response.messages)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


async def test_invalid_model_tool_calls_do_not_poison_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        _tool_call_message(
            ("bad-1", "get_vm_status", "{}"),
            ("ok-1", "get_nodes", "{'node': 'pve1'}"),
        ),
        {"choices": [{"message": {"role": "assistant", "content": "完成"}}]},
    ]
    _patch_vllm(monkeypatch, lambda index: responses[index])
    monkeypatch.setattr(
        collector.PveToolContext, "execute", lambda self, *_a, **_k: []
    )

    response = await pve_chat_module.chat(message="查節點")

    assert response.reply == "完成"
    assistant = next(
        m for m in response.messages if m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert [call["function"]["name"] for call in assistant["tool_calls"]] == ["get_nodes"]
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {
        "node": "pve1"
    }
    _merge_next_turn(response.messages)


def test_all_invalid_tool_calls_are_removed() -> None:
    message = {
        "role": "assistant",
        "content": "嗯",
        "tool_calls": [
            {"id": "x", "type": "function", "function": {"name": "rm_rf"}},
            {"id": "y", "type": "function"},
        ],
    }
    result = pve_chat_module._canonicalize_model_tool_calls(message, reserved_ids=set())
    assert "tool_calls" not in result
    assert result["content"] == "嗯"


def test_non_json_literal_arguments_are_serialized() -> None:
    """ast.literal_eval 退路產生 set 時不可讓 json.dumps 丟 TypeError。"""
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "x",
                "type": "function",
                "function": {"name": "get_nodes", "arguments": "{'a': {1, 2}}"},
            }
        ],
    }
    result = pve_chat_module._canonicalize_model_tool_calls(message, reserved_ids=set())
    arguments = json.loads(result["tool_calls"][0]["function"]["arguments"])
    assert isinstance(arguments, dict)
    assert isinstance(arguments["a"], str)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


class _Endpoint:
    def __init__(self, value: Any) -> None:
        self._value = value

    def get(self, **_kwargs: Any) -> Any:
        return self._value


class _FakeProxmox:
    def __init__(self, *, node: str, vmid: int, cluster_name: str) -> None:
        self.node = node
        self.vmid = vmid
        self.detail_calls: list[int] = []
        self.cluster = SimpleNamespace(
            status=_Endpoint(
                [{"type": "cluster", "name": cluster_name, "nodes": 1, "quorate": 1}]
            ),
            resources=_Endpoint(
                [
                    {
                        "vmid": vmid,
                        "name": f"vm-{vmid}",
                        "type": "qemu",
                        "node": node,
                        "status": "running",
                    }
                ]
            ),
        )

    class _Nodes:
        def __init__(self, owner: _FakeProxmox) -> None:
            self._owner = owner

        def get(self) -> list[dict[str, Any]]:
            return [{"node": self._owner.node, "status": "online"}]

        def __call__(self, node: str) -> Any:
            owner = self._owner
            assert node == owner.node, f"{node} queried on the wrong connection"

            def _guest(vmid: int) -> Any:
                owner.detail_calls.append(vmid)
                return SimpleNamespace(
                    status=SimpleNamespace(
                        current=_Endpoint({"status": "running", "maxmem": 1})
                    ),
                    config=_Endpoint({"name": f"vm-{vmid}"}),
                )

            return SimpleNamespace(storage=_Endpoint([]), qemu=_guest, lxc=_guest)

    @property
    def nodes(self) -> _FakeProxmox._Nodes:
        return _FakeProxmox._Nodes(self)


def _patch_collector_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector.settings, "collector_retry_attempts", 1)
    monkeypatch.setattr(collector.settings, "collector_retry_backoff", 0)
    monkeypatch.setattr(collector.settings, "collector_max_workers", 2)
    monkeypatch.setattr(collector.settings, "collector_fetch_config", True)
    monkeypatch.setattr(collector.settings, "collector_fetch_lxc_interfaces", False)


def test_tool_context_aggregates_all_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_collector_settings(monkeypatch)
    clients = {
        1: _FakeProxmox(node="pve-a", vmid=101, cluster_name="a"),
        2: _FakeProxmox(node="pve-b", vmid=201, cluster_name="b"),
    }
    monkeypatch.setattr(collector, "list_enabled_connection_ids", lambda: [1, 2])
    monkeypatch.setattr(collector, "get_proxmox_api", lambda cid=None: clients[cid])

    def _no_node_lookup(node: str):
        raise AssertionError(f"unexpected node routing lookup for {node}")

    monkeypatch.setattr(collector, "get_proxmox_api_for_node", _no_node_lookup)

    context = collector.PveToolContext()
    resources = context.execute("get_resources", {})
    assert sorted(item["vmid"] for item in resources) == [101, 201]
    assert sorted(item["node"] for item in context.execute("get_nodes", {})) == [
        "pve-a",
        "pve-b",
    ]

    detail = context.execute("get_resource_detail", {"vmid": 201})
    assert detail["summary"]["vmid"] == 201
    assert detail["status"]["status"] == "running"
    assert clients[2].detail_calls and not clients[1].detail_calls

    cluster = context.execute("get_cluster", {})
    assert cluster["node_count"] == 2
    assert cluster["quorate"] is True
    assert context.errors == []


def test_unreachable_connection_is_reported_not_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_collector_settings(monkeypatch)
    healthy = _FakeProxmox(node="pve-a", vmid=101, cluster_name="a")

    def _get_api(cid=None):
        if cid == 2:
            raise OSError("connection 2 down")
        return healthy

    monkeypatch.setattr(collector, "list_enabled_connection_ids", lambda: [1, 2])
    monkeypatch.setattr(collector, "get_proxmox_api", _get_api)

    context = collector.PveToolContext()
    result = pve_chat_module._execute_tool_sync(
        context, "get_resource_detail", {"vmid": 201}
    )
    assert "不存在" in result["error"]
    assert "201" not in result["error"]
    assert context.errors
    assert [item["vmid"] for item in context.execute("get_resources", {})] == [101]


def test_single_default_connection_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_collector_settings(monkeypatch)
    only = _FakeProxmox(node="pve-a", vmid=101, cluster_name="a")
    monkeypatch.setattr(collector, "list_enabled_connection_ids", lambda: [])
    monkeypatch.setattr(collector, "get_proxmox_api", lambda: only)

    context = collector.PveToolContext()
    assert context.execute("get_cluster", {})["cluster_name"] == "a"
    assert [item["vmid"] for item in context.execute("get_resources", {})] == [101]


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


async def test_live_ip_lookup_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = SimpleNamespace(ssh_private_key_encrypted="encrypted")
    monkeypatch.setattr(
        ssh_exec_module.resource_repo, "get_resource_by_vmid", lambda **_k: resource
    )
    monkeypatch.setattr(
        ssh_exec_module.resource_repo, "get_cached_ip_address", lambda **_k: None
    )
    updates: list[dict] = []
    monkeypatch.setattr(
        ssh_exec_module.resource_repo,
        "update_ip_address",
        lambda **kwargs: updates.append(kwargs),
    )

    def slow_find(_vmid: int) -> dict[str, str]:
        time.sleep(0.5)
        return {"node": "pve", "type": "qemu"}

    monkeypatch.setattr(ssh_exec_module.proxmox_service, "find_resource", slow_find)
    monkeypatch.setattr(
        ssh_exec_module.proxmox_service,
        "get_ip_address",
        lambda *_a: "10.0.0.9",
    )
    monkeypatch.setattr(ssh_exec_module, "decrypt_value", lambda _v: "KEY")

    max_gap = 0.0

    async def ticker() -> None:
        nonlocal max_gap
        last = time.perf_counter()
        for _ in range(8):
            await asyncio.sleep(0.05)
            now = time.perf_counter()
            max_gap = max(max_gap, now - last)
            last = now

    result, _ = await asyncio.gather(
        ssh_exec_module._resolve_vm_credentials(101, session=object()),
        ticker(),
    )

    assert result == ("10.0.0.9", "KEY")
    assert updates and updates[0]["ip_address"] == "10.0.0.9"
    assert max_gap < 0.25


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


async def test_string_vmid_confirmation_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SSHExecRequest(vmid=101, command="df -h", require_confirm=True)
    token = ssh_exec_module._store_pending(request, requester_id=None)
    ssh_exec_module.bind_pending_tool_call(token, "ssh-str")

    async def fake_do_exec(req, **_kwargs):
        return SSHExecResult(vmid=req.vmid, command=req.command, exit_code=0, stdout="ok")

    monkeypatch.setattr(ssh_exec_module, "_do_exec", fake_do_exec)
    try:
        result = await ssh_exec_module.confirm_exec(
            SSHConfirmRequest(token=token, approved=True)
        )
        history = [
            {"role": "user", "content": "看磁碟"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "ssh-str",
                        "type": "function",
                        "function": {
                            "name": "ssh_exec",
                            "arguments": '{"vmid":"101","command":"df -h","reason":"x","ssh_port":"22"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "ssh-str",
                "content": json.dumps(
                    {
                        **result.model_dump(mode="json"),
                        "reason": "x",
                        "confirmation_token": token,
                    }
                ),
            },
        ]
        messages = merge_pve_messages(
            message=None,
            history=history,
            server_system_prompt="server",
            allowed_tool_names=pve_chat_module._ALLOWED_TOOL_NAMES,
        )
        pve_chat_module._validate_confirmation_history(
            messages,
            requester_id=None,
            scope_type=None,
            scope_id=None,
            allowed_vmids=None,
        )
    finally:
        _cleanup_tokens(token)
