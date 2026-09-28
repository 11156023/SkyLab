from __future__ import annotations

import shutil
import subprocess

import pytest
from pydantic import ValidationError

from app.schemas.gateway import GatewayInstallInterface, GatewayInstallOptions
from app.services.network import gateway_install_service as svc

_STATUS_HEAD = """CLIENT=192.168.100.20
ROOT=1
RUNNING=0
EXIT=0
FINISHED=2026-09-27T03:10:00Z
STARTED=2026-09-27T03:05:12Z
HAS_LOG=1
OS=Debian GNU/Linux 13 (trixie)
LINK=eth0
LINK=eth1
LINK=veth9@if5
LINK=wg0
ADDR=lo 127.0.0.1/8
ADDR=eth0 192.168.100.5/24
ADDR=eth1 10.10.0.2/16
ADDR=wg0 10.250.0.1/16
DEFAULT_IFACE=eth0
COMPONENT=nginx 1
COMPONENT=wg 1
COMPONENT=certbot 0
COMPONENT=ufw 1
"""


def _status(head: str, log: str = "") -> str:
    return f"{head}---SKYLAB-LOG---\n{log}"


def test_parse_finished_status_with_interfaces_and_log() -> None:
    parsed = svc.parse_status_output(
        _status(_STATUS_HEAD, "\x1b[0;32m[INFO]\x1b[0m nginx 安裝完成\n")
    )

    assert parsed["state"] == "succeeded"
    assert parsed["root_access"] is True
    assert parsed["client_ip"] == "192.168.100.20"
    assert parsed["exit_code"] == 0
    assert parsed["started_at"].isoformat() == "2026-09-27T03:05:12+00:00"
    assert parsed["os_name"] == "Debian GNU/Linux 13 (trixie)"
    assert parsed["default_interface"] == "eth0"
    names = [iface.name for iface in parsed["interfaces"]]
    # veth 的 @ifN 後綴要去掉、lo 不列
    assert names == ["eth0", "eth1", "veth9", "wg0"]
    assert parsed["interfaces"][1].addresses == ["10.10.0.2/16"]
    assert parsed["components"] == {
        "nginx": True,
        "wireguard": True,
        "certbot": False,
        "ufw": True,
    }
    # ANSI 顏色碼要剝掉
    assert parsed["log"] == "[INFO] nginx 安裝完成\n"


@pytest.mark.parametrize(
    ("replacements", "expected"),
    [
        ({"RUNNING=0": "RUNNING=1"}, "running"),
        ({"EXIT=0": "EXIT=1"}, "failed"),
        ({"EXIT=0\n": "", "FINISHED=2026-09-27T03:10:00Z\n": ""}, "interrupted"),
        (
            {
                "EXIT=0\n": "",
                "FINISHED=2026-09-27T03:10:00Z\n": "",
                "HAS_LOG=1\n": "",
            },
            "idle",
        ),
    ],
)
def test_parse_install_states(replacements: dict[str, str], expected: str) -> None:
    head = _STATUS_HEAD
    for old, new in replacements.items():
        head = head.replace(old, new)
    parsed = svc.parse_status_output(_status(head))
    assert parsed["state"] == expected
    if expected == "running":
        # 還在跑時上一輪的結束碼不算數
        assert parsed["exit_code"] is None
        assert parsed["finished_at"] is None


def test_parse_status_without_root() -> None:
    parsed = svc.parse_status_output("CLIENT=10.0.0.9\nROOT=0\n")
    assert parsed["root_access"] is False
    assert parsed["state"] == "idle"
    assert parsed["interfaces"] == []


def _ifaces() -> list[GatewayInstallInterface]:
    return [
        GatewayInstallInterface(name="ens18", addresses=["192.168.100.5/24"]),
        GatewayInstallInterface(name="ens19", addresses=["10.10.0.2/16"]),
    ]


