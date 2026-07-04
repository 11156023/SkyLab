"""guest 檔案寫入測試（mock proxmoxer / SSH）。"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from app.exceptions import AppError, BadRequestError
from app.infrastructure.proxmox import guest


class _AgentApi:
    """記錄 agent 呼叫的假 proxmoxer 鏈。"""

    def __init__(self, calls: dict, ping_fails: bool = False) -> None:
        self._calls = calls
        self._ping_fails = ping_fails

    def nodes(self, node):
        self._calls["node"] = node
        return self

    def qemu(self, vmid):
        self._calls["vmid"] = vmid
        return self

    def agent(self, cmd):
        self._calls.setdefault("agent_cmds", []).append(cmd)
        self._current = cmd
        return self

    def post(self, **params):
        if self._current == "ping" and self._ping_fails:
            raise RuntimeError("agent not running")
        self._calls.setdefault("posts", []).append((self._current, params))
        return {}


def test_validate_target_path_rejects_relative() -> None:
    with pytest.raises(BadRequestError):
        guest.validate_target_path("etc/app.conf")


def test_validate_target_path_rejects_traversal() -> None:
    with pytest.raises(BadRequestError):
        guest.validate_target_path("/etc/../root/x")


def test_write_file_qemu_base64_and_encode_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict = {}
    monkeypatch.setattr(guest, "get_proxmox_api", lambda: _AgentApi(calls))
    guest.write_file_qemu("pve1", 101, "/etc/app.conf", b"hello")
    cmd, params = calls["posts"][-1]
    assert cmd == "file-write"
    assert params["file"] == "/etc/app.conf"
    assert base64.b64decode(params["content"]) == b"hello"
    assert params["encode"] == 0
    assert "ping" in calls["agent_cmds"]


def test_write_file_qemu_agent_down_readable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        guest, "get_proxmox_api", lambda: _AgentApi({}, ping_fails=True)
    )
    with pytest.raises(AppError) as exc_info:
        guest.write_file_qemu("pve1", 101, "/etc/app.conf", b"hello")
    assert "guest agent" in exc_info.value.message.lower()


def test_write_file_lxc_pushes_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []
    written: dict = {}

    class _Sftp:
        def file(self, path, mode):
            written["path"] = path

            class _F:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    return False

                def write(self_inner, data):
                    written["data"] = data

            return _F()

        def close(self) -> None: ...

    class _Client:
        def open_sftp(self):
            return _Sftp()

        def close(self) -> None:
            written["closed"] = True

    monkeypatch.setattr(guest, "_node_ssh_client", lambda: _Client())
    monkeypatch.setattr(
        guest,
        "exec_command",
        lambda client, cmd, timeout=None: executed.append(cmd) or (0, "", ""),
    )
    guest.write_file_lxc("pve1", 102, "/etc/app.conf", b"hello")
    assert written["data"] == b"hello"
    assert any("pct push 102" in cmd for cmd in executed)
    assert any(cmd.startswith("rm -f ") for cmd in executed)
    assert written.get("closed") is True


def test_write_file_lxc_nonzero_exit_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Sftp:
        def file(self, path, mode):
            class _F:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    return False

                def write(self_inner, data): ...

            return _F()

        def close(self) -> None: ...

    class _Client:
        def open_sftp(self):
            return _Sftp()

        def close(self) -> None: ...

    monkeypatch.setattr(guest, "_node_ssh_client", lambda: _Client())

    def _exec(client, cmd, timeout=None):
        if "pct push" in cmd:
            return 1, "", "CT 102 not running"
        return 0, "", ""

    monkeypatch.setattr(guest, "exec_command", _exec)
    with pytest.raises(AppError) as exc_info:
        guest.write_file_lxc("pve1", 102, "/etc/app.conf", b"hello")
    assert "not running" in exc_info.value.message
