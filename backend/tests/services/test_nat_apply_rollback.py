"""Regression test for ``apply_nat_rule`` compensation-on-failure.

The DB rule is committed BEFORE the nginx sync; if sync fails the rule must be
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

    def fake_create_rule(session: Any, rule: Any) -> Any:
        created.append(rule)
        return rule

    def fake_delete_rule(session: Any, rule: Any) -> None:
        deleted.append(rule)

    def failing_sync(session: Any) -> None:
        raise ProxmoxError("Gateway VM unreachable")

    monkeypatch.setattr(nat_repo, "create_rule", fake_create_rule)
    monkeypatch.setattr(nat_repo, "delete_rule", fake_delete_rule)
    monkeypatch.setattr(nat_service, "_sync_nginx_stream", failing_sync)
    monkeypatch.setattr(
        nat_service, "check_port_available", lambda *a, **k: None
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
    monkeypatch.setattr(nat_service, "_sync_nginx_stream", lambda session: None)
    monkeypatch.setattr(
        nat_service, "check_port_available", lambda *a, **k: None
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


def test_apply_nat_rule_turns_port_race_into_bad_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """兩個請求同時配到同一個對外 port：唯一約束擋下的那個要變成 400 並 rollback，
    課程發布（publish_forward）才會換號重試，session 也還能繼續用。"""
    from sqlalchemy.exc import IntegrityError

    from app.exceptions import BadRequestError

    class FakeSession:
        rolled_back = False

        def rollback(self) -> None:
            self.rolled_back = True

    def racing_create_rule(session: Any, rule: Any) -> Any:
        raise IntegrityError("INSERT", {}, Exception("duplicate key"))

    synced: list[Any] = []
    monkeypatch.setattr(nat_repo, "create_rule", racing_create_rule)
    monkeypatch.setattr(nat_service, "_sync_nginx_stream", synced.append)
    monkeypatch.setattr(
        nat_service, "check_port_available", lambda *a, **k: None
    )
    session = FakeSession()

    with pytest.raises(BadRequestError):
        nat_service.apply_nat_rule(
            session=session,
            vmid=150,
            vm_ip="10.0.0.5",
            external_port=18080,
            internal_port=80,
            protocol="tcp",
        )

    assert session.rolled_back is True
    assert synced == []
