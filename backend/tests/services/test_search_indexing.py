"""對外網址是否讓搜尋引擎收錄：預設不收錄，擁有者可逐條打開。

旗標存在 reverse_proxy_rule.allow_indexing，一路經 PortSpec／PublishedService 帶到 Gateway：
不允許時 nginx 回應帶 X-Robots-Tag: noindex。切換只改旗標並重建 nginx，網址與 DNS 不動。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.repositories import nat_rule as nat_repo
from app.repositories import reverse_proxy as rp_repo
from app.schemas.firewall import (
    PortSpec,
    PublishedServiceCreate,
    PublishedServiceRef,
    TopologyEdge,
)
from app.services.network import firewall_service as fw
from app.services.network import reverse_proxy_service

# ─── schema ──────────────────────────────────────────────────────────────────


def test_indexing_is_off_unless_asked_for() -> None:
    assert PortSpec(port=80).allow_indexing is False
    assert (
        PublishedServiceCreate(
            port=80, mode="domain", domain="a.example.com"
        ).allow_indexing
        is False
    )


def test_domain_service_carries_indexing_into_port_spec() -> None:
    create = PublishedServiceCreate(
        port=80, mode="domain", domain="a.example.com", allow_indexing=True
    )
    assert create.to_port_spec().allow_indexing is True


@pytest.mark.parametrize(
    "extra",
    [{"mode": "port_forward", "external_port": 30080}, {"mode": "firewall_only"}],
)
def test_indexing_only_applies_to_domain_services(extra: dict[str, Any]) -> None:
    """沒有網址就沒有網頁可收錄：其他模式一律清成 False。"""
    create = PublishedServiceCreate(port=80, allow_indexing=True, **extra)
    assert create.allow_indexing is False
    assert create.to_port_spec().allow_indexing is False


# ─── 從 DB 帶回拓撲與服務清單 ────────────────────────────────────────────────


def test_enrich_edges_reads_indexing_from_the_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rp_repo,
        "list_rules_by_vmids",
        lambda _s, _vmids: [
            SimpleNamespace(
                vmid=150,
                internal_port=80,
                domain="web.example.com",
                enable_https=True,
                allow_indexing=True,
            )
        ],
    )
    monkeypatch.setattr(nat_repo, "list_rules_by_vmids", lambda _s, _vmids: [])
    edge = TopologyEdge(
        source_vmid=None,
        target_vmid=150,
        ports=[PortSpec(port=80)],
        direction="one_way",
    )

    fw._enrich_edges_from_db([edge], session=object())  # type: ignore[arg-type]

    assert edge.ports[0].allow_indexing is True


def test_published_service_reports_indexing() -> None:
    spec = PortSpec(port=80, domain="web.example.com", allow_indexing=True)
    service = fw._service_from_spec(spec, firewall_rule_present=True)
    assert service.allow_indexing is True


def test_orphan_domain_rule_lists_its_indexing(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB 有網址、Proxmox 沒有入站規則的舊資料：清單照樣要帶出收錄設定。"""
    monkeypatch.setattr(fw, "get_connections_from_rules", lambda _vmids: [])
    monkeypatch.setattr(fw, "_enrich_edges_from_db", lambda _edges, _session: None)
    monkeypatch.setattr(
        rp_repo,
        "list_rules_by_vmid",
        lambda _s, _vmid: [
            SimpleNamespace(
                internal_port=80,
                domain="web.example.com",
                enable_https=True,
                allow_indexing=True,
            )
        ],
    )
    monkeypatch.setattr(nat_repo, "list_rules_by_vmid", lambda _s, _vmid: [])

    (service,) = fw.list_vm_published_services(150, session=None)  # type: ignore[arg-type]

    assert service.firewall_rule_present is False
    assert service.allow_indexing is True


# ─── 建立與替換 ──────────────────────────────────────────────────────────────


def test_create_connection_passes_indexing_to_the_proxy_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied: list[dict[str, Any]] = []
    monkeypatch.setattr(
        fw,
        "proxmox_service",
        SimpleNamespace(find_resource=lambda vmid: {"node": "pve1", "type": "qemu"}),
    )
    monkeypatch.setattr(
        "app.repositories.gateway_config.get_gateway_config",
        lambda _s: SimpleNamespace(host="gw", encrypted_private_key="k"),
    )
    monkeypatch.setattr(fw, "_get_publishable_vm_ip", lambda _vmid, _s: "10.10.0.5")
    monkeypatch.setattr(fw, "create_rule", lambda *_a: None)
    monkeypatch.setattr(
        reverse_proxy_service,
        "apply_reverse_proxy_rule_for_domain",
        lambda **kwargs: applied.append(kwargs) or SimpleNamespace(),
    )

    fw.create_connection(
        source_vmid=None,
        target_vmid=150,
        ports=[PortSpec(port=80, domain="web.example.com", allow_indexing=True)],
        session=object(),
    )

    assert applied[0]["allow_indexing"] is True


