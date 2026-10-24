"""Administrator-managed user groups (a school class, a lab, …).

Admins create groups and manage members; teachers only list groups and import
one into a teaching class (``teaching_classes.import_group_students``). Soft
deleted accounts never count as members.
"""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select
from sqlmodel.sql.expression import Select

from app.core.i18n import t
from app.exceptions import ConflictError, NotFoundError
from app.models import AuditAction, User, UserGroup, UserGroupMember, UserRole
from app.schemas.user_group import (
    UserGroupIn,
    UserGroupMemberPublic,
    UserGroupMembersPublic,
    UserGroupMembersResult,
    UserGroupPublic,
    UserGroupsPublic,
)
from app.services.user import audit_service


def _live_members_statement(
    group_ids: list[uuid.UUID],
) -> Select[uuid.UUID, User]:
    return (
        select(UserGroupMember.group_id, User)
        .join(User, col(User.id) == col(UserGroupMember.user_id))
        .where(
            col(UserGroupMember.group_id).in_(group_ids),
            col(User.deleted_at).is_(None),
        )
    )


def _to_public(
    group: UserGroup, member_count: int = 0, student_count: int = 0
) -> UserGroupPublic:
    return UserGroupPublic(
        id=group.id,
        name=group.name,
        member_count=member_count,
        student_count=student_count,
        created_at=group.created_at,
    )


def _serialize(session: Session, group: UserGroup) -> UserGroupPublic:
    users = [user for _gid, user in session.exec(_live_members_statement([group.id]))]
    return _to_public(
        group,
        member_count=len(users),
        student_count=sum(1 for user in users if user.role == UserRole.student),
    )


def get_group(*, session: Session, group_id: uuid.UUID) -> UserGroup:
    group = session.get(UserGroup, group_id)
    if group is None:
        raise NotFoundError(t("userGroup.notFound"))
    return group


def list_groups(*, session: Session) -> UserGroupsPublic:
    groups = list(session.exec(select(UserGroup).order_by(col(UserGroup.name))))
    rows = session.exec(
        select(
            UserGroupMember.group_id,
            func.count(),
            func.count().filter(col(User.role) == UserRole.student),
        )
        .join(User, col(User.id) == col(UserGroupMember.user_id))
        .where(col(User.deleted_at).is_(None))
        .group_by(col(UserGroupMember.group_id))
    ).all()
    counts = {group_id: (total, students) for group_id, total, students in rows}
    data = [_to_public(group, *counts.get(group.id, (0, 0))) for group in groups]
    return UserGroupsPublic(data=data, count=len(data))


def _ensure_name_free(
    session: Session, name: str, *, exclude_id: uuid.UUID | None = None
) -> None:
    statement = select(UserGroup).where(func.lower(UserGroup.name) == name.lower())
    if exclude_id is not None:
        statement = statement.where(col(UserGroup.id) != exclude_id)
    if session.exec(statement).first() is not None:
        raise ConflictError(t("userGroup.nameExists", name=name))


def _save_name(session: Session, name: str, *, commit: bool = False) -> None:
    """同名檢查與寫入之間仍可能被另一個請求搶先（兩邊都通過預查）：唯一索引擋下時回 409。

    新名稱要在寫稽核紀錄之前先 flush：寫紀錄會觸發 autoflush，在那裡撞到索引就成了 500。
    """
    try:
        if commit:
            session.commit()
        else:
            session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(t("userGroup.nameExists", name=name)) from exc


def create_group(
    *, session: Session, body: UserGroupIn, actor_id: uuid.UUID
) -> UserGroupPublic:
    _ensure_name_free(session, body.name)
    group = UserGroup(name=body.name)
    session.add(group)
    _save_name(session, body.name)
    audit_service.log_action(
        session=session,
        user_id=actor_id,
        action=AuditAction.user_group_create,
        details=f"Created user group {body.name}",
        commit=False,
    )
    _save_name(session, body.name, commit=True)
    session.refresh(group)
    return _to_public(group)


def rename_group(
    *, session: Session, group_id: uuid.UUID, body: UserGroupIn, actor_id: uuid.UUID
) -> UserGroupPublic:
    group = get_group(session=session, group_id=group_id)
    if group.name != body.name:
        _ensure_name_free(session, body.name, exclude_id=group.id)
        old_name = group.name
        group.name = body.name
        session.add(group)
        _save_name(session, body.name)
        audit_service.log_action(
            session=session,
            user_id=actor_id,
            action=AuditAction.user_group_update,
            details=f"Renamed user group {old_name} to {body.name}",
            commit=False,
        )
        _save_name(session, body.name, commit=True)
        session.refresh(group)
    return _serialize(session, group)


