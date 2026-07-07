"""配額解析與執法純函式測試。"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.resource.quota_policy import (
    DEFAULT_QUOTA,
    EffectiveQuota,
    QuotaUsage,
    check_quota_delta,
    resolve_effective_quota,
)


def _quota_row(**overrides: object) -> SimpleNamespace:
    values: dict = {
        "max_cpu_cores": 8,
        "max_memory_mb": 16384,
        "max_disk_gb": 100,
        "max_instances": 5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestResolveEffectiveQuota:
    def test_no_rows_returns_default(self) -> None:
        assert resolve_effective_quota(None, []) == DEFAULT_QUOTA

    def test_user_override_wins_over_groups(self) -> None:
        user_q = _quota_row(max_cpu_cores=2, max_instances=1)
        group_q = _quota_row(max_cpu_cores=32)
        result = resolve_effective_quota(user_q, [group_q])
        assert result.max_cpu_cores == 2
        assert result.max_instances == 1

    def test_group_quotas_take_per_field_max(self) -> None:
        g1 = _quota_row(max_cpu_cores=4, max_memory_mb=8192)
        g2 = _quota_row(max_cpu_cores=16, max_memory_mb=4096)
        result = resolve_effective_quota(None, [g1, g2])
        assert result.max_cpu_cores == 16
        assert result.max_memory_mb == 8192


class TestCheckQuotaDelta:
    def _quota(self) -> EffectiveQuota:
        return EffectiveQuota(
            max_cpu_cores=8, max_memory_mb=16384, max_disk_gb=100, max_instances=5
        )

    def test_within_quota_passes(self) -> None:
        usage = QuotaUsage(cpu_cores=4, memory_mb=8192, disk_gb=40, instances=2)
        assert check_quota_delta(usage, self._quota(), delta_cores=4) == []

    def test_cpu_over_quota_reports(self) -> None:
        usage = QuotaUsage(cpu_cores=6, memory_mb=0, disk_gb=0, instances=0)
        violations = check_quota_delta(usage, self._quota(), delta_cores=4)
        assert len(violations) == 1
        assert "CPU" in violations[0]

    def test_multiple_violations_all_reported(self) -> None:
        usage = QuotaUsage(cpu_cores=8, memory_mb=16384, disk_gb=100, instances=5)
        violations = check_quota_delta(
            usage,
            self._quota(),
            delta_cores=1,
            delta_memory_mb=1,
            delta_disk_gb=1,
            delta_instances=1,
        )
        assert len(violations) == 4

    def test_negative_delta_always_passes(self) -> None:
        usage = QuotaUsage(cpu_cores=8, memory_mb=16384, disk_gb=100, instances=5)
        assert check_quota_delta(usage, self._quota(), delta_cores=-2) == []
