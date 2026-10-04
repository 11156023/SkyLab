"""guest_ssh_login：sshd 設定腳本的實際行為，以及 QEMU 背景套用流程。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.infrastructure.proxmox import guest
from app.services.resource import guest_ssh_login
from app.services.template import clone_service

_SH = shutil.which("sh")
needs_sh = pytest.mark.skipif(_SH is None, reason="needs a POSIX sh")


def _run(root: Path) -> None:
    script = guest_ssh_login.ssh_login_script(root=root.as_posix(), reload=False)
    result = subprocess.run([_SH or "sh", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _ssh_dir(root: Path, config: str) -> Path:
    ssh = root / "etc" / "ssh"
    ssh.mkdir(parents=True)
    (ssh / "sshd_config").write_text(config, newline="\n")
    return ssh


@needs_sh
def test_writes_drop_in_when_config_includes_directory(tmp_path: Path) -> None:
    original = (
        "Include /etc/ssh/sshd_config.d/*.conf\n"
        "#PermitRootLogin prohibit-password\n"
    )
    ssh = _ssh_dir(tmp_path, original)

    _run(tmp_path)

    drop_in = (ssh / "sshd_config.d" / "00-skylab-login.conf").read_text()
    assert "PermitRootLogin yes" in drop_in
    assert "PasswordAuthentication yes" in drop_in
    assert (ssh / "sshd_config").read_text() == original
    assert (ssh / ".skylab-ssh-login").exists()


@needs_sh
def test_prepends_to_config_without_include(tmp_path: Path) -> None:
    ssh = _ssh_dir(tmp_path, "PermitRootLogin prohibit-password\nPasswordAuthentication no\n")

    _run(tmp_path)

    lines = (ssh / "sshd_config").read_text().splitlines()
    settings = [line for line in lines if not line.startswith("#")]
    # sshd 取第一次出現的值：平台設定要排在原本的設定前面
    assert settings[:2] == ["PermitRootLogin yes", "PasswordAuthentication yes"]
    assert "PasswordAuthentication no" in lines
    assert not (ssh / "sshd_config.skylab").exists()
    assert not (ssh / "sshd_config.d").exists()


@needs_sh
def test_runs_only_once_so_user_changes_are_kept(tmp_path: Path) -> None:
    ssh = _ssh_dir(tmp_path, "Include /etc/ssh/sshd_config.d/*.conf\n")
    _run(tmp_path)
    drop_in = ssh / "sshd_config.d" / "00-skylab-login.conf"
    drop_in.unlink()

    _run(tmp_path)

    assert not drop_in.exists()


@needs_sh
def test_without_sshd_does_nothing(tmp_path: Path) -> None:
    _run(tmp_path)

    assert not (tmp_path / "etc").exists()


def test_lxc_key_sync_also_opens_ssh_login(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[str] = []
    monkeypatch.setattr(
        guest,
        "exec_lxc",
        lambda node, vmid, command, **kw: commands.append(command) or (0, "", ""),
    )

    assert clone_service.inject_lxc_platform_key("pve1", 300, "ssh-ed25519 AAAA k")
    assert "00-skylab-login.conf" in commands[0]
    assert "/root/.ssh/authorized_keys" in commands[0]


def _fake_qemu(
    monkeypatch: pytest.MonkeyPatch, *, agent: str = "1", osid: str = "ubuntu"
) -> list[list[str]]:
    executed: list[list[str]] = []
    monkeypatch.setattr(
        guest_ssh_login.proxmox_ops, "get_config", lambda *a, **k: {"agent": agent}
    )
    monkeypatch.setattr(guest_ssh_login.guest, "ping_qemu_agent", lambda n, v: True)
    monkeypatch.setattr(
        guest_ssh_login.guest, "get_osinfo_qemu", lambda n, v: {"id": osid}
    )

    def fake_exec(node: str, vmid: int, command: list[str], **kw: Any) -> Any:
        executed.append(command)
        return 0, "", ""

    monkeypatch.setattr(guest_ssh_login.guest, "exec_qemu", fake_exec)
    return executed


def test_qemu_runs_script_after_cloud_init(monkeypatch: pytest.MonkeyPatch) -> None:
    executed = _fake_qemu(monkeypatch, agent="1,fstrim_cloned_disks=1")

    assert guest_ssh_login.enable_qemu_ssh_login("pve1", 400)
    command = executed[0][2]
    assert command.index("cloud-init status --wait") < command.index("00-skylab-login.conf")


@pytest.mark.parametrize(
    ("agent", "osid"), [("0", "ubuntu"), ("", "ubuntu"), ("1", "mswindows")]
)
def test_qemu_skips_without_agent_or_on_windows(
    monkeypatch: pytest.MonkeyPatch, agent: str, osid: str
) -> None:
    executed = _fake_qemu(monkeypatch, agent=agent, osid=osid)

    assert not guest_ssh_login.enable_qemu_ssh_login("pve1", 401)
    assert executed == []


def test_qemu_agent_errors_do_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_qemu(monkeypatch)

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("agent gone")

    monkeypatch.setattr(guest_ssh_login.guest, "exec_qemu", boom)

    assert not guest_ssh_login.enable_qemu_ssh_login("pve1", 402)


def test_schedule_only_for_qemu_and_not_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple[Any, ...]] = []

    class FakeThread:
        def __init__(self, *, target: Any, args: tuple[Any, ...], **kw: Any) -> None:
            self.args = args

        def start(self) -> None:
            started.append(self.args)

    monkeypatch.setattr(guest_ssh_login.threading, "Thread", FakeThread)
    guest_ssh_login._pending.clear()

    guest_ssh_login.schedule_after_start("pve1", 500, "lxc")
    guest_ssh_login.schedule_after_start("pve1", 501, "qemu")
    guest_ssh_login.schedule_after_start("pve1", 501, "qemu")

    assert started == [("pve1", 501)]
    guest_ssh_login._pending.clear()
