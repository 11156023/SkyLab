"""快照自動清理資格判定（純函式，無 I/O）。

受保護不清：``skylab-init`` 初始快照、Proxmox 的 ``current`` 偽快照、
缺 snaptime 的條目。``mining-*`` 存證快照只在對應事件已結案
（dismissed/banned）且滿 ``MINING_EVIDENCE_RETENTION_DAYS`` 天後才可清，
不再永久保護 —— 否則誤判與已停權的案件會把快照留到磁碟滿為止。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

PROTECTED_NAMES = ("skylab-init", "current")
PROTECTED_PREFIXES = ("mining-",)

# 存證快照在案件結案後仍保留的天數（申訴與稽核窗口）
MINING_EVIDENCE_RETENTION_DAYS = 30


def is_cleanup_eligible(
    *,
    name: str | None,
    snaptime: int | None,
    now: datetime,
    retention_days: int,
    mining_closed_at: datetime | None = None,
    mining_evidence_retention_days: int = MINING_EVIDENCE_RETENTION_DAYS,
) -> bool:
    """``mining_closed_at``：對應挖礦事件的結案時間；未結案（或查不到）傳 None。"""
    if not name or name in PROTECTED_NAMES:
        return False
    if snaptime is None:
        return False
    if any(name.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        if mining_closed_at is None:
            return False
        return now - mining_closed_at > timedelta(days=mining_evidence_retention_days)
    taken_at = datetime.fromtimestamp(int(snaptime), tz=timezone.utc)
    return now - taken_at > timedelta(days=retention_days)
