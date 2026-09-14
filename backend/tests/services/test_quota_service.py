"""配額 I/O 層測試（mock DB 查詢與 PVE）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import ConflictError
from app.models import QuotaConfig
from app.services.resource import quota_service
from app.services.resource.quota_policy import DEFAULT_QUOTA, QuotaUsage

USER_ID = uuid.uuid4()


def _quota_row(**overrides: object) -> SimpleNamespace:
    values: dict = {
        "max_cpu_cores": 8,
        "max_memory_mb": 16384,
        "max_disk_gb": 100,
        "max_instances": 5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeSession:
    """最小 DB session 替身：只支援 singleton 讀寫路徑。"""

    def __init__(self, existing: object | None = None) -> None:
        self.existing = existing
        self.added: list[object] = []
        self.commits = 0

    def get(self, model: type, pk: object) -> object | None:
        del model, pk
        return self.existing

    def add(self, obj: object) -> None:
        self.added.append(obj)
        self.existing = obj

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, obj: object) -> None:
        del obj


@pytest.fixture()
def stub_quota(monkeypatch: pytest.MonkeyPatch):
    """樁掉 DB 查詢：個人覆寫與全域預設兩個來源。"""

    def _set(user_quota=None, global_quota=None):
        monkeypatch.setattr(
            quota_service,
            "_quota_for_user",
            lambda session, user_id: user_quota,
        )
        monkeypatch.setattr(
            quota_service,
            "_global_quota_row",
            lambda session: global_quota,
        )

    return _set


def test_get_effective_quota_defaults(stub_quota) -> None:
    stub_quota()
    assert quota_service.get_effective_quota(None, USER_ID) == DEFAULT_QUOTA


def test_get_effective_quota_uses_global_when_no_override(stub_quota) -> None:
    stub_quota(global_quota=_quota_row(max_cpu_cores=16, max_instances=20))
    quota = quota_service.get_effective_quota(None, USER_ID)
    assert quota.max_cpu_cores == 16
    assert quota.max_instances == 20


def test_get_effective_quota_user_override_beats_global(stub_quota) -> None:
    stub_quota(
        user_quota=_quota_row(max_cpu_cores=2),
        global_quota=_quota_row(max_cpu_cores=16),
    )
    assert quota_service.get_effective_quota(None, USER_ID).max_cpu_cores == 2


def test_effective_quota_lookup_never_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """check_quota 走在 provisioning 熱路徑上：查詢配額不得碰觸交易。

    若這裡 lazy-create 並 commit，會把呼叫端尚未完成的變更一起提交。
    """
    monkeypatch.setattr(quota_service, "_quota_for_user", lambda session, user_id: None)
    session = _FakeSession(existing=None)

    quota = quota_service.get_effective_quota(session, USER_ID)  # type: ignore[arg-type]

    assert quota == DEFAULT_QUOTA
    assert session.added == []
    assert session.commits == 0


def test_get_global_quota_creates_row_when_missing() -> None:
    session = _FakeSession(existing=None)

    config = quota_service.get_global_quota(session)  # type: ignore[arg-type]

    assert isinstance(config, QuotaConfig)
    assert config.id == 1
    assert config.max_cpu_cores == DEFAULT_QUOTA.max_cpu_cores
    assert session.added == [config]
    assert session.commits == 1


def test_get_global_quota_returns_existing_row_without_writing() -> None:
    existing = QuotaConfig(id=1, max_cpu_cores=32)
    session = _FakeSession(existing=existing)

    assert quota_service.get_global_quota(session) is existing  # type: ignore[arg-type]
    assert session.added == []
    assert session.commits == 0


def test_update_global_quota_applies_only_given_fields() -> None:
    existing = QuotaConfig(id=1, max_cpu_cores=8, max_memory_mb=16384)
    session = _FakeSession(existing=existing)
    before = existing.updated_at

    updated = quota_service.update_global_quota(
        session,  # type: ignore[arg-type]
        {"max_cpu_cores": 32},
    )

    assert updated.max_cpu_cores == 32
    assert updated.max_memory_mb == 16384
    assert updated.updated_at > before


def test_update_global_quota_ignores_unknown_and_none_values() -> None:
    existing = QuotaConfig(id=1, max_cpu_cores=8)
    session = _FakeSession(existing=existing)

    updated = quota_service.update_global_quota(
        session,  # type: ignore[arg-type]
        {"max_cpu_cores": None, "bogus_field": 999},
    )

    assert updated.max_cpu_cores == 8
    assert not hasattr(updated, "bogus_field")


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


def test_check_quota_raises_conflict(
    monkeypatch: pytest.MonkeyPatch, stub_quota
) -> None:
    stub_quota(user_quota=None)
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
    monkeypatch: pytest.MonkeyPatch, stub_quota
) -> None:
    stub_quota()

    def _boom(session, user_id, cluster_resources=None):
        raise RuntimeError("PVE down")

    monkeypatch.setattr(quota_service, "get_usage", _boom)
    quota_service.check_quota(None, USER_ID, delta_cores=100)  # 不 raise
