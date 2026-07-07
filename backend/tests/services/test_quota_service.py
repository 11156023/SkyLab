"""配額 I/O 層測試（mock DB 查詢與 PVE）。"""

from __future__ import annotations

import uuid

import pytest

from app.exceptions import ConflictError
from app.services.resource import quota_service
from app.services.resource.quota_policy import DEFAULT_QUOTA, EffectiveQuota, QuotaUsage

USER_ID = uuid.uuid4()


@pytest.fixture()
def stub_rows(monkeypatch: pytest.MonkeyPatch):
    """樁掉 DB 查詢：回傳 (user_quota, group_quotas)。"""

    def _set(user_quota=None, group_quotas=None):
        monkeypatch.setattr(
            quota_service,
            "_quota_rows_for_user",
            lambda session, user_id: (user_quota, group_quotas or []),
        )

    return _set


def test_get_effective_quota_defaults(stub_rows) -> None:
    stub_rows()
    assert quota_service.get_effective_quota(None, USER_ID) == DEFAULT_QUOTA


def test_get_usage_sums_cluster_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        quota_service, "_owned_vmids", lambda session, user_id: [101, 102]
    )
    cluster = [
        {"vmid": 101, "maxcpu": 2, "maxmem": 2 * 1024**3, "maxdisk": 20 * 1024**3},
        {"vmid": 102, "maxcpu": 4, "maxmem": 4 * 1024**3, "maxdisk": 30 * 1024**3},
        {"vmid": 999, "maxcpu": 64, "maxmem": 64 * 1024**3, "maxdisk": 999 * 1024**3},
    ]
    usage = quota_service.get_usage(None, USER_ID, cluster_resources=cluster)
    assert usage == QuotaUsage(cpu_cores=6, memory_mb=6144, disk_gb=50, instances=2)


def test_check_quota_raises_conflict(monkeypatch: pytest.MonkeyPatch, stub_rows) -> None:
    stub_rows(user_quota=None, group_quotas=[])
    monkeypatch.setattr(
        quota_service,
        "get_usage",
        lambda session, user_id, cluster_resources=None: QuotaUsage(
            cpu_cores=8, memory_mb=0, disk_gb=0, instances=0
        ),
    )
    with pytest.raises(ConflictError):
        quota_service.check_quota(None, USER_ID, delta_cores=1)


def test_check_quota_fail_open_on_pve_error(
    monkeypatch: pytest.MonkeyPatch, stub_rows
) -> None:
    stub_rows()

    def _boom(session, user_id, cluster_resources=None):
        raise RuntimeError("PVE down")

    monkeypatch.setattr(quota_service, "get_usage", _boom)
    quota_service.check_quota(None, USER_ID, delta_cores=100)  # 不 raise
