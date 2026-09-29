"""IP 快取寫入失敗不能把 session 拖進無效交易。

回歸背景：get_topology 在迴圈裡順手更新每台 VM 的 IP 快取，某一台的 flush 因 DB 連線
中斷失敗後被吞掉，session 卻停在 failed transaction，最後 _enrich_edges_from_db 用同一
個 session 查 NAT 規則時炸出 PendingRollbackError，整張拓撲 500。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.repositories import resource as resource_repo


class _Session:
    """記錄 rollback 次數的假 session（不需要真 DB）。"""

    def __init__(self) -> None:
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1


def _db_down(*_: Any, **__: Any) -> None:
    raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))


# ─── sync_ip_cache ───────────────────────────────────────────────────────────


def test_sync_ip_cache_rolls_back_when_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resource_repo, "update_ip_address", _db_down)
    session = _Session()

    ip = resource_repo.sync_ip_cache(session=session, vmid=150, live_ip="10.0.0.5")  # type: ignore[arg-type]

    assert ip == "10.0.0.5"  # 即時 IP 照樣回傳，快取寫不進去不影響結果
    assert session.rollbacks == 1


def test_sync_ip_cache_rolls_back_when_cache_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resource_repo, "get_cached_ip_address", _db_down)
    session = _Session()

    ip = resource_repo.sync_ip_cache(session=session, vmid=150, live_ip=None)  # type: ignore[arg-type]

    assert ip is None
    assert session.rollbacks == 1


def test_sync_ip_cache_falls_back_to_cache_when_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resource_repo, "get_cached_ip_address", lambda *, session, vmid: "10.0.0.9"
    )
    monkeypatch.setattr(resource_repo, "update_ip_address", _db_down)
    session = _Session()

    ip = resource_repo.sync_ip_cache(session=session, vmid=150, live_ip=None)  # type: ignore[arg-type]

    assert ip == "10.0.0.9"
    assert session.rollbacks == 0


def test_sync_ip_cache_falls_back_to_allocation_when_never_observed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """關機且從未被觀測過的機器：快取空、但 ip_allocation 有佈建時分配的 IP。"""
    monkeypatch.setattr(
        resource_repo, "get_cached_ip_address", lambda *, session, vmid: None
    )
    monkeypatch.setattr(
        resource_repo, "get_allocated_ip_address", lambda *, session, vmid: "10.0.0.20"
    )
    session = _Session()

    ip = resource_repo.sync_ip_cache(session=session, vmid=150, live_ip=None)  # type: ignore[arg-type]

    assert ip == "10.0.0.20"
    assert session.rollbacks == 0


def test_sync_ip_cache_prefers_observed_cache_over_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """快取是實際觀測到的位址，優先於分配紀錄。"""
    monkeypatch.setattr(
        resource_repo, "get_cached_ip_address", lambda *, session, vmid: "10.0.0.9"
    )
    monkeypatch.setattr(
        resource_repo, "get_allocated_ip_address", lambda *, session, vmid: "10.0.0.20"
    )
    session = _Session()

    ip = resource_repo.sync_ip_cache(session=session, vmid=150, live_ip=None)  # type: ignore[arg-type]

    assert ip == "10.0.0.9"


def test_sync_ip_cache_rolls_back_when_allocation_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resource_repo, "get_cached_ip_address", lambda *, session, vmid: None
    )
    monkeypatch.setattr(resource_repo, "get_allocated_ip_address", _db_down)
    session = _Session()

    ip = resource_repo.sync_ip_cache(session=session, vmid=150, live_ip=None)  # type: ignore[arg-type]

    assert ip is None
    assert session.rollbacks == 1


def test_get_allocated_ip_address_ignores_sessions_without_exec() -> None:
    """拓撲測試用的假 session 沒有 exec，不能炸。"""
    assert resource_repo.get_allocated_ip_address(session=_Session(), vmid=150) is None  # type: ignore[arg-type]


# ─── sync_ip_cache_many（清單頁、拓撲）─────────────────────────────────────


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _BatchSession(_Session):
    """依序回傳：快取 IP 查詢、分配紀錄查詢的結果。"""

    def __init__(self, cached: list[Any], allocated: list[Any]) -> None:
        super().__init__()
        self._results = [cached, allocated]

    def exec(self, _statement: Any) -> _Rows:
        return _Rows(self._results.pop(0))

    def get_bind(self) -> object:
        return object()


def test_batch_prefers_live_then_cache_then_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted: list[dict[int, str]] = []
    monkeypatch.setattr(
        resource_repo, "_persist_ip_cache", lambda session, changes: persisted.append(changes)
    )
    session = _BatchSession(
        cached=[(150, "10.0.0.50"), (151, "10.0.0.51")],
        allocated=[(152, "10.0.0.52")],
    )

    ips = resource_repo.sync_ip_cache_many(
        session=session,  # type: ignore[arg-type]
        live_ips={150: "10.0.0.50", 151: None, 152: None, 153: "10.0.0.53"},
    )

    assert ips == {150: "10.0.0.50", 151: "10.0.0.51", 152: "10.0.0.52", 153: "10.0.0.53"}
    # 只有跟快取不同的即時 IP 才寫回
    assert persisted == [{153: "10.0.0.53"}]


def test_batch_write_failure_never_touches_the_request_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """寫回走獨立交易：失敗只記 log，請求本身的 session 不會進入無效交易。

    回歸背景：以前在請求 session 裡 flush，一台失敗就讓後面查 NAT 規則時
    PendingRollbackError，整張拓撲 500；而且請求 session 從不 commit，
    寫進去的快取本來就會被丟掉，UPDATE 的列鎖還會持有到請求結束。
    """

    class _BrokenWriteSession:
        def __init__(self, _bind: Any) -> None:
            pass

        def __enter__(self) -> Any:
            raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))

        def __exit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(resource_repo, "Session", _BrokenWriteSession)
    session = _BatchSession(cached=[], allocated=[])

    ips = resource_repo.sync_ip_cache_many(
        session=session,  # type: ignore[arg-type]
        live_ips={150: "10.0.0.50"},
    )

    assert ips == {150: "10.0.0.50"}
    assert session.rollbacks == 0


def test_batch_read_failure_rolls_back_and_still_returns_live_ips() -> None:
    session = _Session()  # 沒有 exec：模擬讀取失敗

    ips = resource_repo.sync_ip_cache_many(
        session=session,  # type: ignore[arg-type]
        live_ips={150: "10.0.0.50", 151: None},
    )

    assert ips == {150: "10.0.0.50", 151: None}
    assert session.rollbacks == 1
