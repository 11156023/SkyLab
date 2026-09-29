"""mining repository：未結案狀態只定義一份，未結案 vmid 單次查詢。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.models import MiningIncidentStatus
from app.repositories import mining as mining_repo


def test_open_statuses_are_detected_and_suspended() -> None:
    assert mining_repo.OPEN_STATUSES == (
        MiningIncidentStatus.detected,
        MiningIncidentStatus.suspended,
    )


def test_list_open_incident_vmids_drops_none_and_dedupes() -> None:
    captured: list[Any] = []

    class _Session:
        def exec(self, stmt: Any) -> Any:
            captured.append(stmt)
            return SimpleNamespace(all=lambda: [101, None, 205, 101])

    session: Any = _Session()
    assert mining_repo.list_open_incident_vmids(session=session) == {101, 205}
    assert len(captured) == 1
    sql = str(captured[0])
    assert "mining_incident" in sql.lower()
    assert " IN " in sql.upper()
