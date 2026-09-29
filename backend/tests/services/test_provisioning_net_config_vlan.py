"""provisioning_service 的網卡設定在子網帶 VLAN 時要把 tag 帶進 net0。

nic_config 本身的格式由 test_subnet_vlan.py 覆蓋；這裡確認 provisioning
的包裝（_vm_net_config／_lxc_net0）有把 vlan_tag 一路傳下去，且 ipconfig0
與 nameserver 不受 tag 影響。
"""

from __future__ import annotations

from app.services.proxmox import provisioning_service as svc

_NET_CFG = {
    "bridge_name": "vmbr1",
    "prefix_len": 24,
    "gateway": "10.0.0.1",
    "vlan_tag": 120,
}


def test_vm_net_config_carries_vlan_tag() -> None:
    assert svc._vm_net_config(_NET_CFG, "10.0.0.5") == {
        "net0": "virtio,bridge=vmbr1,tag=120,firewall=1",
        "ipconfig0": "ip=10.0.0.5/24,gw=10.0.0.1",
    }


def test_vm_net_config_vlan_tag_with_dns() -> None:
    config = svc._vm_net_config({**_NET_CFG, "dns_servers": "1.1.1.1"}, "10.0.0.5")
    assert config["net0"] == "virtio,bridge=vmbr1,tag=120,firewall=1"
    assert config["nameserver"] == "1.1.1.1"


def test_lxc_net0_carries_vlan_tag() -> None:
    assert svc._lxc_net0(_NET_CFG, "10.0.0.5") == (
        "name=eth0,bridge=vmbr1,tag=120,ip=10.0.0.5/24,gw=10.0.0.1,firewall=1"
    )


def test_vm_net_config_blank_vlan_tag_is_untagged() -> None:
    config = svc._vm_net_config({**_NET_CFG, "vlan_tag": None}, "10.0.0.5")
    assert config["net0"] == "virtio,bridge=vmbr1,firewall=1"
