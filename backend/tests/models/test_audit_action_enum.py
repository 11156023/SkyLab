"""AuditAction 與資料庫既有紀錄的相容性檢查。

``audit_logs.action`` 是 PostgreSQL enum，標籤加了就拿不掉；只要 Python 的
``AuditAction`` 少了任何一個資料表裡出現過的值，SQLAlchemy 讀到那筆時就會丟
``LookupError``，稽核清單與 CSV 匯出會整批失敗（2026-09-07 的
``group_member_remove`` 事故）。
"""

import importlib.util
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditAction, AuditLog
from app.repositories import audit_log as audit_repo
from app.services.user import audit_service

# 已下線功能留下的 action；共用資料庫的 audit_logs 仍有這些紀錄
RETIRED_ACTIONS = (
    "group_create",
    "group_delete",
    "group_member_add",
    "group_member_remove",
    "cloudflare_zone_activation_check",
)

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "alembic"
    / "versions"
    / "aud01_sync_auditaction_labels.py"
)


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _insert_raw(session: Session, action: str) -> uuid.UUID:
    """繞過 ORM 直接寫入字串，模擬舊版程式留下的紀錄。"""
    log_id = uuid.uuid4()
    session.execute(
        sa.insert(AuditLog.__table__).values(
            id=log_id,
            action=action,
            details=f"legacy {action}",
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    return log_id


@pytest.mark.parametrize("action", RETIRED_ACTIONS)
def test_retired_actions_are_still_enum_members(action: str) -> None:
    assert AuditAction(action).value == action


@pytest.mark.parametrize("action", RETIRED_ACTIONS)
def test_rows_with_retired_actions_are_readable(db: Session, action: str) -> None:
    log_id = _insert_raw(db, action)

    logs, count = audit_repo.get_audit_logs(session=db)
    assert count == 1
    assert logs[0].id == log_id
    assert logs[0].action is AuditAction(action)

    exported = audit_repo.iter_audit_logs_for_export(session=db)
    assert [log.id for log in exported] == [log_id]

    csv_text = audit_service.export_csv(session=db)
    assert action in csv_text


def test_unknown_label_still_breaks_reads(db: Session) -> None:
    """確認上面的測試真的經過 Enum 轉換：不在 AuditAction 裡的標籤讀取時會爆。"""
    _insert_raw(db, "label_that_never_existed")

    with pytest.raises(LookupError):
        audit_repo.iter_audit_logs_for_export(session=db)


def test_migration_only_adds_labels_known_to_the_model() -> None:
    spec = importlib.util.spec_from_file_location("aud01", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    known = {action.value for action in AuditAction}
    assert set(module.VALUES) <= known
    assert set(RETIRED_ACTIONS) <= set(module.VALUES)
