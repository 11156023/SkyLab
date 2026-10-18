"""Coverage tests for app.services.network.nat_service."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import app.infrastructure.ssh as ssh_pkg
import app.repositories.gateway_config as gw_repo
import app.repositories.nat_rule as nat_repo
from app.exceptions import BadRequestError, ProxmoxError
from app.services.network import nat_service
from app.services.network import nginx_gateway_service as nginx


def test_check_port_available_rejects_taken_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        nat_repo, "is_external_port_taken", lambda session, port, protocol: True
    )
    with pytest.raises(BadRequestError):
        nat_service.check_port_available(30123, "tcp", object())


def test_sync_requires_configured_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gw_repo, "get_gateway_config", lambda session: None)
    with pytest.raises(ProxmoxError):
        nat_service._sync_nginx_stream(object())


class _Client:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(client=_Client(), error=None, written=[])
    monkeypatch.setattr(
        gw_repo,
        "get_gateway_config",
        lambda session: SimpleNamespace(
            host="10.0.0.2",
            ssh_port=22,
            ssh_user="root",
            encrypted_private_key="enc",
        ),
    )
    monkeypatch.setattr(gw_repo, "get_decrypted_private_key", lambda config: "PEM")
    monkeypatch.setattr(nginx, "lock_config_writes", lambda session: None)
    monkeypatch.setattr(nat_repo, "list_rules", lambda session: [])
    monkeypatch.setattr(ssh_pkg, "create_key_client", lambda *args: state.client)

    def fake_write(client: Any, path: str, content: str) -> None:
        if state.error is not None:
            raise state.error
        state.written.append(path)

    monkeypatch.setattr(nginx, "write_validated_config", fake_write)
    return state


def test_sync_propagates_proxmox_errors(gateway: SimpleNamespace) -> None:
    gateway.error = ProxmoxError("nginx -t failed")

    with pytest.raises(ProxmoxError, match="nginx -t failed"):
        nat_service._sync_nginx_stream(object())
    assert gateway.client.closed is True


def test_sync_wraps_unexpected_errors(gateway: SimpleNamespace) -> None:
    gateway.error = OSError("sftp broken")

    with pytest.raises(ProxmoxError):
        nat_service._sync_nginx_stream(object(), rules=[])
    assert gateway.client.closed is True


def test_apply_nat_rule_logs_when_compensation_delete_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(nat_service, "check_port_available", lambda *a, **k: None)
    monkeypatch.setattr(nat_service, "assert_publishable_vm_ip", lambda *a, **k: None)
    monkeypatch.setattr(nat_repo, "create_rule", lambda session, rule: rule)

    def broken_delete(session: Any, rule: Any) -> None:
        raise RuntimeError("db gone")

    def failing_sync(session: Any) -> None:
        raise ProxmoxError("gateway unreachable")

    monkeypatch.setattr(nat_repo, "delete_rule", broken_delete)
    monkeypatch.setattr(nat_service, "_sync_nginx_stream", failing_sync)

    with pytest.raises(ProxmoxError, match="gateway unreachable"):
        nat_service.apply_nat_rule(
            session=object(),
            vmid=150,
            vm_ip="10.0.0.5",
            external_port=18080,
            internal_port=80,
            protocol="tcp",
        )
