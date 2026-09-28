"""個人配額 CRUD 移進 quota_service 後的行為測試（不需 DB）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import ConflictError, NotFoundError
from app.models import ResourceQuota, User
from app.schemas import ResourceQuotaCreate, ResourceQuotaUpdate
from app.services.resource import quota_service

ACTOR_ID = uuid.uuid4()


class _FakeSession:
    def __init__(self, objects: dict[type, Any] | None = None) -> None:
        self.objects = objects or {}
        self.exec_rows: list[Any] = []
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.commits = 0

    def get(self, model: type, _key: Any) -> Any:
        return self.objects.get(model)

    def exec(self, _stmt: Any) -> Any:
        rows = list(self.exec_rows)
        return SimpleNamespace(all=lambda: rows, first=lambda: rows[0] if rows else None)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, _obj: Any) -> None:
        return None


@pytest.fixture
def audit_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        quota_service.audit_service,
        "log_action",
        lambda **kw: calls.append(kw),
    )
    return calls


def _quota(**overrides: Any) -> ResourceQuota:
    values: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "max_cpu_cores": 8,
        "max_memory_mb": 16384,
        "max_disk_gb": 100,
        "max_instances": 5,
    }
    values.update(overrides)
    return ResourceQuota(**values)


def test_create_user_quota_missing_user_is_404(
    audit_calls: list[dict[str, Any]],
) -> None:
    session = _FakeSession()
    with pytest.raises(NotFoundError):
        quota_service.create_user_quota(
            session,  # type: ignore[arg-type]
            ResourceQuotaCreate(user_id=uuid.uuid4()),
            actor_id=ACTOR_ID,
        )
    assert session.commits == 0
    assert audit_calls == []


def test_create_user_quota_duplicate_is_409(
    audit_calls: list[dict[str, Any]],
) -> None:
    user_id = uuid.uuid4()
    session = _FakeSession({User: SimpleNamespace(id=user_id)})
    session.exec_rows = [_quota(user_id=user_id)]
    with pytest.raises(ConflictError):
        quota_service.create_user_quota(
            session,  # type: ignore[arg-type]
            ResourceQuotaCreate(user_id=user_id),
            actor_id=ACTOR_ID,
        )
    assert session.commits == 0


def test_create_user_quota_audits_in_same_commit(
    audit_calls: list[dict[str, Any]],
) -> None:
    user_id = uuid.uuid4()
    session = _FakeSession({User: SimpleNamespace(id=user_id)})
    quota = quota_service.create_user_quota(
        session,  # type: ignore[arg-type]
        ResourceQuotaCreate(user_id=user_id, max_cpu_cores=4),
        actor_id=ACTOR_ID,
    )
    assert quota.user_id == user_id
    assert quota.max_cpu_cores == 4
    assert session.added == [quota]
    assert session.commits == 1
    assert audit_calls[0]["commit"] is False
    assert audit_calls[0]["user_id"] == ACTOR_ID


def test_update_user_quota_ignores_explicit_null(
    audit_calls: list[dict[str, Any]],
) -> None:
    quota = _quota()
    session = _FakeSession({ResourceQuota: quota})
    body = ResourceQuotaUpdate.model_validate(
        {"max_cpu_cores": None, "max_instances": 3}
    )
    quota_service.update_user_quota(
        session,  # type: ignore[arg-type]
        quota.id,
        body,
        actor_id=ACTOR_ID,
    )
    assert quota.max_cpu_cores == 8
    assert quota.max_instances == 3
    assert session.commits == 1
    assert audit_calls[0]["commit"] is False


@pytest.mark.parametrize("op", ["update", "delete"])
def test_missing_user_quota_is_404(op: str, audit_calls: list[dict[str, Any]]) -> None:
    session = _FakeSession()
    with pytest.raises(NotFoundError):
        if op == "update":
            quota_service.update_user_quota(
                session,  # type: ignore[arg-type]
                uuid.uuid4(),
                ResourceQuotaUpdate(),
                actor_id=ACTOR_ID,
            )
        else:
            quota_service.delete_user_quota(
                session,  # type: ignore[arg-type]
                uuid.uuid4(),
                actor_id=ACTOR_ID,
            )
    assert audit_calls == []


def test_delete_user_quota_audits_in_same_commit(
    audit_calls: list[dict[str, Any]],
) -> None:
    quota = _quota()
    session = _FakeSession({ResourceQuota: quota})
    quota_service.delete_user_quota(
        session,  # type: ignore[arg-type]
        quota.id,
        actor_id=ACTOR_ID,
    )
    assert session.deleted == [quota]
    assert session.commits == 1
    assert audit_calls[0]["commit"] is False
