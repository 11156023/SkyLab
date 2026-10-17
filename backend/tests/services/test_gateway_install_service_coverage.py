"""Coverage tests for app.services.network.gateway_install_service."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import BadRequestError, ConflictError, UpstreamServiceError
from app.repositories import gateway_config as gw_repo
from app.schemas.gateway import GatewayInstallInterface, GatewayInstallOptions
from app.services.network import gateway_install_service as svc
from app.services.network import gateway_service, ip_management_service

_STATUS_OK = """CLIENT=192.168.100.20
ROOT=1
RUNNING=0
LINK=eth0
ADDR=eth0 192.168.100.5/24
DEFAULT_IFACE=eth0
---SKYLAB-LOG---
done
"""


class _FakeSftp:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.closed = False
        self.uploaded: list[str] = []

    def putfo(self, _fileobj: Any, name: str) -> None:
        if self.fail:
            raise OSError("disk full")
        self.uploaded.append(name)

    def chmod(self, _name: str, _mode: int) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _FakeClient:
    def __init__(self, sftp: _FakeSftp | None = None) -> None:
        self.sftp = sftp or _FakeSftp()
        self.closed = False

    def open_sftp(self) -> _FakeSftp:
        return self.sftp

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------- pure helpers


def test_load_install_script_requires_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(svc, "install_script_path", lambda: tmp_path / "missing.sh")
    with pytest.raises(BadRequestError):
        svc.load_install_script()


def test_parse_datetime_rejects_garbage() -> None:
    assert svc._parse_datetime("not-a-date") is None


def test_parse_status_skips_noise_and_adds_address_only_interfaces() -> None:
    parsed = svc.parse_status_output(
        "garbage line without separator\nADDR=ens19 10.10.0.2/16\n---SKYLAB-LOG---\n"
    )
    assert [iface.name for iface in parsed["interfaces"]] == ["ens19"]


def test_suggest_options_ignores_unparsable_addresses() -> None:
    options = svc.suggest_install_options(
        interfaces=[GatewayInstallInterface(name="eth0", addresses=["bogus"])],
        default_interface="eth0",
        client_ip=None,
        gateway_vm_ip=None,
        forward_port_range=None,
    )
    assert options.ingress_interface == "eth0"
    assert options.vm_interface == "eth1"


# ---------------------------------------------------------------- connect


def _patch_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gateway_service,
        "_get_config",
        lambda session: SimpleNamespace(
            host="gw.example", ssh_port=22, ssh_user="root"
        ),
    )
    monkeypatch.setattr(gw_repo, "get_decrypted_private_key", lambda config: "PEM")


def test_connect_wraps_ssh_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)

    def refuse(*_args: Any) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(gateway_service, "make_client", refuse)

    with pytest.raises(UpstreamServiceError):
        svc._connect(object())


# ---------------------------------------------------------------- status


def test_read_status_raises_when_command_fails_without_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_service, "_exec", lambda client, cmd: (1, "", ""))
    with pytest.raises(UpstreamServiceError):
        svc._read_status(object(), _FakeClient())


def test_get_install_status_uses_subnet_config(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    monkeypatch.setattr(svc, "_connect", lambda session: client)
    monkeypatch.setattr(
        gateway_service, "_exec", lambda client, cmd: (0, _STATUS_OK, "")
    )
    subnet = SimpleNamespace(
        cidr="10.10.0.0/16",
        gateway_vm_ip="10.10.0.2",
        forward_port_start=31000,
        forward_port_end=32000,
    )
    monkeypatch.setattr(
        ip_management_service, "get_subnet_config", lambda session: subnet
    )

    status = svc.get_install_status(object())

    assert client.closed is True
    assert status.state == "idle"
    assert status.root_access is True
    assert status.wireguard_vm_subnet == "10.10.0.0/16"
    assert status.defaults.forward_port_start == 31000
    assert status.defaults.monitoring_allow_from == ["192.168.100.20"]
    assert status.log == "done\n"


# ---------------------------------------------------------------- start


@pytest.fixture
def start_env(monkeypatch: pytest.MonkeyPatch):
    client = _FakeClient()
    monkeypatch.setattr(svc, "load_install_script", lambda: b"#!/bin/bash\n")
    monkeypatch.setattr(
        ip_management_service, "get_subnet_config", lambda session: None
    )
    monkeypatch.setattr(svc, "_connect", lambda session: client)
    commands: list[str] = []
    state: dict[str, Any] = {"start": (0, "ROOT=1\nSKYLAB_INSTALL_STARTED\n", "")}

    def fake_exec(_client: Any, command: str) -> tuple[int, str, str]:
        commands.append(command)
        if command.startswith("rm -f"):
            return 0, "", ""
        if "SKYLAB_INSTALL_STARTED" in command and len(commands) == 1:
            result = state["start"]
            if isinstance(result, Exception):
                raise result
            return result
        return 0, _STATUS_OK, ""

    monkeypatch.setattr(gateway_service, "_exec", fake_exec)
    return SimpleNamespace(client=client, commands=commands, state=state)


def test_start_install_returns_status_after_launch(start_env) -> None:
    status = svc.start_install(object(), GatewayInstallOptions())

    assert status.state == "idle"
    assert start_env.client.sftp.uploaded == [svc.UPLOAD_NAME]
    assert start_env.client.sftp.closed is True
    assert start_env.client.closed is True
    assert len(start_env.commands) == 2


@pytest.mark.parametrize(
    ("result", "error"),
    [
        ((0, "ROOT=0\n", ""), BadRequestError),
        ((0, "ROOT=1\nSKYLAB_INSTALL_BUSY\n", ""), ConflictError),
        ((0, "ROOT=1\nSKYLAB_INSTALL_NO_SYSTEMD\n", ""), BadRequestError),
        ((1, "ROOT=1\n", "boom"), UpstreamServiceError),
        ((0, "", ""), UpstreamServiceError),
        (RuntimeError("channel closed"), UpstreamServiceError),
    ],
)
def test_start_install_maps_remote_outcomes(start_env, result, error) -> None:
    start_env.state["start"] = result

    with pytest.raises(error):
        svc.start_install(object(), GatewayInstallOptions())

    assert start_env.client.closed is True
