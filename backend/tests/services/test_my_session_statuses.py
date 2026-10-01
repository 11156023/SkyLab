"""GET /resources/my/session-status：一次算完本人所有執行中機器的關機警告。

原本前端每 30 秒對每台執行中機器各打一次 /{vmid}/session-status（每次 find_resource）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.resource import resource_service as svc


def _resource(vmid: int, *, auto_stop_in_min: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        vmid=vmid,
        auto_stop_at=(
            datetime.now(UTC) + timedelta(minutes=auto_stop_in_min)
            if auto_stop_in_min is not None
            else None
        ),
        auto_stop_reason="practice_quota" if auto_stop_in_min is not None else None,
        request_id=None,
        expiry_date=None,
        batch_job_id=None,
    )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, int] = {"listing": 0, "policy": 0, "released": 0}

    def install(resources: list[Any], pve: dict[int, dict[str, Any]]) -> dict[str, int]:
        monkeypatch.setattr(
            svc.resource_repo,
            "get_resources_by_user",
            lambda *, session, user_id: resources,
        )

        def listing() -> dict[int, dict[str, Any]]:
            # 排隊等叢集清單時不能還抱著 DB 連線
            assert calls["released"] == 1
            calls["listing"] += 1
            return pve

        def release(_session: Any) -> None:
            calls["released"] += 1

        def policy(*, session: Any) -> SimpleNamespace:
            calls["policy"] += 1
            return SimpleNamespace(practice_warning_minutes=10, expiry_warning_hours=24)

        monkeypatch.setattr(
            svc,
            "proxmox_service",
            SimpleNamespace(list_all_resources_by_vmid=listing),
        )
        monkeypatch.setattr(svc, "get_schedule_policy", policy)
        monkeypatch.setattr(svc, "end_read_transaction", release)
        return calls

    return install


def test_only_running_machines_are_reported_with_one_listing(env) -> None:
    calls = env(
        [_resource(100, auto_stop_in_min=5), _resource(101), _resource(102)],
        {100: {"status": "running"}, 101: {"status": "stopped"}},
    )

    statuses = svc.list_my_session_statuses(session=object(), user_id="u1")  # type: ignore[arg-type]

    assert [s.vmid for s in statuses] == [100]
    assert statuses[0].should_warn is True
    assert statuses[0].warn_reason == "auto_stop"
    assert calls == {"listing": 1, "policy": 1, "released": 1}


def test_user_without_machines_skips_pve(env) -> None:
    calls = env([], {})

    assert svc.list_my_session_statuses(session=object(), user_id="u1") == []  # type: ignore[arg-type]
    assert calls["listing"] == 0
