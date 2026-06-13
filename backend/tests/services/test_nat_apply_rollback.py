"""Regression test for ``apply_nat_rule`` compensation-on-failure.

The DB rule is committed BEFORE haproxy sync; if sync fails the rule must be
deleted again, otherwise it permanently occupies the external port ("port
already taken") while never actually forwarding traffic.
"""

from __future__ import annotations

from typing import Any

import pytest

import app.repositories.nat_rule as nat_repo
from app.exceptions import ProxmoxError
from app.services.network import nat_service


def test_apply_nat_rule_deletes_db_rule_when_sync_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []
    deleted: list[Any] = []

    def fake_create_rule(session: Any, rule: Any) -> Any:  # noqa: ARG001
        created.append(rule)
        return rule

    def fake_delete_rule(session: Any, rule: Any) -> None:  # noqa: ARG001
        deleted.append(rule)

    def failing_sync(session: Any) -> None:  # noqa: ARG001
        raise ProxmoxError("Gateway VM unreachable")

    monkeypatch.setattr(nat_repo, "create_rule", fake_create_rule)
    monkeypatch.setattr(nat_repo, "delete_rule", fake_delete_rule)
    monkeypatch.setattr(nat_service, "_sync_haproxy", failing_sync)
    monkeypatch.setattr(
        nat_service, "check_port_available", lambda *a, **k: None  # noqa: ARG005
    )

    with pytest.raises(ProxmoxError):
        nat_service.apply_nat_rule(
            session=object(),
            vmid=150,
            vm_ip="10.0.0.5",
            external_port=18080,
            internal_port=80,
            protocol="tcp",
        )

    assert len(created) == 1
    assert deleted == created  # the just-created rule was compensated away


def test_apply_nat_rule_keeps_rule_when_sync_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []
    deleted: list[Any] = []

    monkeypatch.setattr(
        nat_repo, "create_rule", lambda session, rule: created.append(rule) or rule
    )
    monkeypatch.setattr(
        nat_repo, "delete_rule", lambda session, rule: deleted.append(rule)
    )
    monkeypatch.setattr(nat_service, "_sync_haproxy", lambda session: None)
    monkeypatch.setattr(
        nat_service, "check_port_available", lambda *a, **k: None  # noqa: ARG005
    )

    nat_service.apply_nat_rule(
        session=object(),
        vmid=150,
        vm_ip="10.0.0.5",
        external_port=18080,
        internal_port=80,
        protocol="tcp",
    )

    assert len(created) == 1
    assert deleted == []
