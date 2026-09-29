"""Teacher-managed, versioned per-student course environments.

路由只做權限與參數；設定驗證、落地、序列化與發布都在
``app.services.course_environment.environment_service``，請求 schemas 在
``app.schemas.course_environment``。
"""

import uuid
from typing import Any

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import InstructorUser, SessionDep
from app.models import CourseEnvironment
from app.schemas.course_environment import (
    EnvironmentBasicsIn,
    EnvironmentCreate,
    EnvironmentDraftIn,
    EnvironmentUpdate,
)
from app.services.course_environment import environment_service

router = APIRouter(prefix="/course-environments", tags=["course-environments"])


@router.get("")
def list_environments(
    session: SessionDep, current_user: InstructorUser
) -> list[dict[str, Any]]:
    return environment_service.list_environments(session, current_user)


@router.get("/published")
def list_published_environments(
    session: SessionDep, current_user: InstructorUser
) -> list[dict[str, Any]]:
    return environment_service.list_published_environments(session, current_user)


@router.post("/drafts", status_code=201)
def create_environment_draft(
    body: EnvironmentDraftIn, session: SessionDep, current_user: InstructorUser
) -> dict[str, Any]:
    if body.draft_id is not None:
        existing = session.get(CourseEnvironment, body.draft_id)
        if existing is not None:
            # 續寫既有草稿：權限由 save_environment_draft 的 get_environment 把關
            return save_environment_draft(existing.id, body, session, current_user)
    return environment_service.create_draft(session, current_user, body)


@router.put("/{environment_id}/draft")
def save_environment_draft(
    environment_id: uuid.UUID,
    body: EnvironmentDraftIn,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    environment = environment_service.get_environment(
        session, current_user, environment_id
    )
    return environment_service.save_draft(session, environment, body)


@router.get("/{environment_id}")
def get_environment(
    environment_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    environment = environment_service.get_environment(
        session, current_user, environment_id
    )
    return environment_service.serialize_latest(session, environment)


@router.post("", status_code=201)
def create_environment(
    body: EnvironmentCreate,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    return environment_service.create_environment(session, current_user, body)


@router.put("/{environment_id}")
def update_environment(
    environment_id: uuid.UUID,
    body: EnvironmentUpdate,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    environment = environment_service.get_environment(
        session, current_user, environment_id
    )
    return environment_service.update_environment(
        session, environment, body, current_user
    )


@router.patch("/{environment_id}/basics")
def update_environment_basics(
    environment_id: uuid.UUID,
    body: EnvironmentBasicsIn,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    """調整名稱、用途與提供方式，不需要開新版本。"""
    environment = environment_service.get_environment(
        session, current_user, environment_id
    )
    return environment_service.update_basics(session, environment, body)


@router.post("/{environment_id}/files", status_code=201)
async def upload_environment_file(
    environment_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """文件掛在環境身分上，換版本不會讓講義跟著消失。"""
    environment = environment_service.get_environment(
        session, current_user, environment_id
    )
    return await environment_service.add_file(
        session, environment, file, current_user
    )


@router.get("/{environment_id}/files/{file_id}", response_class=FileResponse)
def download_environment_file(
    environment_id: uuid.UUID,
    file_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> FileResponse:
    _environment, item = environment_service.get_environment_file(
        session, current_user, environment_id, file_id
    )
    return FileResponse(
        environment_service.stored_file_path(item), filename=item.filename
    )


@router.delete("/{environment_id}/files/{file_id}")
def delete_environment_file(
    environment_id: uuid.UUID,
    file_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    environment, item = environment_service.get_environment_file(
        session, current_user, environment_id, file_id
    )
    return environment_service.delete_file(session, environment, item)


@router.post("/{environment_id}/publish")
def publish_environment(
    environment_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    environment = environment_service.get_environment(
        session, current_user, environment_id
    )
    return environment_service.publish(session, environment, current_user)


@router.delete("/{environment_id}")
def delete_environment(
    environment_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, str]:
    """硬刪除；只允許沒有任何引用的環境，其餘一律走下架。"""
    environment = environment_service.get_environment(
        session, current_user, environment_id
    )
    environment_service.delete_environment(session, environment)
    return {"status": "deleted"}
