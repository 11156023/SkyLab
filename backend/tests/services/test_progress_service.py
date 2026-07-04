"""進度熱圖測試：activity 判定純函式 + 聚合。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.teaching import progress_service

STUDENT_ID = uuid.uuid4()


class TestClassifyActivity:
    def test_stopped(self) -> None:
        assert (
            progress_service.classify_activity(
                status="stopped", cpu_percent=0.0, uptime_seconds=0
            )
            == "stopped"
        )

    def test_stale_long_uptime_zero_cpu(self) -> None:
        assert (
            progress_service.classify_activity(
                status="running", cpu_percent=0.4, uptime_seconds=7200
            )
            == "stale"
        )

    def test_idle_low_cpu_short_uptime(self) -> None:
        assert (
            progress_service.classify_activity(
                status="running", cpu_percent=3.0, uptime_seconds=600
            )
            == "idle"
        )

    def test_running(self) -> None:
        assert (
            progress_service.classify_activity(
                status="running", cpu_percent=45.0, uptime_seconds=600
            )
            == "running"
        )


def test_get_heatmap_aggregates(monkeypatch: pytest.MonkeyPatch) -> None:
    group = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4())
    monkeypatch.setattr(
        progress_service, "_get_group_or_404", lambda session, group_id: group
    )
    monkeypatch.setattr(
        progress_service, "require_group_access", lambda user, owner_id: None
    )
    monkeypatch.setattr(
        progress_service.group_repo,
        "get_group_members",
        lambda **kwargs: [
            SimpleNamespace(id=STUDENT_ID, email="s@campus.edu", full_name="小明")
        ],
    )
    monkeypatch.setattr(
        progress_service,
        "_resources_for_users",
        lambda session, user_ids: [
            SimpleNamespace(vmid=101, user_id=STUDENT_ID)
        ],
    )
    cluster = [
        {
            "vmid": 101, "name": "stu-vm", "status": "running",
            "cpu": 0.42, "maxcpu": 2,
            "mem": 1024**3, "maxmem": 2 * 1024**3,
            "uptime": 7200,
        }
    ]
    entries = progress_service.get_heatmap(
        None, group_id=group.id, user=None, cluster_resources=cluster
    )
    assert len(entries) == 1
    entry = entries[0]
    assert entry.vmid == 101
    assert entry.owner_name == "小明"
    assert entry.cpu_percent == pytest.approx(42.0)
    assert entry.mem_percent == pytest.approx(50.0)
    assert entry.activity == "running"
