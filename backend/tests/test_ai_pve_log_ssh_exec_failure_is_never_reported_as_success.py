"""SSH 執行失敗時不可回成 exit_code=0／空 error 的「成功」結果。

- 指令長時間沒有輸出、讀取撞到 channel timeout：回明確的逾時錯誤與 exit_code=-1。
- 連線階段拋出 str() 為空字串的例外：error 改用例外類別名稱，exit_code=-1。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.pve_log import ssh_exec as ssh_exec_module
from app.ai.pve_log.schemas import SSHExecRequest


class _SilentStream:
    """模擬 paramiko ChannelFile：遠端一直沒輸出，recv 撞到 channel timeout。"""

    def __init__(self) -> None:
        self.channel = self

    def read(self, _size: int = -1) -> bytes:
        # paramiko 拋的是 socket.timeout，也就是 TimeoutError（且 str() 為空字串）
        raise TimeoutError()

    def recv_exit_status(self) -> int:
        raise AssertionError("讀取逾時後不該再等 exit status")


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    async def fake_resolve(_vmid: int, *, session: Any) -> tuple[str, str]:
        return "10.0.0.1", "key"

    outcomes: list[str] = []

    def fake_audit(**kwargs: Any) -> None:
        outcomes.append(kwargs["outcome"])

    monkeypatch.setattr(ssh_exec_module, "_resolve_vm_credentials", fake_resolve)
    monkeypatch.setattr(ssh_exec_module, "_log_ssh_audit", fake_audit)
    return outcomes


async def test_silent_command_hitting_channel_timeout_returns_explicit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = _patch_common(monkeypatch)
    closed: list[bool] = []

    class _Client:
        def exec_command(self, *_args: Any, **_kwargs: Any):
            return None, _SilentStream(), _SilentStream()

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(
        ssh_exec_module, "create_key_client", lambda *_a, **_k: _Client()
    )

    result = await ssh_exec_module._do_exec(
        SSHExecRequest(vmid=101, command="sleep 40; echo done"),
        session=None,
    )

    assert result.exit_code != 0
    assert result.exit_code == -1
    assert result.error
    assert result.error == ssh_exec_module.t(
        "pveLog.sshExecTimeout", seconds=ssh_exec_module.settings.ssh_timeout
    )
    assert result.stdout == ""
    assert closed == [True]
    assert outcomes == ["host=10.0.0.1 timeout"]


async def test_exception_with_empty_message_still_reports_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = _patch_common(monkeypatch)

    def fail_connect(*_args: Any, **_kwargs: Any) -> Any:
        # 連線階段的逾時：指令根本沒送出，不應被說成「指令逾時」
        raise TimeoutError()

    monkeypatch.setattr(ssh_exec_module, "create_key_client", fail_connect)

    result = await ssh_exec_module._do_exec(
        SSHExecRequest(vmid=101, command="uptime"),
        session=None,
    )

    assert result.exit_code == -1
    assert result.error == "TimeoutError"
    assert outcomes == ["host=10.0.0.1 error:TimeoutError"]
