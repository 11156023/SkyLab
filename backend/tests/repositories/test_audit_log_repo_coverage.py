"""app.repositories.audit_log 的篩選條件與匯出分頁覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditAction, AuditLog
from app.repositories import audit_log as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


BASE = datetime(2099, 1, 1, tzinfo=UTC)


def _seed(session: Session) -> tuple[uuid.UUID, list[AuditLog]]:
    user_id = uuid.uuid4()
    rows = [
        AuditLog(
            user_id=user_id if i % 2 == 0 else None,
            vmid=100 + (i % 2),
            resource_vmid=100 if i % 2 == 0 else None,
            action=(
                AuditAction.login_failed.value
                if i < 3
                else AuditAction.login_success.value
            ),
            details=f"detail-{i} {'needle' if i == 4 else ''}",
            ip_address=f"10.0.0.{i}",
            # 第 1、2 筆時間相同，驗證 keyset 的 (created_at, id) 平手處理
            created_at=BASE + timedelta(minutes=0 if i in (1, 2) else i),
        )
        for i in range(6)
    ]
    for row in rows:
        session.add(row)
    session.commit()
    return user_id, rows


def test_get_audit_logs_applies_every_filter(session: Session) -> None:
    user_id, _ = _seed(session)

    _, count = repo.get_audit_logs(session=session, vmid=100)
    assert count == 3
    _, count = repo.get_audit_logs(session=session, resource_vmid=100)
    assert count == 3
    _, count = repo.get_audit_logs(session=session, user_id=user_id)
    assert count == 3
    _, count = repo.get_audit_logs(session=session, action="login_failed")
    assert count == 3
    _, count = repo.get_audit_logs(
        session=session,
        actions=[AuditAction.login_failed, AuditAction.login_success],
    )
    assert count == 6
    _, count = repo.get_audit_logs(
        session=session,
        start_time=BASE + timedelta(minutes=3),
        end_time=BASE + timedelta(minutes=4),
    )
    assert count == 2
    _, count = repo.get_audit_logs(session=session, ip_address="10.0.0.5")
    assert count == 1
    rows, count = repo.get_audit_logs(session=session, search="needle")
    assert count == 1 and "needle" in rows[0].details


def test_stream_export_paginates_with_keyset(session: Session) -> None:
    _, seeded = _seed(session)

    exported = list(repo.stream_audit_logs_for_export(session=session, batch_size=2))
    assert len(exported) == len(seeded)
    assert len({row.id for row in exported}) == len(seeded)
    assert [r.created_at for r in exported] == sorted(
        (r.created_at for r in exported), reverse=True
    )

    # 剛好在批次邊界結束：最後一輪查到空集合
    full = list(
        repo.stream_audit_logs_for_export(
            session=session, action=AuditAction.login_failed, batch_size=3
        )
    )
    assert len(full) == 3

    capped = list(
        repo.stream_audit_logs_for_export(session=session, max_rows=3, batch_size=2)
    )
    assert len(capped) == 3
