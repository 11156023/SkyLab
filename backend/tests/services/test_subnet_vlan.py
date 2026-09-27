"""實驗室子網的選填 VLAN：schema 驗證與 net0 組字串。"""

import pytest
from pydantic import ValidationError

from app.schemas.ip_management import SubnetConfigCreate
from app.services.network import nic_config

_BASE = {
    "cidr": "10.10.0.0/24",
    "gateway": "10.10.0.1",
    "bridge_name": "vmbr1",
    "gateway_vm_ip": "10.10.0.2",
}


def _net_cfg(vlan_tag: int | None) -> dict:
    return {
        "bridge_name": "vmbr1",
        "vlan_tag": vlan_tag,
        "prefix_len": 24,
        "gateway": "10.10.0.1",
    }


@pytest.mark.parametrize("raw", [None, "", "  "])
def test_vlan_tag_blank_means_untagged(raw):
    assert SubnetConfigCreate(**_BASE, vlan_tag=raw).vlan_tag is None


def test_vlan_tag_omitted_defaults_to_none():
    assert SubnetConfigCreate(**_BASE).vlan_tag is None


@pytest.mark.parametrize("raw", [1, 100, "200", 4094])
def test_vlan_tag_accepts_valid_range(raw):
    assert SubnetConfigCreate(**_BASE, vlan_tag=raw).vlan_tag == int(raw)


@pytest.mark.parametrize("raw", [0, 4095, -1, 5000, "abc"])
def test_vlan_tag_rejects_out_of_range(raw):
    with pytest.raises(ValidationError):
        SubnetConfigCreate(**_BASE, vlan_tag=raw)


def test_qemu_net0_without_vlan_is_unchanged():
    assert nic_config.qemu_net0(_net_cfg(None)) == "virtio,bridge=vmbr1,firewall=1"


def test_qemu_net0_with_vlan_adds_tag():
    assert (
        nic_config.qemu_net0(_net_cfg(120))
        == "virtio,bridge=vmbr1,tag=120,firewall=1"
    )


def test_lxc_net0_without_vlan_is_unchanged():
    assert nic_config.lxc_net0(_net_cfg(None), "10.10.0.50") == (
        "name=eth0,bridge=vmbr1,ip=10.10.0.50/24,gw=10.10.0.1,firewall=1"
    )


def test_lxc_net0_with_vlan_adds_tag():
    assert nic_config.lxc_net0(_net_cfg(7), "10.10.0.50") == (
        "name=eth0,bridge=vmbr1,tag=7,ip=10.10.0.50/24,gw=10.10.0.1,firewall=1"
    )


def test_net0_tolerates_legacy_net_cfg_without_vlan_key():
    legacy = {"bridge_name": "vmbr0", "prefix_len": 24, "gateway": "10.0.0.1"}
    assert nic_config.qemu_net0(legacy) == "virtio,bridge=vmbr0,firewall=1"
