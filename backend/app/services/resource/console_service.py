"""VNC 主控台的附屬資訊。

PVE 的 Display 設定 ``vga: <type>,clipboard=vnc`` 會在 QEMU 掛上 qemu-vdagent，
noVNC 的 ClientCutText 才會被送進 guest 剪貼簿（guest 內仍需 spice-vdagent）。
沒開這個選項時貼上會無聲無效，所以主控台要把旗標回給前端提示。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.proxmox import proxmox_service


def vnc_clipboard_enabled(config: Mapping[str, Any]) -> bool:
    """由 VM 設定判斷 Display 是否開了 ``clipboard=vnc``。"""
    vga = config.get("vga")
    if not isinstance(vga, str):
        return False
    for part in vga.split(","):
        key, has_value, value = part.strip().partition("=")
        if has_value and key == "clipboard":
            return value == "vnc"
    return False


def get_vnc_clipboard_enabled(*, node: str, vmid: int) -> bool:
    """讀實際生效中的設定（改 Display 要重開機才生效，不看 pending 值）。"""
    config = proxmox_service.get_config(node, vmid, "qemu", current=True)
    return vnc_clipboard_enabled(config)
