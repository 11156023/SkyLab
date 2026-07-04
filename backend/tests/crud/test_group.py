"""group repository 單元測試（in-memory SQLite，不碰真實 DB）"""

from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.user import User
from app.repositories import group as group_repo


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _create_user(session: Session, email: str) -> User:
    user = User(email=email, hashed_password="x", full_name=email.split("@")[0])
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_delete_group_with_members() -> None:
    """刪除含成員的群組不應報錯，且成員關聯列須一併刪除（regression: 500）"""
    session = _session()
    teacher = _create_user(session, "teacher@example.com")
    student = _create_user(session, "student@example.com")

    group = group_repo.create_group(
        session=session, name="demo", description=None, owner_id=teacher.id
    )
    added, not_found = group_repo.add_members_by_emails(
        session=session, group_id=group.id, emails=[student.email]
    )
    assert len(added) == 1
    assert not_found == []

    group_repo.delete_group(session=session, group_id=group.id)

    assert group_repo.get_group_by_id(session=session, group_id=group.id) is None
    assert session.exec(select(GroupMember)).all() == []
    # 成員的 user 本身不受影響
    assert session.exec(select(User).where(User.id == student.id)).first() is not None


def test_delete_empty_group() -> None:
    session = _session()
    teacher = _create_user(session, "teacher@example.com")
    group = group_repo.create_group(
        session=session, name="empty", description=None, owner_id=teacher.id
    )

    group_repo.delete_group(session=session, group_id=group.id)

    assert group_repo.get_group_by_id(session=session, group_id=group.id) is None
    assert session.exec(select(Group)).all() == []
