"""稽核儀表板的「登入失敗」要涵蓋所有登入方式。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from app.models import AuditAction, AuditLog
from app.repositories import audit_log as audit_repo


def test_login_failed_stat_counts_every_login_method(db: Session) -> None:
    # 用一段不會和其他測試重疊的時間窗，只算這裡寫進去的紀錄
    window_start = datetime(2099, 3, 1, tzinfo=UTC)
    actions = [
        AuditAction.login_failed,
        AuditAction.login_google_failed,
        AuditAction.login_ldap_failed,
        AuditAction.login_totp_failed,
        AuditAction.login_success,
    ]
    rows = [
        AuditLog(
            user_id=None,
            vmid=None,
            action=action.value,
            details="stats test",
            created_at=window_start + timedelta(minutes=index),
        )
        for index, action in enumerate(actions)
    ]
    for row in rows:
        db.add(row)
    db.commit()
    try:
        stats = audit_repo.get_audit_stats(
            session=db,
            start_time=window_start,
            end_time=window_start + timedelta(hours=1),
        )
        assert stats["total"] == len(actions)
        assert stats["login_failed"] == 4
    finally:
        for row in rows:
            db.delete(row)
        db.commit()
