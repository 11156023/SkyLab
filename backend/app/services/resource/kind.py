"""機器來源（machine kind）判定：個人申請、共享、班級、快速練習、課程實驗。

Resource 表上沒有 source 欄位，四種來源要用組合推導：
- 班級機：teaching_class_id 有值（allocation_scope=teaching_class）
- 快速練習：request_id 出現在 QuickPracticeSessionMachine，或申請單 request_kind=quick_template
- 課程實驗：申請單 request_kind=course
- 其餘：個人申請
「共享給我」不是機器本身的性質，而是觀看者與機器的關係，由呼叫端在標示共享時覆寫。

班級機再分「我是這班的學生」（機器分給我）與「我是這班的老師」（機器是學生的，
我有管理權），前端據此把老師自己的機器與學生的機器分開標示。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any, Literal

from sqlmodel import Session, col, select

from app.models import User, VMRequest
from app.models.quick_practice import QuickPracticeSessionMachine

MachineKind = Literal["personal", "shared", "teaching_class", "quick_practice", "course"]
ClassRelation = Literal["student", "teacher"]


def classify(
    db_resource: Any,
    *,
    is_practice: bool = False,
    request_kind: str | None = None,
) -> MachineKind:
    """單台機器的來源；呼叫端自行提供快速練習與申請單種類的查詢結果。"""
    if db_resource is None:
        return "personal"
    if db_resource.teaching_class_id or db_resource.allocation_scope == "teaching_class":
        return "teaching_class"
    if is_practice or request_kind == "quick_template":
        return "quick_practice"
    if request_kind == "course":
        return "course"
    return "personal"


def class_relation_for(
    db_resource: Any, *, viewer_id: uuid.UUID, owned_class_ids: set[uuid.UUID]
) -> ClassRelation | None:
    """觀看者與這台班級機的關係；不是班級機回 None。"""
    if db_resource is None or not db_resource.teaching_class_id:
        return None
    if db_resource.teaching_class_id in owned_class_ids:
        return "teacher"
    if db_resource.user_id == viewer_id:
        return "student"
    return None


def practice_request_ids(session: Session) -> set[uuid.UUID]:
    return set(session.exec(select(QuickPracticeSessionMachine.vm_request_id)).all())


def request_kinds(
    session: Session, request_ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
    ids = [r for r in request_ids if r is not None]
    if not ids:
        return {}
    stmt = select(VMRequest.id, VMRequest.request_kind).where(col(VMRequest.id).in_(ids))
    return {rid: kind for rid, kind in session.exec(stmt).all() if kind}


def classify_many(session: Session, resources: list[Any]) -> dict[int, MachineKind]:
    """一次判定多台機器的來源（兩個批次查詢，不是逐台 N+1）。"""
    practice_ids = practice_request_ids(session)
    kinds = request_kinds(session, (r.request_id for r in resources))
    return {
        r.vmid: classify(
            r,
            is_practice=r.request_id in practice_ids,
            request_kind=kinds.get(r.request_id) if r.request_id else None,
        )
        for r in resources
    }


def user_display_names(
    session: Session, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """機器不是自己的時顯示擁有者：有姓名用姓名，沒有用信箱。"""
    ids = list({u for u in user_ids if u is not None})
    if not ids:
        return {}
    stmt = select(User.id, User.full_name, User.email).where(col(User.id).in_(ids))
    return {uid: (full_name or email) for uid, full_name, email in session.exec(stmt).all()}


__all__ = [
    "ClassRelation",
    "MachineKind",
    "class_relation_for",
    "classify",
    "classify_many",
    "practice_request_ids",
    "request_kinds",
    "user_display_names",
]
