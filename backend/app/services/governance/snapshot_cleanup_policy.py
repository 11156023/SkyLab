"""快照自動清理資格判定（純函式，無 I/O）。

受保護不清：``skylab-init`` 初始快照、``mining-*`` 存證快照、
Proxmox 的 ``current`` 偽快照、缺 snaptime 的條目。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

PROTECTED_NAMES = ("skylab-init", "current")
PROTECTED_PREFIXES = ("mining-",)


def is_cleanup_eligible(
    *,
    name: str | None,
    snaptime: int | None,
    now: datetime,
    retention_days: int,
) -> bool:
    if not name or name in PROTECTED_NAMES:
        return False
    if any(name.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return False
    if snaptime is None:
        return False
    taken_at = datetime.fromtimestamp(int(snaptime), tz=timezone.utc)
    return now - taken_at > timedelta(days=retention_days)
