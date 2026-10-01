"""快照可用性：這台機器「當下」能不能使用快照。

判定來源是 PVE 的 feature API（``proxmox_service.has_snapshot_feature``），
與 PVE 自己在建立快照前做的檢查同一套——磁碟所在 storage 或磁碟格式不支援
（LVM 非 thin、目錄型 storage 上的 raw…）或機器已轉成範本時都會回不支援。
每次都即時查，不快取：磁碟搬移、加掛磁碟後結果就會變。

不可用時，使用者面向的快照功能（建立／還原／刪除、一鍵重置、補建初始快照）
一律拒絕；前端依 ``get_snapshot_capability`` 的結果把整個快照分頁藏起來。
系統自己的快照動作（挖礦存證、過期清理、轉範本前清快照）本來就是
best-effort，不經這裡。

獨立成模組是因為 ``network/snapshot_service`` 與 ``resource/reset_service``
都要用，而前者已經 import 後者。
"""

from __future__ import annotations

import logging
from typing import Literal, TypedDict

from app.core.i18n import t
from app.exceptions import ConflictError, ProxmoxError
from app.services.proxmox import proxmox_service

logger = logging.getLogger(__name__)

GuestType = Literal["qemu", "lxc"]
UnavailableReason = Literal["unsupported", "unknown"]


class SnapshotCapabilityDict(TypedDict):
    available: bool
    reason: UnavailableReason | None


def get_snapshot_capability(
    node: str, vmid: int, rtype: GuestType
) -> SnapshotCapabilityDict:
    """回傳這台機器當下的快照可用性；查詢本身失敗時視為不可用（fail-closed）。

    查不到（PVE 連不上、節點離線）時快照操作本來也做不了，回 ``unknown``
    讓前端同樣把快照功能藏起來，而不是露出一排按了必失敗的按鈕。
    """
    try:
        supported = proxmox_service.has_snapshot_feature(node, vmid, rtype)
    except Exception:
        logger.warning(
            "Snapshot feature check failed for vmid=%s on node=%s",
            vmid,
            node,
            exc_info=True,
        )
        return {"available": False, "reason": "unknown"}
    if not supported:
        return {"available": False, "reason": "unsupported"}
    return {"available": True, "reason": None}


def require_snapshot_available(node: str, vmid: int, rtype: GuestType) -> None:
    """快照寫入操作的守門：當下不能用快照就拒絕，不把請求送到 PVE。

    PVE 明確回報不支援 → 409；連可用性都查不到（PVE 連不上、機器的 storage
    已不存在…）→ 502，訊息請使用者稍後再試，而不是讓 PVE 的原始例外漏出去。
    """
    capability = get_snapshot_capability(node, vmid, rtype)
    if capability["available"]:
        return
    if capability["reason"] == "unknown":
        raise ProxmoxError(t("snapshot.checkFailed"))
    raise ConflictError(t("snapshot.unavailable"))


__all__ = [
    "SnapshotCapabilityDict",
    "get_snapshot_capability",
    "require_snapshot_available",
]
