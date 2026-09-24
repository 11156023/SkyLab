"""判斷 Teacher Judge 目標機器的作業系統（executor 與 run service 共用）。"""

from __future__ import annotations

from typing import Any

from app.infrastructure.proxmox.os_detection import is_windows_guest_identity

_WINDOWS_MARKERS = ("windows", "win32", "win64", "win10", "win11", "microsoft")


def resource_os_context(resource: Any) -> str:
    """給錯誤訊息用的 OS 描述：結構化 guest_os 優先，否則拼接舊字串欄位。"""
    identity = getattr(resource, "guest_os", None)
    if isinstance(identity, dict):
        structured = (
            str(identity.get("pretty_name") or "").strip()
            or str(identity.get("id") or "").strip()
        )
        if structured:
            return structured
    return " ".join(
        str(value).strip()
        for value in (
            getattr(resource, "os_info", None),
            getattr(resource, "environment_type", None),
        )
        if value is not None and str(value).strip()
    )


def is_windows_target(resource: Any) -> bool:
    """結構化 guest_os 為準；無結構資料時退回舊的字串判斷。"""
    structured = is_windows_guest_identity(getattr(resource, "guest_os", None))
    if structured is not None:
        return structured
    normalized = resource_os_context(resource).casefold()
    return any(marker in normalized for marker in _WINDOWS_MARKERS)
