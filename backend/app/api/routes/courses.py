"""課程學習 API（學生端）。"""

import uuid

from fastapi import APIRouter
from fastapi.responses import FileResponse
from sqlmodel import select

from app.api.deps import CurrentUser, SessionDep
from app.models.teaching_class import (
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStudent,
    TeachingClassStudentMachine,
)
from app.schemas.course import (
    CourseAIAssignmentStudent,
    CourseAICheckStudent,
    CourseAICompletionStudent,
    CourseAICompletionUpdate,
    CourseAnswerResult,
    CourseAnswerSubmit,
    CourseDeploymentPublic,
    CoursePathDetail,
    CoursePathSummary,
    CoursePracticeMachineStudent,
    CourseReminderStudent,
    CourseRoomStudentDetail,
    CourseScheduleStudent,
    CourseWeeklyTaskStudent,
)
from app.services.course import (
    ai_assignment_service,
    course_service,
    deployment_service,
    progress_service,
    reminder_service,
    weekly_task_service,
)
from app.services.course.progress_hub import course_progress_hub
from app.services.teaching import course_publication_service

router = APIRouter(prefix="/courses", tags=["courses"])


@router.get("/schedule", response_model=list[CourseScheduleStudent])
def list_schedule(
    session: SessionDep,
    current_user: CurrentUser,
) -> list[CourseScheduleStudent]:
    """Return active-term classes linked to this student's course paths."""

    return course_service.list_student_schedule(
        session,
        user_id=current_user.id,
    )


@router.get("/reminders", response_model=list[CourseReminderStudent])
def list_reminders(
    session: SessionDep,
    current_user: CurrentUser,
) -> list[CourseReminderStudent]:
    """Build the student's actionable reminders from current platform data."""

    return reminder_service.list_student_reminders(
        session,
        user_id=current_user.id,
    )


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


@router.get(
    "/paths/{path_id}/ai-assignments",
    response_model=list[CourseAIAssignmentStudent],
)
def list_ai_assignments(
    session: SessionDep, current_user: CurrentUser, path_id: uuid.UUID
) -> list[CourseAIAssignmentStudent]:
    return ai_assignment_service.list_student_ai_assignments(
        session,
        user_id=current_user.id,
        path_id=path_id,
    )


@router.get(
    "/paths/{path_id}/weekly-tasks",
    response_model=list[CourseWeeklyTaskStudent],
)
def list_weekly_tasks(
    session: SessionDep, current_user: CurrentUser, path_id: uuid.UUID
) -> list[CourseWeeklyTaskStudent]:
    return weekly_task_service.list_student_weekly_tasks(
        session,
        user_id=current_user.id,
        path_id=path_id,
    )


@router.get(
    "/paths/{path_id}/weekly-tasks/{week_id}/files/{file_id}",
    response_class=FileResponse,
)
def get_weekly_task_pdf(
    session: SessionDep,
    current_user: CurrentUser,
    path_id: uuid.UUID,
    week_id: uuid.UUID,
    file_id: uuid.UUID,
) -> FileResponse:
    path, filename = weekly_task_service.get_student_weekly_task_pdf(
        session,
        user_id=current_user.id,
        path_id=path_id,
        week_id=week_id,
        file_id=file_id,
    )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=filename,
        content_disposition_type="inline",
    )


@router.get(
    "/paths/{path_id}/practice-machines",
    response_model=list[CoursePracticeMachineStudent],
)
def list_practice_machines(
    session: SessionDep,
    current_user: CurrentUser,
    path_id: uuid.UUID,
) -> list[CoursePracticeMachineStudent]:
    """List machines assigned in the exact class linked to this path."""

    teaching_class = course_service.get_student_class_for_path(
        session,
        user_id=current_user.id,
        path_id=path_id,
    )
    if teaching_class is None:
        return []
    rows = session.exec(
        select(
            TeachingClass,
            TeachingClassStudentMachine,
            TeachingClassMachineNode,
        )
        .join(
            TeachingClassStudent,
            TeachingClassStudent.class_id == TeachingClass.id,
        )
        .join(
            TeachingClassStudentMachine,
            TeachingClassStudentMachine.class_student_id == TeachingClassStudent.id,
        )
        .join(
            TeachingClassMachineNode,
            TeachingClassStudentMachine.machine_node_id == TeachingClassMachineNode.id,
        )
        .where(
            TeachingClass.id == teaching_class.id,
            TeachingClassStudent.user_id == current_user.id,
            TeachingClassStudent.status == "active",
        )
        .order_by(TeachingClass.name, TeachingClassMachineNode.sort_order)
    ).all()
    public_urls = course_publication_service.public_urls_by_vmid(
        session,
        [machine.vmid for _teaching_class, machine, _node in rows if machine.vmid],
    )
    return [
        CoursePracticeMachineStudent(
            teaching_class_id=teaching_class.id,
            teaching_class_name=teaching_class.name,
            machine_node_id=node.id,
            node_key=node.node_key,
            name=node.name,
            role=node.role,
            resource_type=node.resource_type,
            vmid=machine.vmid,
            status=machine.status,
            public_url=public_urls.get(machine.vmid) if machine.vmid else None,
        )
        for teaching_class, machine, node in rows
    ]


@router.get(
    "/paths/{path_id}/ai-assignments/{assignment_id}/source-document",
    response_class=FileResponse,
)
def get_ai_assignment_source_document(
    session: SessionDep,
    current_user: CurrentUser,
    path_id: uuid.UUID,
    assignment_id: uuid.UUID,
) -> FileResponse:
    """Preview the uploaded PDF tied to an approved assignment."""

    path, filename = ai_assignment_service.get_student_ai_assignment_source_document(
        session,
        user_id=current_user.id,
        path_id=path_id,
        assignment_id=assignment_id,
    )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=filename,
        content_disposition_type="inline",
    )


@router.put(
    "/paths/{path_id}/ai-assignments/{assignment_id}/completion",
    response_model=CourseAICompletionStudent,
)
def update_ai_assignment_completion(
    session: SessionDep,
    current_user: CurrentUser,
    path_id: uuid.UUID,
    assignment_id: uuid.UUID,
    body: CourseAICompletionUpdate,
) -> CourseAICompletionStudent:
    """Record completion only; teachers decide when to run the final check."""

    return ai_assignment_service.update_student_completion(
        session,
        user_id=current_user.id,
        path_id=path_id,
        assignment_id=assignment_id,
        item_id=body.item_id,
        completed=body.completed,
    )


@router.get(
    "/paths/{path_id}/ai-assignments/{assignment_id}/checks/{run_id}",
    response_model=CourseAICheckStudent,
)
def get_ai_check(
    session: SessionDep,
    current_user: CurrentUser,
    path_id: uuid.UUID,
    assignment_id: uuid.UUID,
    run_id: uuid.UUID,
) -> CourseAICheckStudent:
    return ai_assignment_service.get_student_ai_check(
        session,
        user_id=current_user.id,
        path_id=path_id,
        assignment_id=assignment_id,
        run_id=run_id,
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
