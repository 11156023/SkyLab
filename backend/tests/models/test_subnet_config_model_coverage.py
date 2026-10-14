"""app.models.subnet_config 子表攤平屬性（dns_server_list／blocked_subnet_list）覆蓋。"""

from __future__ import annotations

import pytest

from app.models.subnet_config import (
    SubnetBlockedSubnet,
    SubnetConfig,
    SubnetDnsServer,
)


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


def test_list_properties_follow_child_rows() -> None:
    config = SubnetConfig(
        cidr="10.10.0.0/24",
        gateway="10.10.0.1",
        bridge_name="vmbr1",
        gateway_vm_ip="10.10.0.2",
    )
    assert config.dns_server_list == []
    assert config.blocked_subnet_list == []

    config.dns_server_rows = [
        SubnetDnsServer(position=0, address="1.1.1.1"),
        SubnetDnsServer(position=1, address="8.8.8.8"),
    ]
    config.blocked_subnet_rows = [
        SubnetBlockedSubnet(position=0, cidr="192.168.0.0/16"),
        SubnetBlockedSubnet(position=1, cidr="172.16.0.0/12"),
    ]

    assert config.dns_server_list == ["1.1.1.1", "8.8.8.8"]
    assert config.blocked_subnet_list == ["192.168.0.0/16", "172.16.0.0/12"]
