"""NAT 規則刪除順序：haproxy 同步成功才刪 DB。

反過來做的話，同步失敗就會留下「DB 查不到、Gateway 還在轉發」的孤兒 port，
既撤不掉，那個對外 port 還會被重新配給別人。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import app.repositories.nat_rule as nat_repo
from app.exceptions import ProxmoxError
from app.services.network import nat_service


def _rule(rule_id: str, vmid: int = 150, internal_port: int = 80) -> SimpleNamespace:
    return SimpleNamespace(
        id=rule_id,
        vmid=vmid,
        vm_ip="10.0.0.5",
        external_port=18080,
        internal_port=internal_port,
        protocol="tcp",
    )


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(
        rules=[_rule("a"), _rule("b", internal_port=443)],
        synced=[],
        deleted=[],
        sync_fails=False,
    )

    def fake_sync(session: Any, rules: list | None = None) -> None:  # noqa: ARG001
        if state.sync_fails:
            raise ProxmoxError("Gateway VM unreachable")
        state.synced.append(list(rules or []))

    monkeypatch.setattr(nat_service, "_sync_haproxy", fake_sync)
    monkeypatch.setattr(nat_repo, "list_rules", lambda session: state.rules)  # noqa: ARG005
    monkeypatch.setattr(
        nat_repo,
        "delete_rules",
        lambda session, rules, **_kwargs: state.deleted.extend(rules),  # noqa: ARG005
    )
    return state


def test_remove_rules_for_vmid_syncs_remaining_before_delete(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    doomed = [env.rules[0]]
    monkeypatch.setattr(nat_repo, "list_rules_by_vmid", lambda session, vmid: doomed)  # noqa: ARG005

    nat_service.remove_nat_rules_for_vmid(object(), 150)

    assert env.synced == [[env.rules[1]]]
    assert env.deleted == doomed


def test_remove_rules_for_vmid_without_rules_does_nothing(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nat_repo, "list_rules_by_vmid", lambda session, vmid: [])  # noqa: ARG005

    nat_service.remove_nat_rules_for_vmid(object(), 999)

    assert env.synced == []
    assert env.deleted == []


def test_remove_rules_by_internal_port_syncs_remaining_before_delete(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    doomed = [env.rules[1]]
    monkeypatch.setattr(
        nat_repo,
        "list_rules_by_vmid_and_port",
        lambda session, vmid, internal_port, protocol: doomed,  # noqa: ARG005
    )

    nat_service.remove_nat_rules_by_internal_port(object(), 150, 443, "tcp")

    assert env.synced == [[env.rules[0]]]
    assert env.deleted == doomed
