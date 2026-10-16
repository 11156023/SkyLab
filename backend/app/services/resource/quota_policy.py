"""配額解析與執法純函式（無 I/O，可單測）。

解析順序：user 覆寫（整列全勝）→ 全域預設（quota_config singleton）→ 內建預設。
上限值 0 = 無限制（UNLIMITED），該欄位不做執法。
管理員不受配額限制，一律是 NO_QUOTA（由 quota_service 在解析前判斷身分）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.i18n import t


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


UNLIMITED = 0

DEFAULT_QUOTA = EffectiveQuota(
    max_cpu_cores=8, max_memory_mb=16384, max_disk_gb=100, max_instances=5
)

NO_QUOTA = EffectiveQuota(
    max_cpu_cores=UNLIMITED,
    max_memory_mb=UNLIMITED,
    max_disk_gb=UNLIMITED,
    max_instances=UNLIMITED,
)


def is_unlimited(quota: EffectiveQuota) -> bool:
    """四個欄位都不設限：執法點連用量都不必查。"""
    return quota == NO_QUOTA


def resolve_effective_quota(
    user_quota: Any | None, global_quota: Any | None = None
) -> EffectiveQuota:
    """個人覆寫整列全勝；無覆寫時套用全域預設；全域列不存在才走內建預設。"""
    for source in (user_quota, global_quota):
        if source is not None:
            return EffectiveQuota(
                max_cpu_cores=int(source.max_cpu_cores),
                max_memory_mb=int(source.max_memory_mb),
                max_disk_gb=int(source.max_disk_gb),
                max_instances=int(source.max_instances),
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
    """回傳超限訊息清單；空 list 表示通過。負增量（縮減）永遠通過該欄位。

    上限為 UNLIMITED（0）的欄位不檢查。
    """
    violations: list[str] = []
    checks = [
        ("quota.violation.cpu", usage.cpu_cores, delta_cores, quota.max_cpu_cores),
        ("quota.violation.memory", usage.memory_mb, delta_memory_mb, quota.max_memory_mb),
        ("quota.violation.disk", usage.disk_gb, delta_disk_gb, quota.max_disk_gb),
        ("quota.violation.instances", usage.instances, delta_instances, quota.max_instances),
    ]
    for key, used, delta, limit in checks:
        if limit == UNLIMITED:
            continue
        if delta > 0 and used + delta > limit:
            violations.append(t(key, used=used, delta=delta, limit=limit))
    return violations
