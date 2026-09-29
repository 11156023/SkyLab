"""Proxmox service 整理後的行為回歸：共用 helper 取代重複實作後，結果必須與原本一致。"""

from __future__ import annotations

import datetime as dt
import ssl
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.exceptions import ProxmoxError
from app.infrastructure.proxmox import operations, tls
from app.infrastructure.proxmox.os_detection import normalize_qemu_ostype
from app.services.proxmox import gpu_service
from app.services.proxmox import provisioning_service as svc


def _self_signed_ca_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "b12 test CA")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


# ---------------------------------------------------------------------------
# PVE CA context 只有一份實作，ticket 請求也驗主機名
# ---------------------------------------------------------------------------


def test_ws_ssl_context_with_ca_checks_hostname_without_x509_strict() -> None:
    cfg = SimpleNamespace(ca_cert=_self_signed_ca_pem(), verify_ssl=True)

    ctx = tls.build_ws_ssl_context(cfg)  # type: ignore[arg-type]

    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        assert not ctx.verify_flags & ssl.VERIFY_X509_STRICT


def test_ticket_verify_uses_ws_context_when_ca_is_configured() -> None:
    cfg = SimpleNamespace(ca_cert=_self_signed_ca_pem(), verify_ssl=False)

    verify = operations._ws_verify(cfg)  # type: ignore[arg-type]

    assert isinstance(verify, ssl.SSLContext)
    assert verify.check_hostname is True


@pytest.mark.parametrize("verify_ssl", [True, False])
def test_ticket_verify_without_ca_passes_bool_to_httpx(verify_ssl: bool) -> None:
    cfg = SimpleNamespace(ca_cert=None, verify_ssl=verify_ssl)

    assert operations._ws_verify(cfg) is verify_ssl  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# PVE 真正的 Vista ostype 是 wvista
# ---------------------------------------------------------------------------


def test_qemu_ostype_wvista_is_windows() -> None:
    assert normalize_qemu_ostype("wvista")["family"] == "windows"


# ---------------------------------------------------------------------------
# 逐連線彙總只有在全部連線都失敗時才報錯
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, nodes: list[dict] | None) -> None:
        self._nodes = nodes

    @property
    def nodes(self) -> Any:
        outer = self

        class _Nodes:
            @staticmethod
            def get() -> list[dict]:
                if outer._nodes is None:
                    raise RuntimeError("connection down")
                return outer._nodes

        return _Nodes()


def _patch_connections(monkeypatch, clients: dict[int, _Client]) -> None:
    monkeypatch.setattr(operations, "_connection_keys", lambda: list(clients))
    monkeypatch.setattr(operations, "get_proxmox_api", lambda key: clients[key])


def test_list_nodes_skips_failed_connection(monkeypatch) -> None:
    _patch_connections(
        monkeypatch, {1: _Client(None), 2: _Client([{"node": "pve2"}])}
    )

    assert operations.list_nodes() == [{"node": "pve2"}]


def test_list_nodes_empty_connection_is_not_an_error(monkeypatch) -> None:
    _patch_connections(monkeypatch, {1: _Client(None), 2: _Client([])})

    assert operations.list_nodes() == []


def test_list_nodes_raises_when_every_connection_fails(monkeypatch) -> None:
    _patch_connections(monkeypatch, {1: _Client(None), 2: _Client(None)})

    with pytest.raises(ProxmoxError, match="All Proxmox connections are unavailable"):
        operations.list_nodes()


# ---------------------------------------------------------------------------
# 網路設定字串抽成 helper 後格式不變
# ---------------------------------------------------------------------------

_NET_CFG = {"bridge_name": "vmbr1", "prefix_len": 24, "gateway": "10.0.0.1"}


def test_lxc_net0_format() -> None:
    assert svc._lxc_net0(_NET_CFG, "10.0.0.5") == (
        "name=eth0,bridge=vmbr1,ip=10.0.0.5/24,gw=10.0.0.1,firewall=1"
    )


def test_vm_net_config_adds_nameserver_only_when_configured() -> None:
    assert svc._vm_net_config(_NET_CFG, "10.0.0.5") == {
        "net0": "virtio,bridge=vmbr1,firewall=1",
        "ipconfig0": "ip=10.0.0.5/24,gw=10.0.0.1",
    }
    with_dns = svc._vm_net_config({**_NET_CFG, "dns_servers": "1.1.1.1"}, "10.0.0.5")
    assert with_dns["nameserver"] == "1.1.1.1"


@pytest.mark.parametrize(
    ("ostype", "expected"),
    [("win11", True), ("wvista", True), ("l26", False), (None, False), ("", False)],
)
def test_is_windows_ostype(ostype: str | None, expected: bool) -> None:
    assert svc._is_windows_ostype(ostype) is expected


# ---------------------------------------------------------------------------
# 建機失敗回滾先刪防火牆規則（由後往前），再刪半成品
# ---------------------------------------------------------------------------


def test_rollback_created_resource_purges_rules_then_cleans_up(monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        svc.firewall_service,
        "get_vm_firewall_rules",
        lambda node, vmid, rtype: [{"pos": 0}, {"pos": 2}, {"comment": "no pos"}],
    )
    monkeypatch.setattr(
        svc.firewall_service,
        "delete_rule_by_pos",
        lambda node, vmid, rtype, pos: calls.append(("delete", pos)),
    )
    monkeypatch.setattr(
        svc,
        "cleanup_failed_resource",
        lambda node, vmid, rtype: calls.append(("cleanup", node, vmid, rtype)),
    )

    svc._rollback_created_resource("pve1", 101, "lxc")

    assert calls == [("delete", 2), ("delete", 0), ("cleanup", "pve1", 101, "lxc")]


def test_rollback_created_resource_still_cleans_up_when_rule_listing_fails(
    monkeypatch,
) -> None:
    cleaned: list[int] = []

    def _boom(*_args):
        raise RuntimeError("firewall api down")

    monkeypatch.setattr(svc.firewall_service, "get_vm_firewall_rules", _boom)
    monkeypatch.setattr(
        svc, "cleanup_failed_resource", lambda node, vmid, rtype: cleaned.append(vmid)
    )

    svc._rollback_created_resource("pve1", 102, "qemu")

    assert cleaned == [102]


# ---------------------------------------------------------------------------
# GPU mapping 的 map 解析與 bus 分組共用 helper
# ---------------------------------------------------------------------------


def test_parse_mapping_maps_accepts_string_or_list() -> None:
    single = gpu_service._parse_mapping_maps({"map": "node=pve1,path=0000:15:00.0"})
    many = gpu_service._parse_mapping_maps(
        {"map": ["node=pve1,path=0000:15:00.0", 42, "node=pve2,path=0000:16:00.0"]}
    )

    assert [m.node for m in single] == ["pve1"]
    assert [m.node for m in many] == ["pve1", "pve2"]
    assert gpu_service._parse_mapping_maps("not a dict") == []


def test_bus_key_groups_by_domain_and_bus() -> None:
    assert gpu_service._bus_key("0000:15:01.3") == "0000:15"
    assert gpu_service._bus_key("weird") == "weird"


def test_mapping_target_node_reads_first_node_entry() -> None:
    assert (
        gpu_service._mapping_target_node(
            ["path=0000:15:00.0", "id=10de:233b, node = pve3 ,path=0000:16:00.0"]
        )
        == "pve3"
    )
    assert gpu_service._mapping_target_node(["path=0000:15:00.0"]) is None
