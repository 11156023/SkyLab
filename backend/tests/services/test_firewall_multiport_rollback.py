"""Internet→VM 一次建立多個 port 要全有或全無。

以前後面的 port 失敗只回滾 Proxmox 防火牆規則，前面 port 已經上線的
NAT／網域（含 Cloudflare DNS）會留著，對外仍然通到這台機器。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import BadRequestError, ProxmoxError
from app.schemas.firewall import PortSpec
from app.services.network import firewall_service as fw
from app.services.network import nat_service, reverse_proxy_service


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    from app.repositories import gateway_config as gw_repo

    state = SimpleNamespace(
        rules=[],
        nat_applied=[],
        proxy_applied=[],
        nat_removed=[],
        proxy_removed=[],
        fail_nat=None,
        fail_proxy=None,
        fail_nat_removal=None,
    )
    monkeypatch.setattr(
        fw,
        "proxmox_service",
        SimpleNamespace(
            find_resource=lambda vmid: {"node": "pve1", "type": "qemu", "vmid": vmid}
        ),
    )
    monkeypatch.setattr(
        gw_repo,
        "get_gateway_config",
        lambda session: SimpleNamespace(host="gw", encrypted_private_key="k"),
    )
    monkeypatch.setattr(fw, "_get_publishable_vm_ip", lambda vmid, session: "10.0.0.50")

    def fake_create_rule(node: str, vmid: int, rtype: str, rule: dict) -> None:
        state.rules.append({**rule, "pos": len(state.rules)})

    def fake_delete_rule_by_pos(node: str, vmid: int, rtype: str, pos: int) -> None:
        state.rules = [r for r in state.rules if r["pos"] != pos]

    monkeypatch.setattr(fw, "create_rule", fake_create_rule)
    monkeypatch.setattr(fw, "get_vm_firewall_rules", lambda node, vmid, rtype: list(state.rules))
    monkeypatch.setattr(fw, "delete_rule_by_pos", fake_delete_rule_by_pos)

    def fake_apply_nat(**kwargs: Any) -> SimpleNamespace:
        if state.fail_nat is not None:
            raise state.fail_nat
        rule = SimpleNamespace(kind="nat", **kwargs)
        state.nat_applied.append(rule)
        return rule

    def fake_apply_proxy(**kwargs: Any) -> SimpleNamespace:
        if state.fail_proxy is not None:
            raise state.fail_proxy
        rule = SimpleNamespace(kind="proxy", **kwargs)
        state.proxy_applied.append(rule)
        return rule

    def fake_remove_nat(session: Any, rules: list) -> None:
        if state.fail_nat_removal is not None:
            raise state.fail_nat_removal
        state.nat_removed.extend(rules)

    monkeypatch.setattr(nat_service, "apply_nat_rule", fake_apply_nat)
    monkeypatch.setattr(
        reverse_proxy_service, "apply_reverse_proxy_rule_for_domain", fake_apply_proxy
    )
    monkeypatch.setattr(nat_service, "remove_nat_rules", fake_remove_nat)
    monkeypatch.setattr(
        reverse_proxy_service,
        "remove_reverse_proxy_rules",
        lambda session, rules: state.proxy_removed.extend(rules),
    )
    return state


SSH = PortSpec(port=22, protocol="tcp", external_port=30022)
WEB = PortSpec(port=80, protocol="tcp", domain="web.example.com")
DB = PortSpec(port=5432, protocol="tcp", external_port=35432)


def test_later_domain_failure_removes_earlier_port_forward(env: SimpleNamespace) -> None:
    env.fail_proxy = BadRequestError("domain already taken")

    with pytest.raises(BadRequestError):
        fw.create_connection(None, 150, [SSH, WEB], session=object())

    assert env.nat_removed == env.nat_applied
    assert [r.external_port for r in env.nat_removed] == [30022]
    assert env.proxy_removed == []
    assert env.rules == []


def test_later_port_forward_failure_removes_earlier_domain_and_forward(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"nat": 0}
    original_fail = ProxmoxError("nginx -t failed")

    def nat_fails_on_second(**kwargs: Any) -> SimpleNamespace:
        calls["nat"] += 1
        if calls["nat"] == 2:
            raise original_fail
        rule = SimpleNamespace(kind="nat", **kwargs)
        env.nat_applied.append(rule)
        return rule

    monkeypatch.setattr(nat_service, "apply_nat_rule", nat_fails_on_second)

    with pytest.raises(ProxmoxError):
        fw.create_connection(None, 150, [SSH, WEB, DB], session=object())

    assert [r.external_port for r in env.nat_removed] == [30022]
    assert [r.domain for r in env.proxy_removed] == ["web.example.com"]
    assert env.rules == []


def test_single_port_failure_behaves_as_before(env: SimpleNamespace) -> None:
    """前端一次只送一個 port：失敗那個 port 自己會收回，回滾只刪 Proxmox 規則。"""
    env.fail_nat = ProxmoxError("Gateway unreachable")

    with pytest.raises(ProxmoxError):
        fw.create_connection(None, 150, [SSH], session=object())

    assert env.nat_removed == []
    assert env.proxy_removed == []
    assert env.rules == []


def test_all_ports_succeed_nothing_removed(env: SimpleNamespace) -> None:
    fw.create_connection(None, 150, [SSH, WEB], session=object())

    assert len(env.nat_applied) == 1
    assert len(env.proxy_applied) == 1
    assert env.nat_removed == []
    assert env.proxy_removed == []
    assert len(env.rules) == 2


def test_gateway_rollback_failure_keeps_original_error(
    env: SimpleNamespace,
) -> None:
    env.fail_proxy = BadRequestError("domain already taken")
    env.fail_nat_removal = ProxmoxError("Gateway unreachable during rollback")

    with pytest.raises(BadRequestError, match="domain already taken"):
        fw.create_connection(None, 150, [SSH, WEB], session=object())

    # Gateway 撤不掉也要繼續把 Proxmox 規則收乾淨
    assert env.rules == []


# ── 只撤這次建的那幾條，不能用 vmid／port 去刪 ───────────────────────────────


def test_remove_nat_rules_syncs_then_deletes_exactly_those_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list] = []
    monkeypatch.setattr(nat_service, "_sync_then_delete", lambda session, rules: seen.append(rules))
    rule = SimpleNamespace(id=1)

    nat_service.remove_nat_rules(object(), [rule])

    assert seen == [[rule]]


def test_remove_reverse_proxy_rules_cleans_dns_before_db_and_syncs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.repositories.reverse_proxy as rp_repo

    events: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        reverse_proxy_service,
        "_cleanup_managed_dns_record",
        lambda session, rule: events.append(("dns", rule.id)),
    )
    monkeypatch.setattr(
        rp_repo, "delete_rule", lambda session, rule: events.append(("db", rule.id))
    )
    monkeypatch.setattr(
        reverse_proxy_service, "_sync_nginx", lambda session: events.append(("sync", None))
    )

    reverse_proxy_service.remove_reverse_proxy_rules(
        object(), [SimpleNamespace(id="a"), SimpleNamespace(id="b")]
    )

    assert events == [("dns", "a"), ("db", "a"), ("dns", "b"), ("db", "b"), ("sync", None)]


def test_remove_reverse_proxy_rules_with_nothing_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_sync(session: Any) -> None:
        raise AssertionError("nothing to remove, nginx must not be touched")

    monkeypatch.setattr(reverse_proxy_service, "_sync_nginx", must_not_sync)

    reverse_proxy_service.remove_reverse_proxy_rules(object(), [])


def test_apply_nat_rule_returns_the_created_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.repositories.nat_rule as nat_repo

    monkeypatch.setattr(nat_repo, "create_rule", lambda session, rule: rule)
    monkeypatch.setattr(nat_service, "_sync_nginx_stream", lambda session: None)
    monkeypatch.setattr(nat_service, "check_port_available", lambda *a, **k: None)

    created = nat_service.apply_nat_rule(
        session=object(),
        vmid=150,
        vm_ip="10.0.0.5",
        external_port=18080,
        internal_port=80,
        protocol="tcp",
    )

    assert (created.vmid, created.external_port, created.internal_port) == (150, 18080, 80)
