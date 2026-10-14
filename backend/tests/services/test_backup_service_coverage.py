"""Coverage tests for edge paths in app.services.resource.backup_service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import ConflictError
from app.services.resource import backup_service as svc

USER = SimpleNamespace(id=uuid.uuid4(), email="s@campus.edu")
OWNER_TOKEN = 1_780_000_000


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.rollbacks = 0

    def exec(self, _stmt: Any) -> _Rows:
        return _Rows(self.rows)

    def rollback(self) -> None:
        self.rollbacks += 1

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def test_owner_token_treats_naive_datetime_as_utc() -> None:
    naive = datetime(2026, 1, 1, 0, 0, 0)
    aware = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert svc._owner_token(naive) == svc._owner_token(aware)


def test_require_storage_rejects_storage_that_refuses_backups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svc, "_backup_storage", lambda _node: "nfs")
    monkeypatch.setattr(
        svc.proxmox_service, "storage_accepts_backups", lambda _n, _s: False
    )
    with pytest.raises(ConflictError):
        svc._require_storage("pve1")


def test_owned_backups_skips_entries_without_volid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = svc.build_notes(OWNER_TOKEN, "before exam")
    monkeypatch.setattr(
        svc.proxmox_service,
        "list_backups",
        lambda _n, _s, _v: [
            {"volid": "", "notes": notes},
            {"volid": None, "notes": notes},
            {
                "volid": "nfs:backup/a.tar.zst",
                "notes": notes,
                "ctime": "x",
                "size": None,
            },
        ],
    )
    owned = svc._owned_backups("pve1", "nfs", 150, OWNER_TOKEN)
    assert [b["volid"] for b in owned] == ["nfs:backup/a.tar.zst"]
    assert owned[0]["created_at"] is None
    assert owned[0]["size"] is None
    assert owned[0]["description"] == "before exam"


def test_backup_mode_lxc_feature_check_failure_falls_back_to_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_n: str, _v: int, _t: str) -> bool:
        raise RuntimeError("pve down")

    monkeypatch.setattr(svc.proxmox_service, "has_snapshot_feature", _boom)
    assert svc._backup_mode("pve1", 150, "lxc") == "stop"


def test_has_active_machine_task_ignores_malformed_payloads() -> None:
    rows = [
        SimpleNamespace(payload="not json"),
        SimpleNamespace(payload={"vmid": "abc"}),
        SimpleNamespace(payload=None),
        SimpleNamespace(payload="[1, 2]"),
    ]
    assert svc._has_active_machine_task(_FakeSession(rows), 150) is False  # type: ignore[arg-type]
    rows.append(SimpleNamespace(payload='{"vmid": "150"}'))
    assert svc._has_active_machine_task(_FakeSession(rows), 150) is True  # type: ignore[arg-type]


def test_enqueue_exclusive_dedup_returns_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svc.audit_service, "log_action", lambda **_kw: None)
    monkeypatch.setattr(svc, "enqueue_task_sync", lambda **_kw: None)
    with pytest.raises(ConflictError):
        svc._enqueue_exclusive(
            _FakeSession(),  # type: ignore[arg-type]
            vmid=150,
            user=USER,
            task_type=svc.TASK_BACKUP,
            payload={"vmid": 150},
            action="backup_create",
            details="x",
        )


@pytest.mark.parametrize("ok", [True, False])
def test_audit_task_writes_in_own_session(
    monkeypatch: pytest.MonkeyPatch, ok: bool
) -> None:
    audits: list[dict[str, Any]] = []
    monkeypatch.setattr(svc, "Session", lambda _engine: _FakeSession())
    monkeypatch.setattr(svc.audit_service, "log_action", lambda **kw: audits.append(kw))
    svc._audit_task(150, USER.id, action="backup_create", ok=ok, detail="done")
    assert audits[0]["action"] == "backup_create"
    assert audits[0]["details"] == "done"
    assert audits[0]["user_id"] == USER.id


def test_audit_task_swallows_db_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_engine: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "Session", _boom)
    svc._audit_task(150, USER.id, action="backup_restore", ok=False, detail="x")


@pytest.mark.parametrize("rootfs", [None, 123, "noslash", ",size=8G"])
def test_lxc_restore_target_without_storage_prefix(
    monkeypatch: pytest.MonkeyPatch, rootfs: Any
) -> None:
    config: dict[str, Any] = {"unprivileged": 1}
    if rootfs is not None:
        config["rootfs"] = rootfs
    monkeypatch.setattr(svc.proxmox_service, "get_config", lambda _n, _v, _t: config)
    assert svc._lxc_restore_target("pve1", 150) == (None, True)


def test_restore_failure_restart_failure_is_logged_and_original_error_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audits: list[tuple[str, bool]] = []
    controls: list[str] = []
    monkeypatch.setattr(
        svc,
        "_audit_task",
        lambda _v, _u, *, action, ok, detail: audits.append((action, ok)),
    )
    monkeypatch.setattr(
        svc.proxmox_service, "get_status", lambda _n, _v, _t: {"status": "running"}
    )

    def _control(_n: str, _v: int, _t: str, action: str, **_kw: Any) -> None:
        controls.append(action)
        if action == "start":
            raise RuntimeError("disk gone")

    def _restore(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("restore failed")

    monkeypatch.setattr(svc.proxmox_service, "control", _control)
    monkeypatch.setattr(svc.proxmox_service, "restore_backup", _restore)

    with pytest.raises(RuntimeError, match="restore failed"):
        svc.run_restore_task(
            uuid.uuid4(),
            {
                "vmid": 150,
                "node": "pve1",
                "rtype": "qemu",
                "volid": "nfs:backup/a.vma.zst",
                "user_id": str(USER.id),
            },
        )
    assert controls == ["stop", "start"]
    assert audits == [("backup_restore", False)]