def delete_group(*, session: Session, group_id: uuid.UUID, actor_id: uuid.UUID) -> None:
    """刪群組只拿掉群組與成員對應；已從它匯入的班級名單不受影響。"""
    group = get_group(session=session, group_id=group_id)
    name = group.name
    for member in session.exec(
        select(UserGroupMember).where(col(UserGroupMember.group_id) == group.id)
    ):
        session.delete(member)
    session.delete(group)
    audit_service.log_action(
        session=session,
        user_id=actor_id,
        action=AuditAction.user_group_delete,
        details=f"Deleted user group {name}",
        commit=False,
    )
    session.commit()


def get_member_users(*, session: Session, group_id: uuid.UUID) -> list[User]:
    get_group(session=session, group_id=group_id)
    statement = _live_members_statement([group_id]).order_by(col(User.email))
    return [user for _gid, user in session.exec(statement)]


def list_members(*, session: Session, group_id: uuid.UUID) -> UserGroupMembersPublic:
    users = get_member_users(session=session, group_id=group_id)
    data = [
        UserGroupMemberPublic(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
        )
        for user in users
    ]
    return UserGroupMembersPublic(data=data, count=len(data))


def _existing_member_ids(session: Session, group_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        session.exec(
            select(UserGroupMember.user_id).where(
                col(UserGroupMember.group_id) == group_id
            )
        )
    )


def add_members(
    *,
    session: Session,
    group_id: uuid.UUID,
    user_ids: list[uuid.UUID],
    emails: list[str],
    actor_id: uuid.UUID,
) -> UserGroupMembersResult:
    group = get_group(session=session, group_id=group_id)
    targets: dict[uuid.UUID, User] = {}
    not_found: list[str] = []

    wanted_ids = list(dict.fromkeys(user_ids))
    if wanted_ids:
        found = {
            user.id: user
            for user in session.exec(
                select(User).where(
                    col(User.id).in_(wanted_ids), col(User.deleted_at).is_(None)
                )
            )
        }
        for user_id in wanted_ids:
            if user_id in found:
                targets[user_id] = found[user_id]
            else:
                not_found.append(str(user_id))

    wanted_emails = list(
        dict.fromkeys(email.strip().lower() for email in emails if email.strip())
    )
    if wanted_emails:
        by_email = {
            user.email.lower(): user
            for user in session.exec(
                select(User).where(
                    func.lower(User.email).in_(wanted_emails),
                    col(User.deleted_at).is_(None),
                )
            )
        }
        for email in wanted_emails:
            if email in by_email:
                targets[by_email[email].id] = by_email[email]
            else:
                not_found.append(email)

    existing = _existing_member_ids(session, group.id)
    added = 0
    for user_id in targets:
        if user_id not in existing:
            session.add(UserGroupMember(group_id=group.id, user_id=user_id))
            added += 1
    if added:
        audit_service.log_action(
            session=session,
            user_id=actor_id,
            action=AuditAction.user_group_member_add,
            details=f"Added {added} member(s) to user group {group.name}",
            commit=False,
        )
    session.commit()
    return UserGroupMembersResult(
        added=added, not_found=not_found, group=_serialize(session, group)
    )


def remove_members(
    *,
    session: Session,
    group_id: uuid.UUID,
    user_ids: list[uuid.UUID],
    actor_id: uuid.UUID,
) -> UserGroupMembersResult:
    group = get_group(session=session, group_id=group_id)
    wanted = set(user_ids)
    removed = 0
    if wanted:
        for member in session.exec(
            select(UserGroupMember).where(
                col(UserGroupMember.group_id) == group.id,
                col(UserGroupMember.user_id).in_(wanted),
            )
        ):
            session.delete(member)
            removed += 1
    if removed:
        audit_service.log_action(
            session=session,
            user_id=actor_id,
            action=AuditAction.user_group_member_remove,
            details=f"Removed {removed} member(s) from user group {group.name}",
            commit=False,
        )
    session.commit()
    return UserGroupMembersResult(removed=removed, group=_serialize(session, group))