def test_suggest_options_matches_gateway_vm_ip_and_default_route() -> None:
    options = svc.suggest_install_options(
        interfaces=_ifaces(),
        default_interface="ens18",
        client_ip="192.168.100.20",
        gateway_vm_ip="10.10.0.2",
        forward_port_range=(40000, 40999),
    )
    assert options.ingress_interface == "ens18"
    assert options.vm_interface == "ens19"
    assert options.snat_address == "10.10.0.2"
    assert (options.forward_port_start, options.forward_port_end) == (40000, 40999)
    assert options.monitoring_allow_from == ["192.168.100.20"]
    assert options.listen_port == svc.settings.WIREGUARD_ENDPOINT_PORT


def test_suggest_options_falls_back_to_wireguard_vm_subnet() -> None:
    options = svc.suggest_install_options(
        interfaces=_ifaces(),
        default_interface=None,
        client_ip="not-an-ip",
        gateway_vm_ip=None,
        forward_port_range=None,
    )
    assert options.vm_interface == "ens19"
    assert options.ingress_interface == "ens18"
    assert (options.forward_port_start, options.forward_port_end) == (30000, 39999)
    assert options.monitoring_allow_from == []


def test_build_env_follows_backend_wireguard_settings() -> None:
    env = svc.build_env(
        GatewayInstallOptions(
            ingress_interface="ens18",
            vm_interface="ens19",
            snat_address="10.10.0.2",
            listen_port=51821,
            forward_port_start=30000,
            forward_port_end=39999,
            monitoring_allow_from=["192.168.100.20", "10.1.0.0/16"],
        )
    )
    settings = svc.settings
    assert env["WG_INTERFACE"] == settings.WIREGUARD_INTERFACE
    assert env["WG_CLIENT_SUBNET"] == settings.WIREGUARD_CLIENT_SUBNET
    assert env["WG_VM_SUBNET"] == settings.WIREGUARD_VM_SUBNET
    assert env["WG_ADDRESS"] == "10.250.0.1/16"
    assert env["FORWARD_PORT_RANGE"] == "30000:39999"
    assert env["MONITORING_ALLOW_FROM"] == "192.168.100.20/32,10.1.0.0/16"
    assert env["WG_INGRESS_INTERFACE"] == "ens18"
    assert env["WG_VM_INTERFACE"] == "ens19"


@pytest.mark.parametrize(
    "overrides",
    [
        {"vm_interface": "eth1; rm -rf /"},
        {"ingress_interface": "$(reboot)"},
        {"snat_address": "10.10.0.0/16"},
        {"monitoring_allow_from": ["1.2.3.4 && id"]},
        {"forward_port_start": 40000, "forward_port_end": 30000},
        {"listen_port": 30001},
    ],
)
def test_install_options_reject_unsafe_or_invalid_values(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        GatewayInstallOptions(**overrides)


def test_normalize_script_strips_bom_and_crlf() -> None:
    assert svc.normalize_script(b"\xef\xbb\xbf#!/bin/bash\r\necho ok\r\n") == (
        b"#!/bin/bash\necho ok\n"
    )


def test_repository_install_script_is_loadable() -> None:
    script = svc.load_install_script()
    assert script.startswith(b"#!/usr/bin/env bash\n")
    assert b"\r\n" not in script


def test_start_command_runs_install_through_systemd_with_env() -> None:
    command = svc.build_start_command({"WG_VM_INTERFACE": "ens19", "HOME": "/root"})
    assert "systemd-run --unit=skylab-gateway-install --collect" in command
    assert "--setenv=WG_VM_INTERFACE=ens19" in command
    # 上傳檔路徑由外層 shell 展開 $HOME，不能被單引號包成字面值
    assert command.count('skylab-install "$HOME/.skylab-gateway-install.sh"') == 2
    assert "'$HOME" not in command


_ENV = svc.build_env(GatewayInstallOptions())


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
@pytest.mark.parametrize(
    "command",
    [
        svc.status_script(),
        svc.build_status_command(),
        svc.start_script(_ENV),
        svc.build_start_command(_ENV),
    ],
)
def test_generated_commands_are_valid_bash(command: str) -> None:
    result = subprocess.run(
        ["bash", "-n", "-c", command], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
