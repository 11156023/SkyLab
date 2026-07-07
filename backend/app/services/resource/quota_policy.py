"""配額解析與執法純函式（無 I/O，可單測）。

解析順序：user 覆寫（整列全勝）→ 所屬群組逐欄位取最大 → 內建預設。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EffectiveQuota:
    max_cpu_cores: int
    max_memory_mb: int
    max_disk_gb: int
    max_instances: int


@dataclass(frozen=True)
class QuotaUsage:
    cpu_cores: int
    memory_mb: int
    disk_gb: int
    instances: int


DEFAULT_QUOTA = EffectiveQuota(
    max_cpu_cores=8, max_memory_mb=16384, max_disk_gb=100, max_instances=5
)


def resolve_effective_quota(
    user_quota: Any | None, group_quotas: list[Any]
) -> EffectiveQuota:
    if user_quota is not None:
        return EffectiveQuota(
            max_cpu_cores=int(user_quota.max_cpu_cores),
            max_memory_mb=int(user_quota.max_memory_mb),
            max_disk_gb=int(user_quota.max_disk_gb),
            max_instances=int(user_quota.max_instances),
        )
    if group_quotas:
        return EffectiveQuota(
            max_cpu_cores=max(int(q.max_cpu_cores) for q in group_quotas),
            max_memory_mb=max(int(q.max_memory_mb) for q in group_quotas),
            max_disk_gb=max(int(q.max_disk_gb) for q in group_quotas),
            max_instances=max(int(q.max_instances) for q in group_quotas),
        )
    return DEFAULT_QUOTA


def check_quota_delta(
    usage: QuotaUsage,
    quota: EffectiveQuota,
    *,
    delta_cores: int = 0,
    delta_memory_mb: int = 0,
    delta_disk_gb: int = 0,
    delta_instances: int = 0,
) -> list[str]:
    """回傳超限訊息清單；空 list 表示通過。負增量（縮減）永遠通過該欄位。"""
    violations: list[str] = []
    checks = [
        ("CPU", usage.cpu_cores, delta_cores, quota.max_cpu_cores, "cores"),
        ("記憶體", usage.memory_mb, delta_memory_mb, quota.max_memory_mb, "MB"),
        ("磁碟", usage.disk_gb, delta_disk_gb, quota.max_disk_gb, "GB"),
        ("實例數", usage.instances, delta_instances, quota.max_instances, "台"),
    ]
    for label, used, delta, limit, unit in checks:
        if delta > 0 and used + delta > limit:
            violations.append(
                f"{label}超出配額（目前 {used} + 新增 {delta} > 上限 {limit} {unit}）"
            )
    return violations
