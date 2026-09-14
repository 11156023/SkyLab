"""單台 VM 的對外服務清單與發布：以 Proxmox 規則為主、DB 孤兒紀錄補列、重複 port 擋下。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import BadRequestError
from app.schemas.firewall import PortSpec, PublishedServiceCreate, TopologyEdge
from app.services.network import firewall_service as fw


def _rule(comment: str, pos: int) -> dict[str, Any]:
    return {"pos": pos, "type": "in", "action": "ACCEPT", "comment": comment}


@pytest.fixture()
def fake_env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(
        rules=[
            _rule("SkyLab:gateway->150:80/tcp", 0),
            _rule("SkyLab:gateway->150:22/tcp", 1),
        ],
        rp_rules=[],
        nat_rules=[],
        created=[],
    )
    monkeypatch.setattr(
        fw,
        "proxmox_service",
        SimpleNamespace(
            find_resource=lambda vmid: {
                "node": "pve1",
                "type": "qemu",
                "vmid": vmid,
                "name": "web",
            },
        ),
    )
    monkeypatch.setattr(
        fw, "get_vm_firewall_rules", lambda node, vmid, rtype: state.rules
    )

    def fake_enrich(edges: list[TopologyEdge], session: Any) -> None:
        for edge in edges:
            for spec in edge.ports:
                for r in state.rp_rules:
                    if r.internal_port == spec.port:
                        spec.domain = r.domain
                        spec.enable_https = r.enable_https
                for n in state.nat_rules:
                    if n.internal_port == spec.port and n.protocol == spec.protocol:
                        spec.external_port = n.external_port

    monkeypatch.setattr(fw, "_enrich_edges_from_db", fake_enrich)

    import app.repositories.nat_rule as nat_repo
    import app.repositories.reverse_proxy as rp_repo

    monkeypatch.setattr(
        rp_repo, "list_rules_by_vmid", lambda session, vmid: state.rp_rules
    )
    monkeypatch.setattr(
        nat_repo, "list_rules_by_vmid", lambda session, vmid: state.nat_rules
    )

    def fake_create(
        *, source_vmid, target_vmid, ports, session=None, direction="one_way"
    ):
        state.created.append((source_vmid, target_vmid, ports))

    monkeypatch.setattr(fw, "create_connection", fake_create)
    return state


def test_list_marks_modes_and_orphans(fake_env: SimpleNamespace) -> None:
    fake_env.rp_rules = [
        SimpleNamespace(internal_port=80, domain="web.example.com", enable_https=True)
    ]
    fake_env.nat_rules = [
        SimpleNamespace(internal_port=22, protocol="tcp", external_port=2222),
        # DB 有、Proxmox 沒有對應規則 → 孤兒，仍要列出
        SimpleNamespace(internal_port=5432, protocol="tcp", external_port=15432),
    ]

    services = fw.list_vm_published_services(150, session=None)

    by_port = {s.port: s for s in services}
    assert by_port[80].mode == "domain"
    assert by_port[80].url == "https://web.example.com"
    assert by_port[80].firewall_rule_present is True
    assert by_port[22].mode == "port_forward"
    assert by_port[22].external_port == 2222
    assert by_port[5432].mode == "port_forward"
    assert by_port[5432].firewall_rule_present is False


def test_publish_rejects_duplicate_port(fake_env: SimpleNamespace) -> None:
    data = PublishedServiceCreate(port=80, protocol="tcp", mode="firewall_only")
    with pytest.raises(BadRequestError):
        fw.publish_vm_service(150, data, session=None)
    assert fake_env.created == []


def test_publish_goes_through_create_connection(fake_env: SimpleNamespace) -> None:
    data = PublishedServiceCreate(
        port=8080, protocol="tcp", mode="port_forward", external_port=18080
    )
    service = fw.publish_vm_service(150, data, session=None)

    assert service.mode == "port_forward"
    assert fake_env.created == [
        (None, 150, [PortSpec(port=8080, protocol="tcp", external_port=18080)])
    ]


def test_create_schema_normalizes_mode_fields() -> None:
    domain = PublishedServiceCreate(
        port=80, mode="domain", domain=" App.Example.com. ", external_port=9
    )
    assert domain.domain == "app.example.com"
    assert domain.external_port is None

    with pytest.raises(ValueError):
        PublishedServiceCreate(port=80, mode="domain")
    with pytest.raises(ValueError):
        PublishedServiceCreate(port=80, mode="port_forward")
    with pytest.raises(ValueError):
        PublishedServiceCreate(
            port=80, mode="domain", domain="x.example.com", protocol="udp"
        )
