"""app.infrastructure.proxmox.guest 的分支覆蓋：QEMU guest agent 與 LXC pct exec。"""

from __future__ import annotations

from typing import Any

import pytest

from app.exceptions import AppError
from app.infrastructure.proxmox import guest
from app.infrastructure.proxmox.settings import ProxmoxSettings


class _Agent:
    """假的 ``nodes(node).qemu(vmid).agent(cmd)``。"""

    def __init__(self, api: _QemuApi, cmd: str) -> None:
        self.api = api
        self.cmd = cmd

    def post(self, **kwargs: Any) -> Any:
        self.api.calls.append(("post", self.cmd, kwargs))
        if self.cmd == "ping":
            if self.api.ping_error is not None:
                raise self.api.ping_error
            return None
        if self.cmd == "exec":
            return {"pid": "42"}
        raise AssertionError(self.cmd)

    def get(self, **kwargs: Any) -> Any:
        self.api.calls.append(("get", self.cmd, kwargs))
        if self.cmd == "get-osinfo":
            if isinstance(self.api.osinfo, Exception):
                raise self.api.osinfo
            return self.api.osinfo
        if self.cmd == "exec-status":
            return self.api.statuses.pop(0)
        raise AssertionError(self.cmd)


class _QemuApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.ping_error: Exception | None = None
        self.osinfo: Any = None
        self.statuses: list[dict] = []

    def agent(self, cmd: str) -> _Agent:
        return _Agent(self, cmd)


class _Proxmox:
    def __init__(self, qemu: _QemuApi) -> None:
        self._qemu = qemu
        self.node: str | None = None
        self.vmid: int | None = None

    def nodes(self, node: str) -> _Proxmox:
        self.node = node
        return self

    def qemu(self, vmid: int) -> _QemuApi:
        self.vmid = vmid
        return self._qemu


@pytest.fixture
def qemu(monkeypatch: pytest.MonkeyPatch) -> _QemuApi:
    api = _QemuApi()
    proxmox = _Proxmox(api)
    monkeypatch.setattr(guest, "get_proxmox_api_for_node", lambda _node: proxmox)
    monkeypatch.setattr(guest.time, "sleep", lambda _s: None)
    return api


def test_ping_qemu_agent_true_and_false(qemu: _QemuApi) -> None:
    assert guest.ping_qemu_agent("pve1", 100) is True
    qemu.ping_error = RuntimeError("QEMU guest agent is not running")
    assert guest.ping_qemu_agent("pve1", 100) is False


def test_get_osinfo_accepts_unwrapped_dict(qemu: _QemuApi) -> None:
    qemu.osinfo = {"id": "ubuntu"}
    assert guest.get_osinfo_qemu("pve1", 100) == {"id": "ubuntu"}


@pytest.mark.parametrize(
    "osinfo", [RuntimeError("unsupported"), ["x"], {"result": "x"}]
)
def test_get_osinfo_returns_none_for_errors_and_odd_payloads(
    qemu: _QemuApi, osinfo: Any
) -> None:
    qemu.osinfo = osinfo
    assert guest.get_osinfo_qemu("pve1", 100) is None


def test_exec_qemu_polls_until_exited(qemu: _QemuApi) -> None:
    qemu.statuses = [
        {"exited": 0},
        {"exited": 1, "exitcode": 3, "out-data": "hello", "err-data": None},
    ]

    result = guest.exec_qemu("pve1", 100, ["echo", "hello"], poll_interval=0)

    assert result == (3, "hello", "")
    assert ("post", "exec", {"command": ["echo", "hello"]}) in qemu.calls
    assert ("get", "exec-status", {"pid": 42}) in qemu.calls


def test_exec_qemu_without_agent_is_400(qemu: _QemuApi) -> None:
    qemu.ping_error = RuntimeError("not running")

    with pytest.raises(AppError) as excinfo:
        guest.exec_qemu("pve1", 100, ["true"])

    assert excinfo.value.status_code == 400


def test_exec_qemu_timeout_is_504(
    qemu: _QemuApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = [0.0, 0.5]
    monkeypatch.setattr(guest.time, "monotonic", lambda: ticks.pop(0) if ticks else 5.0)
    qemu.statuses = [{"exited": 0}]

    with pytest.raises(AppError) as excinfo:
        guest.exec_qemu("pve1", 100, ["sleep", "9"], timeout=1.0)

    assert excinfo.value.status_code == 504


class _SshClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _settings(user: str) -> ProxmoxSettings:
    return ProxmoxSettings(
        host="10.0.0.1",
        user=user,
        password="secret",
        verify_ssl=False,
        iso_storage="local",
        data_storage="local-lvm",
        api_timeout=10,
        task_check_interval=1,
        pool_name="SkyLab",
    )


@pytest.mark.parametrize(
    ("user", "node_host", "expected_user", "expected_host"),
    [
        ("root@pam", "10.0.0.21", "root", "10.0.0.21"),
        ("admin", None, "admin", "10.0.0.1"),
    ],
)
def test_exec_lxc_runs_pct_exec_on_node_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
    user: str,
    node_host: str | None,
    expected_user: str,
    expected_host: str,
) -> None:
    ssh = _SshClient()
    seen: dict[str, Any] = {}

    def _create(host: str, port: int, ssh_user: str, password: str, **kwargs: Any):
        seen["create"] = (host, port, ssh_user, password, kwargs)
        return ssh

    def _exec(client: Any, command: str, **kwargs: Any) -> tuple[int, str, str]:
        seen["exec"] = (client, command, kwargs)
        return 0, "ok", ""

    monkeypatch.setattr(guest, "get_connection_id_for_node", lambda _n: 3)
    monkeypatch.setattr(guest, "get_proxmox_settings", lambda _cid: _settings(user))
    monkeypatch.setattr(guest, "get_node_host", lambda _n: node_host)
    monkeypatch.setattr(guest, "get_active_host", lambda _cid: "10.0.0.1")
    monkeypatch.setattr(guest, "create_password_client", _create)
    monkeypatch.setattr(guest, "exec_command", _exec)

    result = guest.exec_lxc("pve1", 101, "echo 'hi'", timeout=5, stdin="pw")

    assert result == (0, "ok", "")
    assert seen["create"] == (
        expected_host,
        22,
        expected_user,
        "secret",
        {"timeout": 30},
    )
    assert seen["exec"][1] == "pct exec 101 -- /bin/sh -c 'echo '\"'\"'hi'\"'\"''"
    assert seen["exec"][2] == {"timeout": 5, "stdin": "pw"}
    assert ssh.closed is True
