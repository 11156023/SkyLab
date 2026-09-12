"""Publishing must stay independent of VM outbound firewall isolation."""

from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError
from app.repositories import proxmox_config
from app.services.network import ip_management_service
from app.services.network.publish_target_policy import assert_publishable_vm_ip


@pytest.fixture
def publish_config(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    config = SimpleNamespace(
        cidr="10.10.0.0/16",
        gateway="10.10.0.1",
        gateway_vm_ip="10.10.0.2",
        extra_blocked_subnets="10.10.0.0/16",
    )
    monkeypatch.setattr(ip_management_service, "get_subnet_config", lambda _: config)
    monkeypatch.setattr(
        proxmox_config,
        "get_proxmox_config",
        lambda _: SimpleNamespace(host="10.10.0.5", gateway_ip="10.10.0.6"),
    )
    return config


def _session() -> SimpleNamespace:
    connections = [SimpleNamespace(host="10.10.0.3", gateway_ip="10.10.0.4")]
    return SimpleNamespace(exec=lambda _: SimpleNamespace(all=lambda: connections))


@pytest.mark.parametrize("outbound_block", ["10.10.0.0/16", "10.0.0.0/8", "10.10.0.11"])
def test_vm_can_be_published_with_outbound_isolation(
    publish_config: SimpleNamespace, outbound_block: str,
) -> None:
    publish_config.extra_blocked_subnets = outbound_block

    assert_publishable_vm_ip(_session(), "10.10.0.11")

    # Publishing does not remove or rewrite the outbound firewall configuration.
    assert ip_management_service.get_extra_blocked_subnets(publish_config) == [outbound_block]


@pytest.mark.parametrize("ip", [f"10.10.0.{i}" for i in range(1, 7)])
def test_infrastructure_in_vm_subnet_cannot_be_published(
    publish_config: SimpleNamespace, ip: str,
) -> None:
    with pytest.raises(BadRequestError):
        assert_publishable_vm_ip(_session(), ip)


@pytest.mark.parametrize("ip", ["192.168.100.125", "10.11.0.11"])
def test_targets_outside_vm_subnet_cannot_be_published(
    publish_config: SimpleNamespace, ip: str,
) -> None:
    with pytest.raises(BadRequestError):
        assert_publishable_vm_ip(_session(), ip)
