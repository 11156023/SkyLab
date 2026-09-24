"""VMRequest 結案標記：資源被刪除／轉範本後寫進申請單的固定字串。

這些字串是跨模組的狀態協定（resource_service 寫入、jobs_service 與前端
判讀），放在 domain 層讓讀寫雙方都從同一個地方拿，不用互相 import service。
"""

from __future__ import annotations

RESOURCE_DELETED_BY_USER_MARKER = "Resource deleted by user"
RESOURCE_DELETED_ORPHAN_MARKER = "Resource deleted (orphan DB cleanup)"
RESOURCE_CONVERTED_TO_TEMPLATE_MARKER = "Resource converted to template"

RESOURCE_DELETED_MARKERS = frozenset(
    {
        RESOURCE_DELETED_BY_USER_MARKER,
        RESOURCE_DELETED_ORPHAN_MARKER,
        RESOURCE_CONVERTED_TO_TEMPLATE_MARKER,
    }
)

__all__ = [
    "RESOURCE_CONVERTED_TO_TEMPLATE_MARKER",
    "RESOURCE_DELETED_BY_USER_MARKER",
    "RESOURCE_DELETED_MARKERS",
    "RESOURCE_DELETED_ORPHAN_MARKER",
]
