"""VM／LXC 網卡（net0）設定字串 — 純函式，不碰 DB 與 PVE。

net_cfg 是 ip_management_service.get_network_config_for_vm 回傳的 dict；
子網設了 VLAN 時會帶 vlan_tag，網卡就加上 PVE 的 tag=N（bridge 須為
VLAN aware 或底下有對應的 VLAN 介面）；沒設就維持 untagged。
"""

from typing import Any


def _vlan_suffix(net_cfg: dict[str, Any]) -> str:
    tag = net_cfg.get("vlan_tag")
    return f",tag={int(tag)}" if tag else ""


def qemu_net0(net_cfg: dict[str, Any]) -> str:
    """QEMU VM 的 net0（IP 另由 cloud-init ipconfig0 設定）。"""
    return f"virtio,bridge={net_cfg['bridge_name']}{_vlan_suffix(net_cfg)},firewall=1"


def lxc_net0(net_cfg: dict[str, Any], ip: str) -> str:
    """LXC 的 net0（IP／閘道直接寫在網卡設定上）。"""
    return (
        f"name=eth0,bridge={net_cfg['bridge_name']}{_vlan_suffix(net_cfg)},"
        f"ip={ip}/{net_cfg['prefix_len']},"
        f"gw={net_cfg['gateway']},firewall=1"
    )


__all__ = ["lxc_net0", "qemu_net0"]
