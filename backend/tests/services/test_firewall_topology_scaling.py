"""防火牆拓撲在機器多的時候不能逐台串行打 PVE。

回歸背景：get_topology 對每台機器各做 find_resource ×2（每次都重抓整份
/cluster/resources）、guest agent IP、防火牆 options、rules，全部串行；
50 台機器就是兩三百次 PVE 呼叫，超過 nginx 逾時，頁面一直載入不出來。
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.network import firewall_service as fw


def _vm(vmid: int, *, status: str = "running", node: str = "pve1") -> dict[str, Any]:
    return {
        "node": node,
        "type": "qemu",
        "vmid": vmid,
        "name": f"vm{vmid}",
        "status": status,
    }


@pytest.fixture
def topology_env(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, list[Any]] = {"list_all": [], "ip": [], "cache": []}

    def install(*, reachable: list[int], pve: list[dict[str, Any]]) -> dict[str, list[Any]]:
        monkeypatch.setattr(
            fw.resource_access,
            "list_reachable_resources",
            lambda *, session, user: [
                SimpleNamespace(vmid=vmid, user_id="u1", teaching_class_id=None)
                for vmid in reachable
            ],
        )
        monkeypatch.setattr(
            fw.resource_access,
            "list_owned_teaching_class_ids",
            lambda *, session, user: set(),
        )
        monkeypatch.setattr(
            fw.resource_access,
            "can_manage_resource",
            lambda *, resource, user, owned_class_ids: True,
        )
        monkeypatch.setattr(fw.resource_kind, "classify_many", lambda session, resources: {})
        monkeypatch.setattr(fw.layout_repo, "get_layout", lambda *, session, user_id: [])

        def list_all_resources() -> list[dict[str, Any]]:
            calls["list_all"].append(True)
            return pve

        def get_ip_address(node: str, vmid: int, rtype: str) -> str:
            calls["ip"].append(vmid)
            return f"10.0.1.{vmid % 250}"

        def find_resource(vmid: int) -> dict[str, Any]:
            raise AssertionError("拓撲不應逐台 find_resource")

        monkeypatch.setattr(
            fw,
            "proxmox_service",
            SimpleNamespace(
                list_all_resources=list_all_resources,
                get_ip_address=get_ip_address,
                find_resource=find_resource,
            ),
        )

        def sync_ip_cache_many(
            *, session: Any, live_ips: dict[int, str | None]
        ) -> dict[int, str | None]:
            calls["cache"].extend(live_ips.items())
            return {vmid: ip or f"cached-{vmid}" for vmid, ip in live_ips.items()}

        monkeypatch.setattr(fw.resource_repo, "sync_ip_cache_many", sync_ip_cache_many)
        monkeypatch.setattr(fw, "get_firewall_options", lambda node, vmid, rtype: {"enable": 1})
        monkeypatch.setattr(fw, "get_vm_firewall_rules", lambda node, vmid, rtype: [])
        monkeypatch.setattr(fw, "_enrich_edges_from_db", lambda edges, session: None)
        return calls

    return install


def _topology() -> Any:
    return fw.get_topology(user=SimpleNamespace(id="u1"), session=object())  # type: ignore[arg-type]


def test_cluster_listing_is_fetched_once_for_many_vms(topology_env) -> None:
    vmids = list(range(100, 150))
    calls = topology_env(reachable=vmids, pve=[_vm(v) for v in vmids])

    resp = _topology()

    assert len(calls["list_all"]) == 1
    assert [n.vmid for n in resp.nodes if n.node_type == "vm"] == vmids
    # IP 快取一次批次處理，涵蓋每一台
    assert [vmid for vmid, _ip in calls["cache"]] == vmids


def test_stopped_vm_skips_live_ip_query_and_uses_cache(topology_env) -> None:
    calls = topology_env(
        reachable=[100, 101],
        pve=[_vm(100), _vm(101, status="stopped")],
    )

    resp = _topology()

    assert calls["ip"] == [100]
    ips = {n.vmid: n.ip_address for n in resp.nodes if n.node_type == "vm"}
    assert ips == {100: "10.0.1.100", 101: "cached-101"}


def test_missing_and_duplicate_vmids_are_skipped(topology_env) -> None:
    topology_env(
        reachable=[100, 101, 102],
        pve=[_vm(100), _vm(101, node="pve1"), _vm(101, node="pve9")],
    )

    resp = _topology()

    assert [n.vmid for n in resp.nodes if n.node_type == "vm"] == [100]


def test_per_vm_pve_queries_run_in_parallel(
    topology_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    topology_env(reachable=[100, 101], pve=[_vm(100), _vm(101)])
    # 兩台必須同時卡在查詢裡才過得了 barrier；串行執行會逾時 BrokenBarrierError
    barrier = threading.Barrier(2, timeout=5)

    def options(node: str, vmid: int, rtype: str) -> dict[str, Any]:
        barrier.wait()
        return {"enable": 1}

    monkeypatch.setattr(fw, "get_firewall_options", options)

    resp = _topology()

    assert all(n.firewall_enabled for n in resp.nodes if n.node_type == "vm")


def test_edges_come_from_the_rules_read_in_parallel(
    topology_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    topology_env(reachable=[100, 101], pve=[_vm(100), _vm(101)])
    comment = fw._make_connection_comment(100, 101, 22, "tcp")
    monkeypatch.setattr(
        fw,
        "get_vm_firewall_rules",
        lambda node, vmid, rtype: [{"pos": 0, "comment": comment}],
    )

    resp = _topology()

    assert len(resp.edges) == 1
    edge = resp.edges[0]
    assert (edge.source_vmid, edge.target_vmid) == (100, 101)
    assert [(p.port, p.protocol) for p in edge.ports] == [(22, "tcp")]


def test_quick_practice_nodes_are_read_only_in_firewall_topology(
    topology_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    topology_env(reachable=[100], pve=[_vm(100)])
    monkeypatch.setattr(
        fw.resource_kind,
        "classify_many",
        lambda session, resources: {100: "quick_practice"},
    )

    resp = _topology()

    node = next(n for n in resp.nodes if n.vmid == 100)
    assert node.machine_kind == "quick_practice"
    assert node.can_manage is False
