"""Coverage tests for app.services.resource.reset_service."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.resource import reset_service as svc

USER = SimpleNamespace(id=uuid.uuid4(), email="t@campus.edu")
QEMU_INFO = {"node": "pve1", "type": "qemu"}


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(
        self, rows: list[Any] | None = None, dialect: str | None = None
    ) -> None:
        self.rows = rows or []
        self.rollbacks = 0
        self.executed: list[tuple[Any, Any]] = []
        if dialect is not None:
            self.get_bind = lambda: SimpleNamespace(
                dialect=SimpleNamespace(name=dialect)
            )

    def exec(self, _stmt: Any) -> _Rows:
        return _Rows(self.rows)

    def execute(self, stmt: Any, params: Any = None) -> None:
        self.executed.append((stmt, params))

    def rollback(self) -> None:
        self.rollbacks += 1

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


@pytest.fixture()
def pve(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {
        "snapshots": [],
        "created": [],
        "control": [],
        "rollback": [],
        "status": "running",
        "supported": True,
        "find_errors": 0,
        "audits": [],
    }

    def _find(vmid: int) -> dict[str, Any]:
        if calls["find_errors"]:
            calls["find_errors"] -= 1
            raise RuntimeError("lock timeout")
        return {"vmid": vmid, "node": "pve1", "type": "lxc"}

    ps = svc.proxmox_service
    monkeypatch.setattr(ps, "find_resource", _find)
    monkeypatch.setattr(
        ps, "has_snapshot_feature", lambda _n, _v, _t: calls["supported"]
    )
    monkeypatch.setattr(ps, "list_snapshots", lambda _n, _v, _t: calls["snapshots"])
    monkeypatch.setattr(
        ps,
        "create_snapshot",
        lambda _n, _v, _t, wait_timeout_seconds=None, **p: calls["created"].append(
            p["snapname"]
        ),
    )

    def _status(_n: str, _v: int, _t: str) -> dict[str, Any]:
        if isinstance(calls["status"], Exception):
            raise calls["status"]
        return {"status": calls["status"]}

    monkeypatch.setattr(ps, "get_status", _status)
    monkeypatch.setattr(
        ps, "control", lambda _n, _v, _t, action: calls["control"].append(action)
    )
    monkeypatch.setattr(
        ps,
        "rollback_snapshot",
        lambda _n, _v, _t, snap: calls["rollback"].append(snap),
    )
    monkeypatch.setattr(
        svc.audit_service, "log_action", lambda **kw: calls["audits"].append(kw)
    )
    monkeypatch.setattr(svc.time, "sleep", lambda _s: None)
    return calls


# ── ensure_init_snapshot ───────────────────────────────────────────────────


def test_ensure_init_snapshot_already_present(pve: dict[str, Any]) -> None:
    pve["snapshots"] = [{"name": svc.INIT_SNAPSHOT_NAME}]
    assert svc.ensure_init_snapshot(150) is True
    assert pve["created"] == []


# ── create_init_snapshot ───────────────────────────────────────────────────


def test_create_init_snapshot_creates_and_audits(
    pve: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(svc, "require_snapshot_available", lambda *_a: None)
    out = svc.create_init_snapshot(
        _FakeSession(),  # type: ignore[arg-type]
        vmid=150,
        resource_info=QEMU_INFO,
        user=USER,
    )
    assert out["snapname"] == svc.INIT_SNAPSHOT_NAME
    assert pve["created"] == [svc.INIT_SNAPSHOT_NAME]
    assert pve["audits"][0]["action"] == "snapshot_create"


# ── _audit_reset ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("ok", [True, False])
def test_audit_reset_writes_with_own_session(
    pve: dict[str, Any], monkeypatch: pytest.MonkeyPatch, ok: bool
) -> None:
    monkeypatch.setattr(svc, "Session", lambda _engine: _FakeSession())
    svc._audit_reset(150, USER.id, ok=ok, detail="done")
    assert pve["audits"][0]["action"] == "snapshot_rollback"
    assert pve["audits"][0]["details"] == "done"


def test_audit_reset_swallows_db_failure(
    pve: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_engine: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "Session", _boom)
    svc._audit_reset(150, USER.id, ok=True, detail="done")
    assert pve["audits"] == []


# ── _sync_lxc_platform_key_after_start ─────────────────────────────────────


def test_sync_after_start_lxc_repairs_key_and_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.resource import resource_service

    monkeypatch.setattr(svc, "Session", lambda _engine: _FakeSession())
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        resource_service,
        "ensure_lxc_platform_key",
        lambda **kw: calls.append(("key", kw)),
    )
    monkeypatch.setattr(
        resource_service,
        "ensure_lxc_login_password",
        lambda **kw: calls.append(("pw", kw)),
    )
    svc._sync_lxc_platform_key_after_start("pve1", 150, "lxc")
    assert [c[0] for c in calls] == ["key", "pw"]
    assert calls[1][1]["reapply_recorded"] is True


def test_sync_after_start_lxc_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.resource import resource_service

    monkeypatch.setattr(svc, "Session", lambda _engine: _FakeSession())

    def _boom(**_kw: Any) -> None:
        raise RuntimeError("guest unreachable")

    monkeypatch.setattr(resource_service, "ensure_lxc_platform_key", _boom)
    svc._sync_lxc_platform_key_after_start("pve1", 150, "lxc")


# ── _run_reset / run_reset_task ────────────────────────────────────────────


def test_run_reset_failure_audits_and_reraises(
    pve: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    pve["status"] = RuntimeError("pve gone")
    audits: list[tuple[bool, str]] = []
    monkeypatch.setattr(
        svc, "_audit_reset", lambda _v, _u, *, ok, detail: audits.append((ok, detail))
    )
    with pytest.raises(RuntimeError, match="pve gone"):
        svc._run_reset(150, "pve1", "qemu", USER.id)
    assert audits == [(False, "Reset failed: pve gone")]


# ── _lock_reset_enqueue / _has_active_reset ────────────────────────────────


def test_lock_reset_enqueue_dialects() -> None:
    no_bind = _FakeSession()
    svc._lock_reset_enqueue(no_bind, 150)  # type: ignore[arg-type]
    assert no_bind.executed == []

    none_bind = _FakeSession()
    none_bind.get_bind = lambda: None  # type: ignore[attr-defined]
    svc._lock_reset_enqueue(none_bind, 150)  # type: ignore[arg-type]
    assert none_bind.executed == []

    sqlite = _FakeSession(dialect="sqlite")
    svc._lock_reset_enqueue(sqlite, 150)  # type: ignore[arg-type]
    assert sqlite.executed == []

    pg = _FakeSession(dialect="postgresql")
    svc._lock_reset_enqueue(pg, 150)  # type: ignore[arg-type]
    assert pg.executed[0][1] == {"ns": svc._RESET_LOCK_NAMESPACE, "vmid": 150}


# ── start_reset ────────────────────────────────────────────────────────────
