from __future__ import annotations

import time

import pytest

from app.ai.pve_log import ssh_exec as ssh_exec_module
from app.ai.pve_log.schemas import SSHConfirmRequest, SSHExecRequest, SSHExecResult


@pytest.mark.asyncio
async def test_ssh_exec_blocks_out_of_scope_before_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ssh_exec_module,
        "_store_pending",
        lambda *args, **kwargs: pytest.fail("should not store pending for out-of-scope vmid"),
    )
    monkeypatch.setattr(
        ssh_exec_module,
        "_do_exec",
        lambda *args, **kwargs: pytest.fail("should not execute out-of-scope vmid"),
    )

    result = await ssh_exec_module.ssh_exec(
        SSHExecRequest(vmid=999, command="python3 -V", require_confirm=True),
        allowed_vmids={157},
    )

    assert result.blocked is True
    assert result.pending is False
    assert result.block_reason == "目前只允許存取所在群組內的 VM/LXC"


@pytest.mark.asyncio
async def test_confirm_exec_keeps_allowed_vm_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_do_exec(
        req: SSHExecRequest,
        *,
        session=None,
        allowed_vmids=None,
    ) -> SSHExecResult:
        captured["allowed_vmids"] = allowed_vmids
        return SSHExecResult(vmid=req.vmid, command=req.command, host="127.0.0.1")

    monkeypatch.setattr(ssh_exec_module, "_do_exec", _fake_do_exec)
    token = "token-1"
    ssh_exec_module._pending_store[token] = {
        "request": SSHExecRequest(vmid=157, command="python3 -V"),
        "created_at": time.monotonic(),
        "allowed_vmids": {157},
    }

    try:
        result = await ssh_exec_module.confirm_exec(
            SSHConfirmRequest(token=token, approved=True)
        )
    finally:
        ssh_exec_module._pending_store.pop(token, None)

    assert result.vmid == 157
    assert captured["allowed_vmids"] == {157}
