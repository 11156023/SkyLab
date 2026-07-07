"""課程內容管理 API（老師/管理員）。"""

import uuid

from fastapi import APIRouter

from app.api.deps import InstructorUser, SessionDep
from app.schemas.course import (
    CoursePathCreate,
    CoursePathPublic,
    CoursePathPublish,
    CoursePathUpdate,
    CourseQuestionCreate,
    CourseQuestionPublic,
    CourseQuestionUpdate,
    CourseRoomCreate,
    CourseRoomPublic,
    CourseRoomUpdate,
    CourseTaskCreate,
    CourseTaskPublic,
    CourseTaskUpdate,
    PathProgressReport,
)
from app.services.course import course_service, progress_service

router = APIRouter(prefix="/admin/courses", tags=["course-admin"])

# ── 路徑 ───────────────────────────────────────────────────────────────────


@router.get("/paths", response_model=list[CoursePathPublic])
def list_paths(session: SessionDep, _: InstructorUser) -> list[CoursePathPublic]:
    return course_service.list_paths(session)


@router.post("/paths", response_model=CoursePathPublic, status_code=201)
def create_path(
    session: SessionDep, current_user: InstructorUser, data: CoursePathCreate
) -> CoursePathPublic:
    return course_service.create_path(
        session, user_id=current_user.id, data=data
    )


@router.put("/paths/{path_id}", response_model=CoursePathPublic)
def update_path(
    session: SessionDep,
    _: InstructorUser,
    path_id: uuid.UUID,
    data: CoursePathUpdate,
) -> CoursePathPublic:
    return course_service.update_path(session, path_id=path_id, data=data)


@router.put("/paths/{path_id}/publish", response_model=CoursePathPublic)
def publish_path(
    session: SessionDep,
    _: InstructorUser,
    path_id: uuid.UUID,
    data: CoursePathPublish,
) -> CoursePathPublic:
    return course_service.set_path_published(
        session, path_id=path_id, published=data.published
    )


@router.delete("/paths/{path_id}", status_code=204)
def delete_path(
    session: SessionDep, _: InstructorUser, path_id: uuid.UUID
) -> None:
    course_service.delete_path(session, path_id=path_id)


@router.get("/paths/{path_id}/progress", response_model=PathProgressReport)
def path_progress(
    session: SessionDep, _: InstructorUser, path_id: uuid.UUID
) -> PathProgressReport:
    course_service.get_path_or_404(session, path_id)
    return progress_service.path_progress_report(session, path_id=path_id)


# ── 房間 ───────────────────────────────────────────────────────────────────


@router.get("/paths/{path_id}/rooms", response_model=list[CourseRoomPublic])
def list_rooms(
    session: SessionDep, _: InstructorUser, path_id: uuid.UUID
) -> list[CourseRoomPublic]:
    return course_service.list_rooms(session, path_id=path_id)


@router.post("/rooms", response_model=CourseRoomPublic, status_code=201)
def create_room(
    session: SessionDep, _: InstructorUser, data: CourseRoomCreate
) -> CourseRoomPublic:
    return course_service.create_room(session, data=data)


@router.put("/rooms/{room_id}", response_model=CourseRoomPublic)
def update_room(
    session: SessionDep,
    _: InstructorUser,
    room_id: uuid.UUID,
    data: CourseRoomUpdate,
) -> CourseRoomPublic:
    return course_service.update_room(session, room_id=room_id, data=data)


@router.delete("/rooms/{room_id}", status_code=204)
def delete_room(
    session: SessionDep, _: InstructorUser, room_id: uuid.UUID
) -> None:
    course_service.delete_room(session, room_id=room_id)


# ── 任務 ───────────────────────────────────────────────────────────────────


@router.get("/rooms/{room_id}/tasks", response_model=list[CourseTaskPublic])
def list_tasks(
    session: SessionDep, _: InstructorUser, room_id: uuid.UUID
) -> list[CourseTaskPublic]:
    return course_service.list_tasks(session, room_id=room_id)


@router.post("/tasks", response_model=CourseTaskPublic, status_code=201)
def create_task(
    session: SessionDep, _: InstructorUser, data: CourseTaskCreate
) -> CourseTaskPublic:
    return course_service.create_task(session, data=data)


@router.put("/tasks/{task_id}", response_model=CourseTaskPublic)
def update_task(
    session: SessionDep,
    _: InstructorUser,
    task_id: uuid.UUID,
    data: CourseTaskUpdate,
) -> CourseTaskPublic:
    return course_service.update_task(session, task_id=task_id, data=data)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(
    session: SessionDep, _: InstructorUser, task_id: uuid.UUID
) -> None:
    course_service.delete_task(session, task_id=task_id)


# ── 題目 ───────────────────────────────────────────────────────────────────


@router.get(
    "/tasks/{task_id}/questions", response_model=list[CourseQuestionPublic]
)
def list_questions(
    session: SessionDep, _: InstructorUser, task_id: uuid.UUID
) -> list[CourseQuestionPublic]:
    return course_service.list_questions(session, task_id=task_id)


@router.post("/questions", response_model=CourseQuestionPublic, status_code=201)
def create_question(
    session: SessionDep, _: InstructorUser, data: CourseQuestionCreate
) -> CourseQuestionPublic:
    return course_service.create_question(session, data=data)


@router.put("/questions/{question_id}", response_model=CourseQuestionPublic)
def update_question(
    session: SessionDep,
    _: InstructorUser,
    question_id: uuid.UUID,
    data: CourseQuestionUpdate,
) -> CourseQuestionPublic:
    return course_service.update_question(
        session, question_id=question_id, data=data
    )


@router.delete("/questions/{question_id}", status_code=204)
def delete_question(
    session: SessionDep, _: InstructorUser, question_id: uuid.UUID
) -> None:
    course_service.delete_question(session, question_id=question_id)
