"""課程學習 API（學生端）。"""

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser, SessionDep
from app.schemas.course import (
    CourseAnswerResult,
    CourseAnswerSubmit,
    CourseDeploymentPublic,
    CoursePathDetail,
    CoursePathSummary,
    CourseRoomStudentDetail,
)
from app.services.course import (
    course_service,
    deployment_service,
    progress_service,
)
from app.services.course.progress_hub import course_progress_hub

router = APIRouter(prefix="/courses", tags=["courses"])


@router.get("/paths", response_model=list[CoursePathSummary])
def list_paths(
    session: SessionDep, current_user: CurrentUser
) -> list[CoursePathSummary]:
    return course_service.list_published_paths(session, user_id=current_user.id)


@router.get("/paths/{path_id}", response_model=CoursePathDetail)
def get_path(
    session: SessionDep, current_user: CurrentUser, path_id: uuid.UUID
) -> CoursePathDetail:
    return course_service.get_path_detail(
        session, user_id=current_user.id, path_id=path_id
    )


@router.get("/rooms/{room_id}", response_model=CourseRoomStudentDetail)
def get_room(
    session: SessionDep, current_user: CurrentUser, room_id: uuid.UUID
) -> CourseRoomStudentDetail:
    detail = course_service.get_room_student_detail(
        session, user_id=current_user.id, room_id=room_id
    )
    detail.my_deployment = deployment_service.get_my_room_deployment(
        session, user_id=current_user.id, room_id=room_id
    )
    return detail


@router.post(
    "/rooms/{room_id}/deploy",
    response_model=CourseDeploymentPublic,
    status_code=202,
)
def deploy_room(
    session: SessionDep, current_user: CurrentUser, room_id: uuid.UUID
) -> CourseDeploymentPublic:
    return deployment_service.deploy(session, user=current_user, room_id=room_id)


@router.get(
    "/deployments/{deployment_id}", response_model=CourseDeploymentPublic
)
def get_deployment(
    session: SessionDep, current_user: CurrentUser, deployment_id: uuid.UUID
) -> CourseDeploymentPublic:
    return deployment_service.get_deployment(
        session, user=current_user, deployment_id=deployment_id
    )


@router.delete(
    "/deployments/{deployment_id}", response_model=CourseDeploymentPublic
)
def terminate_deployment(
    session: SessionDep, current_user: CurrentUser, deployment_id: uuid.UUID
) -> CourseDeploymentPublic:
    return deployment_service.terminate(
        session, user=current_user, deployment_id=deployment_id
    )


@router.post(
    "/questions/{question_id}/submit", response_model=CourseAnswerResult
)
async def submit_answer(
    session: SessionDep,
    current_user: CurrentUser,
    question_id: uuid.UUID,
    data: CourseAnswerSubmit,
) -> CourseAnswerResult:
    result, path_id, event = progress_service.submit_answer(
        session,
        user=current_user,
        question_id=question_id,
        answer=data.answer,
    )
    if event is not None and path_id is not None:
        await course_progress_hub.broadcast(path_id, event)
    return result
