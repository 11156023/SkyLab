"""存過防火牆拓樸版面的使用者要刪得掉，版面列一併清除。"""

import uuid
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.core.config import settings
from app.models import FirewallLayout, User, UserRole
from app.repositories import user as user_repo
from app.schemas import UserCreate
from app.services.user import user_service


def _student_with_layout(db: Session) -> User:
    user = user_repo.create_user(
        session=db,
        user_create=UserCreate(
            email=f"b15-layout-{uuid.uuid4().hex[:10]}@example.com",
            password="strongpass123",
            role=UserRole.student,
        ),
    )
    db.commit()
    now = datetime.now(UTC)
    # Internet（gateway）節點沒有 vmid，不會被 resources 的 CASCADE 帶走；
    # VM 節點的 vmid 是 resources 的外鍵，不能再塞找不到 Resource 的節點
    db.add(
        FirewallLayout(
            user_id=user.id,
            vmid=None,
            node_type="gateway",
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _layouts(db: Session, user_id: uuid.UUID) -> list[FirewallLayout]:
    return list(
        db.exec(select(FirewallLayout).where(FirewallLayout.user_id == user_id)).all()
    )


def test_admin_can_delete_a_user_who_saved_a_firewall_layout(db: Session) -> None:
    user = _student_with_layout(db)
    user_id = user.id
    admin = user_repo.get_user_by_email(session=db, email=settings.FIRST_SUPERUSER)
    assert admin is not None

    user_service.delete_user(session=db, user_id=user_id, current_user=admin)

    db.expire_all()
    assert db.get(User, user_id) is None
    assert _layouts(db, user_id) == []


def test_user_can_delete_own_account_after_saving_a_firewall_layout(
    db: Session,
) -> None:
    user = _student_with_layout(db)
    user_id = user.id

    user_service.delete_me(session=db, current_user=user)

    db.expire_all()
    assert db.get(User, user_id) is None
    assert _layouts(db, user_id) == []
