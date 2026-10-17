"""snapshot_cleanup_service（快照自動清理掃描）的分支覆蓋測試。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.repositories import governance as governance_repo
from app.services.governance import snapshot_cleanup_service as svc

NOW = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)


def _ts(days_ago: int) -> int:
    return int((NOW - timedelta(days=days_ago)).timestamp())


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows: list[Any] | None = None, *, fail: bool = False) -> None:
        self.rows = rows or []
        self.fail = fail
        self.statements: list[Any] = []
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def exec(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        if self.fail:
            raise RuntimeError("query failed")
        return _Result(self.rows)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture(autouse=True)
def _restore_cursor() -> Any:
    saved = svc._cursor.vmid
    yield
    svc._cursor.vmid = saved


# ─── 小工具函式 ────────────────────────────────────────────────────────────


def test_utc_now_is_aware() -> None:
    assert svc._utc_now().tzinfo is not None


def test_get_config_reads_governance_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    monkeypatch.setattr(
        governance_repo, "get_governance_config", lambda session: sentinel
    )
    assert svc._get_config(object()) is sentinel  # type: ignore[arg-type]


def test_list_scan_batch_queries_after_cursor() -> None:
    rows = [SimpleNamespace(vmid=5)]
    session = _FakeSession(rows)
    assert svc._list_scan_batch(session, 3, 20) == rows  # type: ignore[arg-type]
    sql = str(session.statements[0])
    assert "vmid >" in sql
    assert "LIMIT" in sql


def test_closed_mining_snapshots_query_failure_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = _FakeSession(fail=True)
    assert svc._closed_mining_snapshots(session, 101) == {}  # type: ignore[arg-type]
    assert "evidence snapshots kept" in caplog.text


def test_closed_mining_snapshots_maps_closed_times() -> None:
    reviewed = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    detected_naive = datetime(2026, 5, 1, 0, 0)
    rows = [
        SimpleNamespace(snapshot_name=None, reviewed_at=reviewed, detected_at=None),
        SimpleNamespace(snapshot_name="mining-a", reviewed_at=None, detected_at=None),
        SimpleNamespace(
            snapshot_name="mining-b", reviewed_at=reviewed, detected_at=None
        ),
        SimpleNamespace(
            snapshot_name="mining-c", reviewed_at=None, detected_at=detected_naive
        ),
    ]
    result = svc._closed_mining_snapshots(_FakeSession(rows), 101)  # type: ignore[arg-type]
    assert result == {
        "mining-b": reviewed,
        "mining-c": detected_naive.replace(tzinfo=timezone.utc),
    }


# ─── _audit_and_notify ─────────────────────────────────────────────────────


def test_audit_and_notify_without_owner_email(monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[dict[str, Any]] = []
    mails: list[dict[str, Any]] = []
    monkeypatch.setattr(svc.audit_service, "log_action", lambda **kw: logged.append(kw))
    monkeypatch.setattr(svc, "send_email", lambda **kw: mails.append(kw))

    svc._audit_and_notify(object(), SimpleNamespace(vmid=1, user=None), "old", 7)  # type: ignore[arg-type]
    svc._audit_and_notify(  # type: ignore[arg-type]
        object(), SimpleNamespace(vmid=2, user=SimpleNamespace(email="")), "old", 7
    )

    assert len(logged) == 2
    assert mails == []


def test_audit_and_notify_swallows_email_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(svc.audit_service, "log_action", lambda **kw: None)

    def boom(**kw: Any) -> None:
        raise RuntimeError("smtp down")

    monkeypatch.setattr(svc, "send_email", boom)
    resource = SimpleNamespace(vmid=101, user=SimpleNamespace(email="s@campus.edu"))

    svc._audit_and_notify(object(), resource, "old", 7)  # type: ignore[arg-type]

    assert "Failed to send snapshot cleanup email" in caplog.text


# ─── process_snapshot_cleanup ──────────────────────────────────────────────


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "session": _FakeSession(),
        "batch": [],
        "pve_map": {},
        "snapshots": {},
        "deleted": [],
        "audits": [],
        "closed": {},
        "closed_calls": 0,
    }
    monkeypatch.setattr(svc, "Session", lambda _engine: state["session"])
    monkeypatch.setattr(svc, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        svc,
        "_get_config",
        lambda session: SimpleNamespace(
            snapshot_cleanup_enabled=True, snapshot_retention_days=7
        ),
    )
    monkeypatch.setattr(
        svc, "_list_scan_batch", lambda session, cursor, limit: state["batch"]
    )
    monkeypatch.setattr(
        svc.proxmox_service, "list_all_resources_by_vmid", lambda: state["pve_map"]
    )

    def list_snapshots(node: str, vmid: int, rtype: str) -> list[dict[str, Any]]:
        value = state["snapshots"][vmid]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(svc.proxmox_service, "list_snapshots", list_snapshots)
    monkeypatch.setattr(
        svc.proxmox_service,
        "delete_snapshot",
        lambda node, vmid, rtype, name: state["deleted"].append((vmid, rtype, name)),
    )

    def closed(session: Any, vmid: int) -> dict[str, datetime]:
        state["closed_calls"] += 1
        return state["closed"]

    monkeypatch.setattr(svc, "_closed_mining_snapshots", closed)
    monkeypatch.setattr(
        svc,
        "_audit_and_notify",
        lambda session, resource, name, days, *, evidence=False: state["audits"].append(
            (resource.vmid, name, days, evidence)
        ),
    )
    return state


def test_empty_batch_resets_cursor(harness: dict[str, Any]) -> None:
    svc._cursor.vmid = 500
    assert svc.process_snapshot_cleanup() == 0
    assert svc._cursor.vmid == 0


def test_loop_handles_missing_failing_and_eligible(harness: dict[str, Any]) -> None:
    harness["batch"] = [
        SimpleNamespace(vmid=101),  # PVE 上不存在 → 跳過
        SimpleNamespace(vmid=102),  # 列快照失敗 → rollback，繼續下一台
        SimpleNamespace(vmid=103),  # lxc，有一般與存證快照
    ]
    harness["pve_map"] = {
        102: {"node": "pve1", "type": "qemu"},
        103: {"node": "pve2", "type": "lxc"},
    }
    harness["snapshots"] = {
        102: RuntimeError("pve timeout"),
        103: [
            {"name": "old", "snaptime": _ts(10)},
            {"name": "fresh", "snaptime": _ts(1)},
            {"name": "skylab-init", "snaptime": _ts(30)},
            {"name": "mining-202605010000", "snaptime": _ts(60)},
            {"name": "mining-202606010000", "snaptime": _ts(40)},
        ],
    }
    harness["closed"] = {"mining-202605010000": NOW - timedelta(days=40)}

    assert svc.process_snapshot_cleanup() == 2

    assert svc._cursor.vmid == 103
    assert harness["deleted"] == [
        (103, "lxc", "old"),
        (103, "lxc", "mining-202605010000"),
    ]
    assert harness["audits"] == [
        (103, "old", 7, False),
        (103, "mining-202605010000", svc.MINING_EVIDENCE_RETENTION_DAYS, True),
    ]
    # 存證事件只查一次（同台多個 mining-* 快照共用）
    assert harness["closed_calls"] == 1
    assert harness["session"].rollbacks == 1
    assert harness["session"].commits == 2


def test_outer_failure_returns_zero(
    harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(session: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "_get_config", boom)
    assert svc.process_snapshot_cleanup() == 0
