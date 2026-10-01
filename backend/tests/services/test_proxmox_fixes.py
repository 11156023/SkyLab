"""回歸測試：Proxmox service／infrastructure 的 bug 與安全修正。"""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from app.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ProxmoxError,
)
from app.infrastructure.proxmox import operations, router, tls
from app.models import IpAllocation
from app.services.proxmox import connection_sync_service, gpu_service
from app.services.proxmox import provisioning_service as svc

# ---------------------------------------------------------------------------
# 連線設定的 port 要一路傳到 CA pre-flight、cluster 查詢與節點列
# ---------------------------------------------------------------------------


def test_resolve_verify_preflights_the_configured_port(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_verify(host: str, ca_cert_pem: str, port: int = 8006) -> None:
        seen["args"] = (host, port)

    monkeypatch.setattr(tls, "_verify_server_with_ca", fake_verify)
    monkeypatch.setattr(tls, "ca_bundle_path", lambda pem: "/tmp/ca.pem")

    assert tls.resolve_verify("pve.example", True, "PEM", port=443) == "/tmp/ca.pem"
    assert seen["args"] == ("pve.example", 443)


def test_try_connect_passes_connection_port_to_resolve_verify(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_resolve(host, verify_ssl, ca_cert, port=8006):
        seen["port"] = port
        return True

    monkeypatch.setattr(router, "resolve_verify", fake_resolve)
    monkeypatch.setattr(router, "ProxmoxAPI", MagicMock())
    cfg = SimpleNamespace(
        verify_ssl=True, ca_cert="PEM", port=8443, user="root@pam",
        password="x", api_timeout=5,
    )

    router.try_connect("pve.example", cfg)  # type: ignore[arg-type]

    assert seen["port"] == 8443


def test_fetch_cluster_nodes_uses_configured_port(monkeypatch) -> None:
    api_cls = MagicMock()
    api_cls.return_value.cluster.status.get.return_value = [
        {"type": "cluster", "name": "c1"},
        {"type": "node", "name": "pve1", "ip": "10.0.0.1", "local": 1},
        {"type": "node", "name": "pve2", "ip": "10.0.0.2"},
    ]
    monkeypatch.setattr(router, "ProxmoxAPI", api_cls)

    nodes = router.fetch_cluster_nodes(
        host="pve.example", user="root@pam", password="x",
        verify_ssl=True, timeout=5, port=443,
    )

    assert api_cls.call_args.kwargs["port"] == 443
    assert nodes and all(n["port"] == 443 for n in nodes)


def test_fetch_cluster_nodes_single_node_fallback_keeps_port(monkeypatch) -> None:
    api_cls = MagicMock()
    api_cls.return_value.cluster.status.get.side_effect = RuntimeError("no cluster")
    monkeypatch.setattr(router, "ProxmoxAPI", api_cls)

    nodes = router.fetch_cluster_nodes(
        host="pve.example", user="root@pam", password="x",
        verify_ssl=True, timeout=5, port=9443,
    )

    assert nodes == [
        {"name": "pve.example", "host": "pve.example", "port": 9443, "is_primary": True}
    ]


def test_sync_connection_inventory_stores_connection_port(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_resolve(host, verify_ssl, ca_cert, port=8006):
        seen["verify_port"] = port
        return True

    def fake_fetch(**kwargs):
        seen["fetch_port"] = kwargs.get("port")
        return [
            {"name": "pve1", "host": "10.0.0.1", "port": kwargs.get("port"),
             "is_primary": True}
        ]

    def fake_upsert_nodes(session, node_dicts, connection_id):
        seen["node_dicts"] = node_dicts
        return []

    monkeypatch.setattr(connection_sync_service, "resolve_verify", fake_resolve)
    monkeypatch.setattr(connection_sync_service, "fetch_cluster_nodes", fake_fetch)
    monkeypatch.setattr(
        connection_sync_service.proxmox_connection_repo,
        "get_decrypted_password", lambda conn: "x",
    )
    monkeypatch.setattr(
        connection_sync_service.proxmox_node_repo, "upsert_nodes", fake_upsert_nodes
    )
    monkeypatch.setattr(
        connection_sync_service.proxmox_storage_repo,
        "upsert_storages", lambda *a, **k: [],
    )
    conn = SimpleNamespace(
        id=1, host="pve.example", port=443, user="root@pam", verify_ssl=True,
        ca_cert="PEM", api_timeout=5,
    )

    with patch.object(connection_sync_service, "open_client", MagicMock()):
        connection_sync_service.sync_connection_inventory(MagicMock(), conn)  # type: ignore[arg-type]

    assert seen["verify_port"] == 443
    assert seen["fetch_port"] == 443
    assert [n["port"] for n in seen["node_dicts"]] == [443]


# ---------------------------------------------------------------------------
# create_lxc／create_vm 必須避開 DB 已預留但 PVE 還看不到的 VMID
# ---------------------------------------------------------------------------


def test_allocate_free_vmid_skips_vmid_reserved_in_db(
    db: Session, monkeypatch
) -> None:
    base = 990_000 + uuid.uuid4().int % 9_000
    row = IpAllocation(
        ip_address=f"b12-test-{uuid.uuid4().hex[:12]}",
        purpose="vm",
        vmid=base,
    )
    db.add(row)
    db.commit()
    try:
        monkeypatch.setattr(svc.proxmox_service, "next_vmid", lambda: base)
        assert svc.allocate_free_vmid(db) == base + 1
    finally:
        db.delete(row)
        db.commit()


@pytest.fixture
def reserved_vmid_500(monkeypatch) -> dict[str, list[int]]:
    """PVE nextid 回 500，但 500 已被排程在 DB 預留（IP 配發紀錄）。"""
    calls: dict[str, list[int]] = {"allocate_ip": [], "release_ip": []}

    monkeypatch.setattr(svc.proxmox_service, "next_vmid", lambda: 500)
    monkeypatch.setattr(
        svc.proxmox_service, "vmid_allocation_lock", lambda **_: nullcontext()
    )
    monkeypatch.setattr(
        svc.resource_repo,
        "get_allocated_ip_address",
        lambda *, session, vmid: "10.0.0.5" if vmid == 500 else None,
    )
    monkeypatch.setattr(
        svc.resource_repo, "get_resource_by_vmid", lambda *, session, vmid: None
    )
    monkeypatch.setattr(svc, "_resolve_managed_storage", lambda **_: "local-lvm")
    monkeypatch.setattr(
        svc.ip_management_service,
        "get_network_config_for_vm",
        lambda session: {"bridge_name": "vmbr0", "prefix_len": 24, "gateway": "10.0.0.1"},
    )

    def fake_allocate_ip(session, vmid, purpose, reservation_key=None):
        calls["allocate_ip"].append(vmid)
        raise RuntimeError("stop after VMID allocation")

    def fake_release_ip(session, vmid, **_kwargs):
        calls["release_ip"].append(vmid)

    monkeypatch.setattr(svc.ip_management_service, "allocate_ip", fake_allocate_ip)
    monkeypatch.setattr(svc.ip_management_service, "release_ip", fake_release_ip)
    monkeypatch.setattr(svc, "Session", MagicMock())
    return calls


def test_create_lxc_skips_vmid_reserved_by_scheduler(reserved_vmid_500) -> None:
    with pytest.raises(ProxmoxError):
        svc.create_lxc(
            session=MagicMock(),
            lxc_data=SimpleNamespace(storage=None, rootfs_size=8),  # type: ignore[arg-type]
            user_id=uuid.uuid4(),
            target_node="pve1",
        )

    assert reserved_vmid_500["allocate_ip"] == [501]
    # 失敗回滾只能釋放自己的 VMID，不能動到排程預留的 500
    assert 500 not in reserved_vmid_500["release_ip"]


def test_create_vm_skips_vmid_reserved_by_scheduler(
    reserved_vmid_500, monkeypatch
) -> None:
    monkeypatch.setattr(svc, "get_vm_target_node", lambda template_id: "pve1")

    with pytest.raises(ProxmoxError):
        svc.create_vm(
            session=MagicMock(),
            vm_data=SimpleNamespace(template_id=9000, storage=None, disk_size=20),  # type: ignore[arg-type]
            user_id=uuid.uuid4(),
        )

    assert reserved_vmid_500["allocate_ip"] == [501]
    assert 500 not in reserved_vmid_500["release_ip"]


# ---------------------------------------------------------------------------
# 快照名稱 '..' 會被 urllib3 正規化成刪整台機器
# ---------------------------------------------------------------------------


@pytest.fixture
def resource_api(monkeypatch) -> MagicMock:
    api = MagicMock()
    monkeypatch.setattr(operations, "_resource_api", api)
    monkeypatch.setattr(operations, "basic_blocking_task_status", lambda *a, **k: None)
    return api


@pytest.mark.parametrize("snapname", ["..", ".", "../x", "a/b", "x", "", "a,b=1"])
def test_delete_snapshot_rejects_malformed_name(resource_api, snapname) -> None:
    with pytest.raises(BadRequestError):
        operations.delete_snapshot("pve1", 100, "qemu", snapname)
    resource_api.assert_not_called()


@pytest.mark.parametrize("snapname", ["..", "../x"])
def test_rollback_snapshot_rejects_malformed_name(resource_api, snapname) -> None:
    with pytest.raises(BadRequestError):
        operations.rollback_snapshot("pve1", 100, "lxc", snapname)
    resource_api.assert_not_called()


def test_create_snapshot_rejects_malformed_name(resource_api) -> None:
    with pytest.raises(BadRequestError):
        operations.create_snapshot("pve1", 100, "qemu", snapname="..")
    resource_api.assert_not_called()


@pytest.mark.parametrize(
    "snapname", ["skylab-init", "mining-202609271200", "before_upgrade", "s1"]
)
def test_snapshot_ops_accept_valid_names(resource_api, snapname) -> None:
    operations.create_snapshot("pve1", 100, "qemu", snapname=snapname)
    operations.delete_snapshot("pve1", 100, "qemu", snapname)
    operations.rollback_snapshot("pve1", 100, "qemu", snapname)
    assert resource_api.call_count == 3


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"hasFeature": 1, "nodes": ["pve1"]}, True),
        ({"hasFeature": True}, True),
        ({"hasFeature": 0, "nodes": []}, False),
        ({"hasFeature": False}, False),
        ({}, False),
        (None, False),
    ],
)
@pytest.mark.parametrize("rtype", ["qemu", "lxc"])
def test_has_snapshot_feature_reads_pve_feature_api(
    resource_api, payload, expected, rtype
) -> None:
    """VM 與 LXC 都問 PVE 的 feature=snapshot；回應缺欄位一律當不支援。"""
    feature_get = resource_api.return_value.feature.get
    feature_get.return_value = payload

    assert operations.has_snapshot_feature("pve1", 100, rtype) is expected
    resource_api.assert_called_once_with("pve1", 100, rtype)
    feature_get.assert_called_once_with(feature="snapshot")


