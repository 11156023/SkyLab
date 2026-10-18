"""Coverage tests for app.ai.pve_log.ssh_exec confirmation, probes and execution."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.pve_log import ssh_exec as ssh_mod
from app.ai.pve_log.guest_diagnostics import (
    ERROR_CODE_COMMAND_FAILED,
    ERROR_CODE_CONNECTION_FAILED,
    ERROR_CODE_PROBE_TIMEOUT,
    GuestProbe,
)
from app.ai.pve_log.schemas import SSHConfirmRequest, SSHExecRequest, SSHExecResult


@pytest.fixture(autouse=True)
def _fresh_stores(monkeypatch):
    monkeypatch.setattr(ssh_mod, "_pending_store", {})
    monkeypatch.setattr(ssh_mod, "_completed_store", {})


class _Session:
    def __init__(self) -> None:
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1


class _Stream:
    def __init__(self, chunks: list[Any], exit_code: int = 0) -> None:
        self._chunks = list(chunks)
        self.channel = SimpleNamespace(recv_exit_status=lambda: exit_code)

    def read(self, _size: int) -> Any:
        return self._chunks.pop(0) if self._chunks else b""


class _Client:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs
        self.closed = False

    def exec_command(self, command: str, timeout: int):
        del timeout
        value = self.outputs[command]
        if isinstance(value, Exception):
            raise value
        stdout, stderr, code = value
        return None, _Stream(stdout, code), _Stream(stderr)

    def close(self) -> None:
        self.closed = True


_PROBE_A = GuestProbe(name="a", command="cmd-a", max_output_bytes=100, exec_timeout=1)
_PROBE_B = GuestProbe(name="b", command="cmd-b", max_output_bytes=100, exec_timeout=1)
_PROBE_C = GuestProbe(name="c", command="cmd-c", max_output_bytes=100, exec_timeout=1)


# ── 稽核 ────────────────────────────────────────────────────────────────


def test_audit_written_and_failures_rolled_back(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        ssh_mod.audit_service, "log_action", lambda **kwargs: calls.append(kwargs)
    )
    session = _Session()
    kwargs = dict(
        session=session,
        requester_id=None,
        vmid=101,
        ssh_user="root",
        command="ls",
        confirmed=True,
        outcome="ok",
    )
    ssh_mod._log_ssh_audit(blocked=True, **kwargs)
    ssh_mod._log_ssh_audit(blocked=False, **kwargs)
    assert [c["action"] for c in calls] == ["ai_ssh_exec_blocked", "ai_ssh_exec"]

    def broken(**_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(ssh_mod.audit_service, "log_action", broken)
    ssh_mod._log_ssh_audit(blocked=False, **kwargs)
    assert session.rollbacks == 1


# ── pending / completed stores ──────────────────────────────────────────


def test_store_helpers_and_expiry(monkeypatch) -> None:
    assert ssh_mod.bind_pending_tool_call("missing", "call-1") is False
    req = SSHExecRequest(vmid=1, command="ls")
    token = ssh_mod._store_pending(req)
    assert ssh_mod.bind_pending_tool_call(token, "") is False
    assert ssh_mod.bind_pending_tool_call(token, "call-1") is True

    assert ssh_mod.consume_completed_confirmation("missing") is None
    entry = ssh_mod._pop_pending(token)
    ssh_mod._store_completed(token, entry=entry, result=SSHExecResult(vmid=1))
    assert ssh_mod.find_completed_confirmation_by_tool_call("call-1")["token"] == token
    assert ssh_mod.find_completed_confirmation_by_tool_call("other") is None
    assert ssh_mod.consume_completed_confirmation(token)["consumed"] is True
    assert ssh_mod.consume_completed_confirmation(token) is None

    ssh_mod._store_pending(req)
    real_monotonic = ssh_mod.time.monotonic
    monkeypatch.setattr(
        ssh_mod.time, "monotonic", lambda: real_monotonic() + ssh_mod._PENDING_TTL + 5
    )
    ssh_mod._cleanup_expired()
    assert ssh_mod._pending_store == {}
    assert ssh_mod._completed_store == {}


# ── 讀取與 probe 批次 ────────────────────────────────────────────────────


def test_read_limited_keeps_partial_chunk_and_drains_rest() -> None:
    text, truncated = ssh_mod._read_limited(_Stream([b"abcdef", "gh", b"ij"]), 4)
    assert text == "abcd"
    assert truncated is True


def test_probe_batch_handles_success_timeout_and_failure(monkeypatch) -> None:
    client = _Client(
        {
            "cmd-a": ([b"out"], [b"err"], 3),
            "cmd-b": TimeoutError(),
            "cmd-c": OSError("boom"),
        }
    )
    monkeypatch.setattr(ssh_mod, "create_key_client", lambda *_a, **_k: client)
    results = ssh_mod._run_probe_batch_sync(
        "10.0.0.1",
        "key",
        (_PROBE_A, _PROBE_B, _PROBE_C),
        connect_timeout=5,
        ssh_user="root",
        ssh_port=22,
    )
    assert results["a"].exit_code == 3
    assert results["a"].stdout == "out"
    assert results["a"].stderr == "err"
    assert results["b"].error_code == ERROR_CODE_PROBE_TIMEOUT
    assert results["c"].error_code == ERROR_CODE_COMMAND_FAILED
    assert client.closed is True


async def test_run_guest_probe_batch_paths(monkeypatch) -> None:
    with pytest.raises(ValueError):
        await ssh_mod.run_guest_probe_batch(5, (_PROBE_A,), allowed_vmids={6})

    # session=None：憑證解析失敗
    failed = await ssh_mod.run_guest_probe_batch(5, (_PROBE_A,))
    assert failed["a"].error_code == ssh_mod.ERROR_CODE_RESOLVE_FAILED

    async def resolved(_vmid, *, session):
        del session
        return "10.0.0.5", "key"

    monkeypatch.setattr(ssh_mod, "_resolve_vm_credentials", resolved)
    client = _Client({"cmd-a": ([b"ok"], [], 0)})
    monkeypatch.setattr(ssh_mod, "create_key_client", lambda *_a, **_k: client)
    ok = await ssh_mod.run_guest_probe_batch(5, (_PROBE_A,), allowed_vmids={5})
    assert ok["a"].stdout == "ok"

    def refuse(*_a, **_k):
        raise OSError("refused")

    monkeypatch.setattr(ssh_mod, "create_key_client", refuse)
    down = await ssh_mod.run_guest_probe_batch(5, (_PROBE_A,))
    assert down["a"].error_code == ERROR_CODE_CONNECTION_FAILED


# ── VM 連線資訊解析 ──────────────────────────────────────────────────────


def test_lookup_live_ip_swallows_proxmox_errors(monkeypatch) -> None:
    def broken(_vmid):
        raise RuntimeError("pve down")

    monkeypatch.setattr(ssh_mod.proxmox_service, "find_resource", broken)
    assert ssh_mod._lookup_live_ip(101) is None


def test_cache_live_ip_rolls_back_on_failure(monkeypatch) -> None:
    def broken(**_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(ssh_mod.resource_repo, "update_ip_address", broken)
    session = _Session()
    ssh_mod._cache_live_ip(session, 101, "10.0.0.9")
    assert session.rollbacks == 1


async def test_resolve_vm_info_requires_resource_and_key(monkeypatch) -> None:
    monkeypatch.setattr(
        ssh_mod.resource_repo, "get_resource_by_vmid", lambda **_k: None
    )
    with pytest.raises(RuntimeError):
        await ssh_mod._resolve_vm_info_from_db(_Session(), 101)

    monkeypatch.setattr(
        ssh_mod.resource_repo,
        "get_resource_by_vmid",
        lambda **_k: SimpleNamespace(ssh_private_key_encrypted=None),
    )
    monkeypatch.setattr(
        ssh_mod.resource_repo, "get_cached_ip_address", lambda **_k: "10.0.0.2"
    )
    with pytest.raises(RuntimeError):
        await ssh_mod._resolve_vm_info_from_db(_Session(), 101)


# ── ssh_exec / confirm_exec / _do_exec ──────────────────────────────────


def _patch_exec(monkeypatch, outputs: dict[str, Any]) -> list[str]:
    async def resolved(_vmid, *, session):
        del session
        return "10.0.0.7", "key"

    outcomes: list[str] = []
    monkeypatch.setattr(ssh_mod, "_resolve_vm_credentials", resolved)
    monkeypatch.setattr(
        ssh_mod, "_log_ssh_audit", lambda **kwargs: outcomes.append(kwargs["outcome"])
    )
    monkeypatch.setattr(
        ssh_mod, "create_key_client", lambda *_a, **_k: _Client(outputs)
    )
    return outcomes


async def test_ssh_exec_guard_block_and_direct_execution(monkeypatch) -> None:
    outcomes = _patch_exec(monkeypatch, {"echo hi": ([b"hi\n"], [b""], 0)})
    blocked = await ssh_mod.ssh_exec(SSHExecRequest(vmid=1, command="rm -rf /"))
    assert blocked.blocked is True
    assert outcomes[0].startswith("guard:")

    result = await ssh_mod.ssh_exec(SSHExecRequest(vmid=1, command="echo hi"))
    assert result.exit_code == 0
    assert result.stdout == "hi\n"
    assert result.host == "10.0.0.7"
    assert outcomes[-1] == "host=10.0.0.7 exit_code=0"


async def test_do_exec_rejects_out_of_scope_vmid(monkeypatch) -> None:
    outcomes = _patch_exec(monkeypatch, {})
    result = await ssh_mod._do_exec(
        SSHExecRequest(vmid=9, command="ls"), allowed_vmids={1}
    )
    assert result.blocked is True
    assert outcomes == ["scope_restricted"]


async def test_confirm_exec_token_validation() -> None:
    missing = await ssh_mod.confirm_exec(SSHConfirmRequest(approved=True))
    assert missing.error
    invalid = await ssh_mod.confirm_exec(SSHConfirmRequest(token="nope", approved=True))
    assert invalid.error

    token = ssh_mod._store_pending(
        SSHExecRequest(vmid=1, command="ls"), requester_id=uuid.uuid4()
    )
    mismatch = await ssh_mod.confirm_exec(
        SSHConfirmRequest(token=token, approved=True), requester_id=uuid.uuid4()
    )
    assert mismatch.error
    assert token in ssh_mod._pending_store


async def test_confirm_exec_token_consumed_concurrently(monkeypatch) -> None:
    token = ssh_mod._store_pending(SSHExecRequest(vmid=1, command="ls"))
    monkeypatch.setattr(ssh_mod, "_pop_pending", lambda _token: None)
    result = await ssh_mod.confirm_exec(
        SSHConfirmRequest(confirm_token=token, approved=True)
    )
    assert result.error


async def test_confirm_exec_rejection_and_overrides(monkeypatch) -> None:
    outcomes = _patch_exec(monkeypatch, {"uptime": ([b"up"], [], 0)})

    rejected_token = ssh_mod._store_pending(SSHExecRequest(vmid=1, command="ls"))
    rejected = await ssh_mod.confirm_exec(
        SSHConfirmRequest(token=rejected_token, approved=False)
    )
    assert rejected.error
    assert ssh_mod._completed_store[rejected_token]["result"]["error"]

    blocked_token = ssh_mod._store_pending(SSHExecRequest(vmid=1, command="ls"))
    blocked = await ssh_mod.confirm_exec(
        SSHConfirmRequest(token=blocked_token, approved=True, command="reboot")
    )
    assert blocked.blocked is True
    assert blocked.command == "reboot"

    override_token = ssh_mod._store_pending(SSHExecRequest(vmid=1, command="ls"))
    override = await ssh_mod.confirm_exec(
        SSHConfirmRequest(token=override_token, approved=True, command=" uptime ")
    )
    assert override.command == "uptime"
    assert override.stdout == "up"
    assert outcomes == [
        "user_rejected",
        outcomes[1],
        "host=10.0.0.7 exit_code=0",
    ]
    assert outcomes[1].startswith("guard:")
