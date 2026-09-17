"""Student-facing multi-machine quick-practice API."""

import uuid

from fastapi import APIRouter

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.services import quick_practice as quick_practice_service
from app.services.scheduling.recurrence import get_schedule_policy

router = APIRouter(prefix="/quick-practice", tags=["quick-practice"])


def _serialize_template(session: SessionDep, environment, version) -> dict:
    nodes = quick_practice_service.nodes_for_version(
        session, version_id=version.id
    )
    return {
        "id": environment.id,
        "version_id": version.id,
        "name": environment.name,
        "description": environment.description,
        "version": version.version,
        "status": version.status,
        "usage_scope": environment.usage_scope,
        "published_at": version.published_at,
        "duration_hours": get_schedule_policy(session=session).practice_session_hours,
        "nodes": [node.model_dump() for node in nodes],
        "per_student": {
            "machines": len(nodes),
            "cpu_cores": sum(node.cpu for node in nodes),
            "memory_mb": sum(node.memory_mb for node in nodes),
            "disk_gb": sum(node.disk_gb for node in nodes),
        },
    }


@router.get("/templates")
def list_templates(session: SessionDep, _current_user: CurrentUser) -> list[dict]:
    return [
        _serialize_template(session, environment, version)
        for environment, version in quick_practice_service.list_published_templates(
            session
        )
    ]


@router.get("/templates/{environment_id}")
def get_template(
    environment_id: uuid.UUID,
    session: SessionDep,
    _current_user: CurrentUser,
) -> dict:
    environment, version = quick_practice_service.get_published_template(
        session, environment_id=environment_id
    )
    return _serialize_template(session, environment, version)


@router.post("/templates/{environment_id}/launch", status_code=202)
def launch_template(
    environment_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> dict:
    item = quick_practice_service.launch(
        session, user=current_user, environment_id=environment_id
    )
    return quick_practice_service.serialize_session(session, item)


@router.post("/sessions/{practice_id}/end")
def end_session(
    practice_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> dict:
    """提早結束自己的練習，整組立刻進入回收。"""
    item = quick_practice_service.end_session(
        session, user=current_user, practice_id=practice_id
    )
    return quick_practice_service.serialize_session(session, item)


@router.get("/sessions/my")
def list_my_sessions(
    session: SessionDep, current_user: CurrentUser
) -> list[dict]:
    return [
        quick_practice_service.serialize_session(session, item)
        for item in quick_practice_service.list_sessions(
            session, user_id=current_user.id
        )
    ]


@router.get("/sessions")
def list_all_sessions(
    session: SessionDep, _current_user: AdminUser
) -> list[dict]:
    return [
        quick_practice_service.serialize_session(session, item)
        for item in quick_practice_service.list_sessions(session)
    ]
