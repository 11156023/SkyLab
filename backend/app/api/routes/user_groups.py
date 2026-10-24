"""Administrator-managed user groups.

Teachers may list groups (to import one into a class); everything else is
admin only.
"""

import uuid

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.api.deps import AdminUser, InstructorUser, SessionDep
from app.api.deps.auth import get_current_active_superuser
from app.schemas import Message
from app.schemas.user_group import (
    UserGroupIn,
    UserGroupMembersAdd,
    UserGroupMembersPublic,
    UserGroupMembersRemove,
    UserGroupMembersResult,
    UserGroupPublic,
    UserGroupsPublic,
)
from app.services.user import roster_csv, user_group_service

router = APIRouter(prefix="/user-groups", tags=["user-groups"])


@router.get("/", response_model=UserGroupsPublic)
def read_user_groups(
    session: SessionDep, _current_user: InstructorUser
) -> UserGroupsPublic:
    return user_group_service.list_groups(session=session)


@router.post("/", response_model=UserGroupPublic)
def create_user_group(
    body: UserGroupIn, session: SessionDep, current_user: AdminUser
) -> UserGroupPublic:
    return user_group_service.create_group(
        session=session, body=body, actor_id=current_user.id
    )


@router.patch("/{group_id}", response_model=UserGroupPublic)
def rename_user_group(
    group_id: uuid.UUID, body: UserGroupIn, session: SessionDep, current_user: AdminUser
) -> UserGroupPublic:
    return user_group_service.rename_group(
        session=session, group_id=group_id, body=body, actor_id=current_user.id
    )


@router.delete("/{group_id}", response_model=Message)
def delete_user_group(
    group_id: uuid.UUID, session: SessionDep, current_user: AdminUser
) -> Message:
    user_group_service.delete_group(
        session=session, group_id=group_id, actor_id=current_user.id
    )
    return Message(message="ok")


@router.get(
    "/{group_id}/members",
    response_model=UserGroupMembersPublic,
    dependencies=[Depends(get_current_active_superuser)],
)
def read_user_group_members(
    group_id: uuid.UUID, session: SessionDep
) -> UserGroupMembersPublic:
    return user_group_service.list_members(session=session, group_id=group_id)


@router.post("/{group_id}/members", response_model=UserGroupMembersResult)
def add_user_group_members(
    group_id: uuid.UUID,
    body: UserGroupMembersAdd,
    session: SessionDep,
    current_user: AdminUser,
) -> UserGroupMembersResult:
    return user_group_service.add_members(
        session=session,
        group_id=group_id,
        user_ids=body.user_ids,
        emails=body.emails,
        actor_id=current_user.id,
    )


@router.post("/{group_id}/members/remove", response_model=UserGroupMembersResult)
def remove_user_group_members(
    group_id: uuid.UUID,
    body: UserGroupMembersRemove,
    session: SessionDep,
    current_user: AdminUser,
) -> UserGroupMembersResult:
    return user_group_service.remove_members(
        session=session,
        group_id=group_id,
        user_ids=body.user_ids,
        actor_id=current_user.id,
    )


@router.post("/{group_id}/members/import-csv", response_model=UserGroupMembersResult)
async def import_user_group_members(
    group_id: uuid.UUID,
    session: SessionDep,
    current_user: AdminUser,
    file: UploadFile = File(...),
) -> UserGroupMembersResult:
    # 檔案串流留在 event loop；查帳號、寫入的同步 DB 工作丟到 worker thread
    emails = await roster_csv.read_roster_upload(file, session)
    return await run_in_threadpool(
        add_user_group_members,
        group_id,
        UserGroupMembersAdd(emails=emails),
        session,
        current_user,
    )
