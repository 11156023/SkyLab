"""IP 快取寫入失敗不能把 session 拖進無效交易。

回歸背景：get_topology 在迴圈裡順手更新每台 VM 的 IP 快取，某一台的 flush 因 DB 連線
中斷失敗後被吞掉，session 卻停在 failed transaction，最後 _enrich_edges_from_db 用同一
個 session 查 NAT 規則時炸出 PendingRollbackError，整張拓撲 500。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.repositories import resource as resource_repo
from app.services.network import firewall_service as fw


class _Session:
    """記錄 rollback 次數的假 session（不需要真 DB）。"""

    def __init__(self) -> None:
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1


def _db_down(*_: Any, **__: Any) -> None:
    raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))


# ─── sync_ip_cache ───────────────────────────────────────────────────────────


def test_sync_ip_cache_rolls_back_when_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resource_repo, "update_ip_address", _db_down)
    session = _Session()

    ip = resource_repo.sync_ip_cache(session=session, vmid=150, live_ip="10.0.0.5")  # type: ignore[arg-type]

    assert ip == "10.0.0.5"  # 即時 IP 照樣回傳，快取寫不進去不影響結果
    assert session.rollbacks == 1


def test_sync_ip_cache_rolls_back_when_cache_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resource_repo, "get_cached_ip_address", _db_down)
    session = _Session()

    ip = resource_repo.sync_ip_cache(session=session, vmid=150, live_ip=None)  # type: ignore[arg-type]

    assert ip is None
    assert session.rollbacks == 1


def test_sync_ip_cache_falls_back_to_cache_when_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resource_repo, "get_cached_ip_address", lambda *, session, vmid: "10.0.0.9"
    )
    monkeypatch.setattr(resource_repo, "update_ip_address", _db_down)
    session = _Session()

    ip = resource_repo.sync_ip_cache(session=session, vmid=150, live_ip=None)  # type: ignore[arg-type]

    assert ip == "10.0.0.9"
    assert session.rollbacks == 0


# ─── get_topology ────────────────────────────────────────────────────────────


def test_get_topology_survives_poisoned_ip_cache_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    user = SimpleNamespace(id="u1")
    enrich_calls: list[int] = []

    monkeypatch.setattr(fw, "can_bypass_resource_ownership", lambda user: True)
    monkeypatch.setattr(
        fw.resource_repo,
        "get_all_resources",
        lambda *, session: [SimpleNamespace(vmid=150), SimpleNamespace(vmid=151)],
    )
    monkeypatch.setattr(fw.layout_repo, "get_layout", lambda *, session, user_id: [])
    monkeypatch.setattr(
        fw,
        "proxmox_service",
        SimpleNamespace(
            find_resource=lambda vmid: {
                "node": "pve1",
                "type": "qemu",
                "vmid": vmid,
                "name": f"vm{vmid}",
                "status": "running",
            },
            get_ip_address=lambda node, vmid, rtype: f"10.0.0.{vmid - 100}",
        ),
    )
    monkeypatch.setattr(fw, "get_firewall_options", lambda node, vmid, rtype: {"enable": 1})
    monkeypatch.setattr(fw, "get_connections_from_rules", lambda vmids: [])
    # 每台 VM 的快取寫入都因 DB 斷線失敗
    monkeypatch.setattr(fw.resource_repo, "update_ip_address", _db_down)

    def fake_enrich(edges: list[Any], sess: Any) -> None:
        # 走到這裡時 session 必須已經 rollback 過，否則真 DB 會 PendingRollbackError
        enrich_calls.append(sess.rollbacks)

    monkeypatch.setattr(fw, "_enrich_edges_from_db", fake_enrich)

    resp = fw.get_topology(user=user, session=session)  # type: ignore[arg-type]

    vm_nodes = [n for n in resp.nodes if n.node_type == "vm"]
    assert [n.ip_address for n in vm_nodes] == ["10.0.0.50", "10.0.0.51"]
    assert all(n.firewall_enabled for n in vm_nodes)
    assert session.rollbacks == 2  # 兩台各 rollback 一次
    assert enrich_calls == [2]
