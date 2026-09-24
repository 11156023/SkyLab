"""初始化精靈狀態 singleton 的 DB 存取。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlmodel import Session

from app.models import SystemSetup

SYSTEM_SETUP_ID = 1


def get_system_setup(*, session: Session) -> SystemSetup:
    """取得初始化狀態 singleton；不存在則以「尚未完成」建立。

    migration 已替既有部署插入 completed=True 的那一列，這裡的建立分支
    只會在全新資料庫或有人手動刪掉那列時走到。
    """
    state = session.get(SystemSetup, SYSTEM_SETUP_ID)
    if state is None:
        state = SystemSetup(id=SYSTEM_SETUP_ID)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def set_admin_user(*, session: Session, user_id: uuid.UUID) -> SystemSetup:
    state = get_system_setup(session=session)
    state.admin_user_id = user_id
    state.updated_at = datetime.now(timezone.utc)
    session.add(state)
    session.commit()
    session.refresh(state)
    return state


def mark_completed(*, session: Session) -> SystemSetup:
    state = get_system_setup(session=session)
    now = datetime.now(timezone.utc)
    state.completed = True
    state.completed_at = now
    state.updated_at = now
    session.add(state)
    session.commit()
    session.refresh(state)
    return state
