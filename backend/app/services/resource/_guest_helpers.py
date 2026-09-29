"""資源設定類 service 共用的 guest 小工具（credentials／settings／reset）。

``resource_info`` 一律是 ``proxmox_service.find_resource`` 回傳的那一筆
（cluster/resources 格式：``type`` 為 qemu／lxc、``status`` 為即時狀態）。
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from app.core.i18n import t
from app.exceptions import ProxmoxError
from app.services.proxmox import proxmox_service

logger = logging.getLogger(__name__)

GuestType = Literal["qemu", "lxc"]


def resource_type(resource_info: dict[str, Any]) -> GuestType:
    """``lxc`` 以外一律當 ``qemu``。"""
    return "lxc" if str(resource_info.get("type") or "") == "lxc" else "qemu"


def is_running(resource_info: dict[str, Any]) -> bool:
    return str(resource_info.get("status") or "") == "running"


def read_config(
    resource_info: dict[str, Any], vmid: int, rtype: GuestType
) -> dict[str, Any]:
    """讀 guest config；失敗記 log 並轉成使用者看得懂的 ProxmoxError。"""
    try:
        return proxmox_service.get_config(resource_info["node"], vmid, rtype)
    except Exception as exc:
        logger.error("Failed to read config for %s: %s", vmid, exc)
        raise ProxmoxError(t("resource_settings.readConfigFailed", vmid=vmid))


__all__ = ["GuestType", "is_running", "read_config", "resource_type"]
