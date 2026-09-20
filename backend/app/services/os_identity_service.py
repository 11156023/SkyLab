"""Guest OS 身份偵測的 I/O 編排與 DB 持久化（保存一次制）。

設計原則：

- OS 身份是機器的不可變屬性：偵測一次 → 存 ``resources.guest_os`` →
  之後一律讀 DB，不在 prompt / 評分時反覆探測。
- 建置當下只寫入 PVE config 可得的 hint（LXC 為發行版欄位、QEMU 僅
  family hint）；QEMU Guest Agent 探測延後到第一次被需要時（lazy）
  補齊並回寫，避免建置流程等 agent 開機。
- 探測失敗不阻擋任何流程：best-effort，寫不進就回 None 留待下次。

純函式正規化在 ``app.infrastructure.proxmox.os_detection``。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session

from app.infrastructure.proxmox import guest
from app.infrastructure.proxmox import operations as proxmox_ops
from app.infrastructure.proxmox import os_detection as od

logger = logging.getLogger(__name__)

_RESOURCE_TYPE_QEMU = "qemu"
_RESOURCE_TYPE_LXC = "lxc"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp(payload: od.GuestOS) -> od.GuestOS:
    payload["detected_at"] = _now_iso()
    return payload


def _read_ostype(node: str, vmid: int, resource_type: str) -> str | None:
    """讀 PVE guest config 的 ostype（單次 API GET，不需 agent）。"""
    try:
        config = proxmox_ops.get_config(node, vmid, resource_type)  # type: ignore[arg-type]
        value = config.get("ostype")
        return str(value) if value is not None else None
    except Exception as exc:
        logger.warning(
            "Guest ostype lookup failed for %s %d on %s: %s",
            resource_type,
            vmid,
            node,
            exc,
        )
        return None


def initial_guest_os(
    *,
    resource_type: str,
    node: str,
    vmid: int,
) -> od.GuestOS | None:
    """建置完成當下的首次身份寫入（不探測 agent）。

    LXC 的 ``ostype`` 是真實發行版欄位（medium）；QEMU 的 ``ostype`` 只
    能給 family hint（win* medium、l26 linux low）。讀不到就回 None。
    """
    normalized_type = (resource_type or "").strip().lower()
    if normalized_type == _RESOURCE_TYPE_LXC:
        payload = od.normalize_lxc_ostype(_read_ostype(node, vmid, normalized_type))
    elif normalized_type == _RESOURCE_TYPE_QEMU:
        payload = od.normalize_qemu_ostype(_read_ostype(node, vmid, normalized_type))
    else:
        return None
    if payload.get("family") == od.OS_FAMILY_UNKNOWN:
        return None
    return _stamp(payload)


def _probe_qga(node: str, vmid: int) -> od.GuestOS | None:
    """QEMU Guest Agent 探測；agent 不回應回 None。"""
    if not guest.ping_qemu_agent(node, vmid):
        return None
    payload = guest.get_osinfo_qemu(node, vmid)
    return od.normalize_qga_osinfo(payload)


def ensure_guest_os(
    session: Session,
    resource: Any,
    *,
    node: str,
    resource_type: str,
) -> od.GuestOS | None:
    """Lazy 補偵測：``guest_os`` 為空時探測一次並回寫（flush，由呼叫端 commit）。

    - QEMU：先走 Guest Agent（high）；agent 不回應時退回 config
      ``ostype`` family hint（low），不標示成特定發行版。
    - LXC：config ``ostype``（medium）。
    - 已有結構化身份、探測失敗、或 PVE 連線問題時不動 DB。
    """
    if resource is None:
        return None
    existing = getattr(resource, "guest_os", None)
    if isinstance(existing, dict) and existing.get("family") in {
        od.OS_FAMILY_LINUX,
        od.OS_FAMILY_WINDOWS,
    }:
        return existing

    vmid = getattr(resource, "vmid", None)
    if not isinstance(vmid, int) or not node:
        return None
    normalized_type = (resource_type or "").strip().lower()
    try:
        if normalized_type == _RESOURCE_TYPE_QEMU:
            payload = _probe_qga(node, vmid)
            if payload is None:
                payload = od.normalize_qemu_ostype(
                    _read_ostype(node, vmid, normalized_type)
                )
        elif normalized_type == _RESOURCE_TYPE_LXC:
            payload = od.normalize_lxc_ostype(_read_ostype(node, vmid, normalized_type))
        else:
            return None
    except Exception as exc:
        logger.warning(
            "Guest OS lazy probe failed for %s %d on %s: %s",
            normalized_type,
            vmid,
            node,
            exc,
        )
        return None
    if not isinstance(payload, dict) or payload.get("family") == od.OS_FAMILY_UNKNOWN:
        return None
    _stamp(payload)
    resource.guest_os = payload
    session.add(resource)
    session.flush()
    return payload


__all__ = ["ensure_guest_os", "initial_guest_os"]
