"""協作實驗室 Pair Mode（E5）：owner 邀請同群組成員共同操作一台 VM。

Session 記錄存 in-memory（與 VncSessionManager 一致）；底層 VNC session
結束（含上游斷線）時由 on_session_end 回呼清掉 pair 記錄。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.core.permissions import is_admin
from app.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.models import Group, GroupMember, Resource, User
from app.services.classroom.vnc_session_manager import (
    ClassroomSession,
    SessionMode,
    vnc_session_manager,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PairSession:
    id: str
    vmid: int
    owner_id: uuid.UUID
    invitee_id: uuid.UUID
    created_at: datetime


_sessions: dict[str, PairSession] = {}


def _share_group(session: Session, a: uuid.UUID, b: uuid.UUID) -> bool:
    """兩人是否同屬任一群組（皆為成員，或一方為群組 owner 另一方為成員）。"""
    a_groups = set(
        session.exec(
            select(GroupMember.group_id).where(GroupMember.user_id == a)
        ).all()
    ) | set(session.exec(select(Group.id).where(Group.owner_id == a)).all())
    b_groups = set(
        session.exec(
            select(GroupMember.group_id).where(GroupMember.user_id == b)
        ).all()
    ) | set(session.exec(select(Group.id).where(Group.owner_id == b)).all())
    return bool(a_groups & b_groups)


def _get_owned_resource(session: Session, user: User, vmid: int) -> Resource:
    resource = session.get(Resource, vmid)
    if resource is None:
        raise NotFoundError(f"Resource {vmid} not found")
    if resource.user_id != user.id:
        raise PermissionDeniedError("只有 VM 擁有者可以發起協作")
    return resource


def _get_active_user(session: Session, user_id: uuid.UUID) -> User:
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise NotFoundError("受邀使用者不存在或已停用")
    return user


async def create_pair(
    session: Session, user: User, *, vmid: int, invitee_user_id: uuid.UUID
) -> PairSession:
    if invitee_user_id == user.id:
        raise BadRequestError("不能邀請自己")
    _get_owned_resource(session, user, vmid)
    _get_active_user(session, invitee_user_id)
    if not _share_group(session, user.id, invitee_user_id):
        raise PermissionDeniedError("只能邀請同群組的成員")
    if any(p.vmid == vmid for p in _sessions.values()):
        raise ConflictError(f"VM {vmid} 已有進行中的協作 session")

    live = await vnc_session_manager.start_session(
        vmid=vmid, mode=SessionMode.pair, group_id=None, started_by=user.id
    )
    pair = PairSession(
        id=live.id,
        vmid=vmid,
        owner_id=user.id,
        invitee_id=invitee_user_id,
        created_at=datetime.now(timezone.utc),
    )
    _sessions[pair.id] = pair
    logger.info(
        "Pair session %s started (vmid=%s owner=%s invitee=%s)",
        pair.id, vmid, user.id, invitee_user_id,
    )
    return pair


def list_mine(user: User) -> list[PairSession]:
    return [
        p
        for p in _sessions.values()
        if p.owner_id == user.id or p.invitee_id == user.id
    ]


def get_pair(session_id: str) -> PairSession | None:
    return _sessions.get(session_id)


def is_participant(session_id: str, user_id: uuid.UUID) -> bool:
    pair = _sessions.get(session_id)
    return pair is not None and user_id in (pair.owner_id, pair.invitee_id)


async def end_pair(user: User, session_id: str) -> None:
    pair = _sessions.get(session_id)
    if pair is None:
        raise NotFoundError("協作 session 不存在")
    if pair.owner_id != user.id and not is_admin(user):
        raise PermissionDeniedError("只有發起者或管理員可以結束協作")
    await vnc_session_manager.stop_session(session_id)
    _sessions.pop(session_id, None)


async def _on_session_end(snapshot: ClassroomSession, _reason: str) -> None:
    """底層 VNC session 結束（含上游斷線）時清掉 pair 記錄。"""
    _sessions.pop(snapshot.id, None)


vnc_session_manager.on_session_end(_on_session_end)