def test_apply_reverse_proxy_rule_for_domain_stores_indexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        reverse_proxy_service,
        "resolve_zone_for_domain",
        lambda _s, _domain: ("zone-1", "web"),
    )
    monkeypatch.setattr(
        reverse_proxy_service,
        "apply_reverse_proxy_rule",
        lambda session, **kwargs: calls.append(kwargs) or SimpleNamespace(),
    )

    reverse_proxy_service.apply_reverse_proxy_rule_for_domain(
        object(),
        vmid=150,
        vm_ip="10.10.0.5",
        domain="web.example.com",
        internal_port=80,
        allow_indexing=True,
    )

    assert calls[0]["allow_indexing"] is True


def test_replace_restores_the_original_indexing_when_publish_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = fw._service_from_spec(
        PortSpec(port=80, domain="web.example.com", allow_indexing=True),
        firewall_rule_present=True,
    )
    monkeypatch.setattr(fw, "list_vm_published_services", lambda _vmid, _s: [current])
    monkeypatch.setattr(fw, "unpublish_vm_service", lambda *_a: None)
    monkeypatch.setattr(
        reverse_proxy_service, "assert_domain_available", lambda *_a, **_k: None
    )
    monkeypatch.setattr(rp_repo, "list_rules_by_vmid", lambda _s, _vmid: [])
    created: list[list[PortSpec]] = []

    def fake_create(*, source_vmid, target_vmid, ports, session=None):
        created.append(list(ports))
        if len(created) == 1:
            raise BadRequestError("create failed")

    monkeypatch.setattr(fw, "create_connection", fake_create)

    with pytest.raises(BadRequestError):
        fw.replace_vm_service(
            150,
            PublishedServiceRef(port=80),
            PublishedServiceCreate(port=80, mode="domain", domain="new.example.com"),
            session=object(),  # type: ignore[arg-type]
        )

    restored = created[1][0]
    assert restored.domain == "web.example.com"
    assert restored.allow_indexing is True


# ─── 切換收錄 ────────────────────────────────────────────────────────────────


class _RuleStore:
    """reverse_proxy repository 的替身：記下每次寫入時的旗標。"""

    def __init__(self, rules: list[Any]) -> None:
        self.rules = rules
        self.writes: list[bool] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rp_repo, "list_rules_by_vmid", lambda _s, _vmid: self.rules)
        monkeypatch.setattr(
            rp_repo,
            "update_rule",
            lambda _s, rule: self.writes.append(rule.allow_indexing) or rule,
        )


def _proxy_rule(**overrides: Any) -> SimpleNamespace:
    fields = {
        "vmid": 150,
        "internal_port": 80,
        "domain": "web.example.com",
        "allow_indexing": False,
    }
    return SimpleNamespace(**{**fields, **overrides})


def test_set_rule_indexing_updates_the_flag_and_resyncs_nginx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = _proxy_rule()
    store = _RuleStore([_proxy_rule(internal_port=22), rule])
    store.install(monkeypatch)
    synced: list[bool] = []
    monkeypatch.setattr(
        reverse_proxy_service,
        "_sync_nginx",
        lambda _s: synced.append(rule.allow_indexing),
    )

    result = reverse_proxy_service.set_rule_indexing(object(), 150, 80, True)

    assert result is rule
    assert rule.allow_indexing is True
    assert store.writes == [True]
    # nginx 重建時讀到的已經是新值
    assert synced == [True]


def test_set_rule_indexing_without_change_skips_the_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RuleStore([_proxy_rule(allow_indexing=True)])
    store.install(monkeypatch)
    monkeypatch.setattr(
        reverse_proxy_service,
        "_sync_nginx",
        lambda _s: pytest.fail("nothing changed, nginx should not be rebuilt"),
    )

    reverse_proxy_service.set_rule_indexing(object(), 150, 80, True)

    assert store.writes == []


def test_set_rule_indexing_reverts_when_the_gateway_sync_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway 沒套上就不能讓 DB 顯示已經改好。"""
    rule = _proxy_rule()
    store = _RuleStore([rule])
    store.install(monkeypatch)

    def fail_sync(_session: Any) -> None:
        raise ProxmoxError("gateway down")

    monkeypatch.setattr(reverse_proxy_service, "_sync_nginx", fail_sync)

    with pytest.raises(ProxmoxError, match="gateway down"):
        reverse_proxy_service.set_rule_indexing(object(), 150, 80, True)

    assert rule.allow_indexing is False
    assert store.writes == [True, False]


def test_set_rule_indexing_unknown_port_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RuleStore([_proxy_rule(internal_port=22)]).install(monkeypatch)

    with pytest.raises(NotFoundError):
        reverse_proxy_service.set_rule_indexing(object(), 150, 80, True)