# ---------------------------------------------------------------------------
# GPU mapping id 不可夾帶 hostpci 選項或路徑片段
# ---------------------------------------------------------------------------


def _forbid(*_args, **_kwargs):
    raise AssertionError("must not reach Proxmox with a malformed mapping id")


def test_build_gpu_hostpci_rejects_mapping_id_smuggling(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "get_gpu_mapping", _forbid)

    with pytest.raises(ProxmoxError):
        svc._build_gpu_hostpci("gpu0,x-vga=1,romfile=a/../gpu0", None)


def test_get_gpu_mapping_rejects_path_traversal(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "iter_connection_clients", _forbid)

    with pytest.raises(NotFoundError):
        gpu_service.get_gpu_mapping("../../nodes")


def test_get_gpu_node_counts_ignores_malformed_mapping_id(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "iter_connection_clients", _forbid)

    assert gpu_service.get_gpu_node_counts(mapping_id="gpu0/../x") == {}


# ---------------------------------------------------------------------------
# 刪除 GPU mapping 不可連帶刪掉其他連線同名的 mapping
# ---------------------------------------------------------------------------


def _client_with_mapping(has_mapping: bool) -> MagicMock:
    client = MagicMock()
    pci = client.cluster.mapping.pci.return_value
    if not has_mapping:
        pci.get.side_effect = RuntimeError("no such mapping")
    return client


def test_delete_gpu_mapping_refuses_when_id_exists_on_several_connections(
    monkeypatch,
) -> None:
    c1, c2 = _client_with_mapping(True), _client_with_mapping(True)
    monkeypatch.setattr(
        gpu_service, "iter_connection_clients", lambda: [(1, c1), (2, c2)]
    )

    with pytest.raises(ConflictError):
        gpu_service.delete_gpu_mapping("gpu0")

    c1.cluster.mapping.pci.return_value.delete.assert_not_called()
    c2.cluster.mapping.pci.return_value.delete.assert_not_called()


def test_delete_gpu_mapping_only_deletes_on_owning_connection(monkeypatch) -> None:
    c1, c2 = _client_with_mapping(False), _client_with_mapping(True)
    monkeypatch.setattr(
        gpu_service, "iter_connection_clients", lambda: [(1, c1), (2, c2)]
    )

    gpu_service.delete_gpu_mapping("gpu0")

    c1.cluster.mapping.pci.return_value.delete.assert_not_called()
    c2.cluster.mapping.pci.return_value.delete.assert_called_once()


def test_delete_gpu_mapping_missing_everywhere_raises(monkeypatch) -> None:
    c1 = _client_with_mapping(False)
    monkeypatch.setattr(gpu_service, "iter_connection_clients", lambda: [(1, c1)])

    with pytest.raises(ProxmoxError):
        gpu_service.delete_gpu_mapping("gpu0")
    c1.cluster.mapping.pci.return_value.delete.assert_not_called()
