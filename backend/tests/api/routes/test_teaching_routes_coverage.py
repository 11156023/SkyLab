"""教學相關路由（teacher_judge_scripts／files、courses、course_environments、quick_practice、classroom、rubric）的直接呼叫測試。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.api.routes import classroom as classroom_route
from app.api.routes import course_environments as env_route
from app.api.routes import courses as courses_route
from app.api.routes import quick_practice as qp_route
from app.api.routes import rubric as rubric_route
from app.api.routes import teacher_judge_files as tj_files_route
from app.api.routes import teacher_judge_scripts as tj_scripts_route
from app.models import TeachingClass
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptArtifact

_USER = SimpleNamespace(id=uuid.uuid4(), email="teacher@example.com")


def _session_with_class() -> MagicMock:
    session = MagicMock()
    session.get.return_value = SimpleNamespace(owner_id=uuid.uuid4())
    return session


def _session_with_rows(rows: dict[type, Any]) -> MagicMock:
    """依查詢的資料表回傳對應的假資料列（沒列到的回傳 None）。"""
    session = MagicMock()
    session.get.side_effect = lambda model, _id: rows.get(model)
    return session


def _session_without_class() -> MagicMock:
    session = MagicMock()
    session.get.return_value = None
    return session


# ─── teacher_judge_scripts ───────────────────────────────────────────────────


@pytest.fixture
def tj_scripts(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    mocks = SimpleNamespace(
        require=MagicMock(),
        list_artifacts=MagicMock(return_value=["a"]),
        get_artifact_public=MagicMock(return_value="artifact"),
        approve_artifact=MagicMock(return_value="approved"),
        rename_artifact=MagicMock(return_value="renamed"),
        archive_artifact=MagicMock(return_value="archived"),
        delete_artifact=MagicMock(return_value=None),
        create_script_run=MagicMock(),
        get_script_run_public=MagicMock(return_value="run-public"),
        execute_script_run=MagicMock(return_value="coroutine-placeholder"),
        submit=MagicMock(),
    )
    monkeypatch.setattr(tj_scripts_route, "require_teaching_access", mocks.require)
    for name in (
        "list_artifacts",
        "get_artifact_public",
        "approve_artifact",
        "rename_artifact",
        "archive_artifact",
        "delete_artifact",
        "create_script_run",
        "get_script_run_public",
        "execute_script_run",
        "submit",
    ):
        monkeypatch.setattr(tj_scripts_route, name, getattr(mocks, name))
    return mocks


def test_tj_scripts_missing_class_is_404(tj_scripts: SimpleNamespace) -> None:
    with pytest.raises(HTTPException) as exc_info:
        tj_scripts_route.list_class_teacher_judge_scripts(
            teaching_class_id=uuid.uuid4(),
            session=_session_without_class(),
            current_user=_USER,
        )
    assert exc_info.value.status_code == 404
    tj_scripts.require.assert_not_called()


def test_tj_scripts_routes_delegate_to_services(tj_scripts: SimpleNamespace) -> None:
    class_id = uuid.uuid4()
    script_id = uuid.uuid4()
    run_id = uuid.uuid4()
    session = _session_with_class()
    common: dict[str, Any] = {
        "teaching_class_id": class_id,
        "session": session,
        "current_user": _USER,
    }

    assert tj_scripts_route.list_class_teacher_judge_scripts(
        **common, session_id=None
    ) == ["a"]
    assert (
        tj_scripts_route.get_class_teacher_judge_script(**common, script_id=script_id)
        == "artifact"
    )
    assert (
        tj_scripts_route.approve_class_teacher_judge_script(
            **common, script_id=script_id
        )
        == "approved"
    )
    assert (
        tj_scripts_route.rename_class_teacher_judge_script(
            **common, script_id=script_id, payload=SimpleNamespace(name="新名稱")
        )
        == "renamed"
    )
    assert (
        tj_scripts_route.get_class_teacher_judge_script_run(
            **common, script_id=script_id, run_id=run_id
        )
        == "run-public"
    )
    assert (
        tj_scripts_route.archive_class_teacher_judge_script(
            **common, script_id=script_id
        )
        == "archived"
    )
    assert (
        tj_scripts_route.delete_class_teacher_judge_script(
            **common, script_id=script_id
        )
        is None
    )
    tj_scripts.approve_artifact.assert_called_once_with(
        session=session,
        teaching_class_id=class_id,
        artifact_id=script_id,
        approved_by=_USER.id,
    )
    tj_scripts.rename_artifact.assert_called_once_with(
        session=session,
        teaching_class_id=class_id,
        artifact_id=script_id,
        name="新名稱",
    )
    tj_scripts.delete_artifact.assert_called_once()
    assert tj_scripts.require.call_count == 7


def test_tj_scripts_create_run_submits_background_job(
    tj_scripts: SimpleNamespace,
) -> None:
    class_id = uuid.uuid4()
    script_id = uuid.uuid4()
    new_run_id = uuid.uuid4()
    tj_scripts.create_script_run.return_value = SimpleNamespace(id=str(new_run_id))
    payload = SimpleNamespace(
        target_scope="manual", target_vmids=[101], target_node_key="web"
    )

    # 不屬於任何一週的腳本（舊資料）照舊可以執行
    session = _session_with_rows({
        TeachingClass: SimpleNamespace(owner_id=uuid.uuid4()),
        TeacherJudgeScriptArtifact: SimpleNamespace(teaching_class_id=class_id, session_id=None),
    })
    result = tj_scripts_route.create_class_teacher_judge_script_run(
        teaching_class_id=class_id,
        script_id=script_id,
        payload=payload,
        session=session,
        current_user=_USER,
    )

    assert result == "run-public"
    create_kwargs = tj_scripts.create_script_run.call_args.kwargs
    assert create_kwargs["target_scope"].value == "manual"
    assert create_kwargs["target_vmids"] == [101]
    assert create_kwargs["started_by"] == _USER.id
    tj_scripts.execute_script_run.assert_called_once_with(new_run_id)
    tj_scripts.submit.assert_called_once_with(
        "coroutine-placeholder",
        name=f"teacher_judge_script_run:{new_run_id}",
        task_id=f"teacher_judge_script_run:{new_run_id}",
    )
    assert tj_scripts.get_script_run_public.call_args.kwargs["run_id"] == new_run_id


# ─── teacher_judge_files ─────────────────────────────────────────────────────


def test_tj_files_missing_class_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tj_files_route, "require_teaching_access", MagicMock())
    with pytest.raises(HTTPException) as exc_info:
        tj_files_route.list_class_teacher_judge_files(
            teaching_class_id=uuid.uuid4(),
            session=_session_without_class(),
            current_user=_USER,
        )
    assert exc_info.value.status_code == 404


def test_tj_files_routes_delegate_to_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    require = MagicMock()
    list_files = MagicMock(return_value=["file"])
    get_file_download = MagicMock(return_value=("/tmp/rubric.pdf", "rubric.pdf"))
    update_file_analysis = MagicMock(return_value="updated")
    monkeypatch.setattr(tj_files_route, "require_teaching_access", require)
    monkeypatch.setattr(tj_files_route, "list_files", list_files)
    monkeypatch.setattr(tj_files_route, "get_file_download", get_file_download)
    monkeypatch.setattr(tj_files_route, "update_file_analysis", update_file_analysis)
    class_id = uuid.uuid4()
    file_id = uuid.uuid4()
    common: dict[str, Any] = {
        "teaching_class_id": class_id,
        "session": _session_with_class(),
        "current_user": _USER,
    }

    assert tj_files_route.list_class_teacher_judge_files(**common) == ["file"]
    response = tj_files_route.download_class_teacher_judge_file(
        **common, file_id=file_id
    )
    assert isinstance(response, FileResponse)
    assert response.filename == "rubric.pdf"
    assert (
        tj_files_route.update_class_teacher_judge_file_analysis(
            **common,
            file_id=file_id,
            payload=SimpleNamespace(analysis={"k": "v"}, expected_revision=3),
        )
        == "updated"
    )
    assert update_file_analysis.call_args.kwargs["expected_revision"] == 3
    assert require.call_count == 3


# ─── courses ─────────────────────────────────────────────────────────────────


async def test_course_submit_rate_limit_without_reset_at_uses_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(courses_route, "get_redis", AsyncMock(return_value=None))
    monkeypatch.setattr(
        courses_route,
        "check_rate_limit_by_key",
        AsyncMock(return_value=(False, {"reset_at": None})),
    )

    with pytest.raises(HTTPException) as exc_info:
        await courses_route._enforce_submit_rate_limit(uuid.uuid4(), uuid.uuid4())

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {
        "Retry-After": str(courses_route._SUBMIT_WINDOW_SECONDS)
    }


def test_course_read_routes_delegate_to_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course_service = MagicMock()
    reminder_service = MagicMock()
    ai_assignment_service = MagicMock()
    weekly_task_service = MagicMock()
    monkeypatch.setattr(courses_route, "course_service", course_service)
    monkeypatch.setattr(courses_route, "reminder_service", reminder_service)
    monkeypatch.setattr(courses_route, "ai_assignment_service", ai_assignment_service)
    monkeypatch.setattr(courses_route, "weekly_task_service", weekly_task_service)
    session = MagicMock()
    path_id = uuid.uuid4()
    assignment_id = uuid.uuid4()

    assert (
        courses_route.list_schedule(session=session, current_user=_USER)
        is course_service.list_student_schedule.return_value
    )
    assert (
        courses_route.list_reminders(session=session, current_user=_USER)
        is reminder_service.list_student_reminders.return_value
    )
    assert (
        courses_route.list_paths(session=session, current_user=_USER)
        is course_service.list_published_paths.return_value
    )
    assert (
        courses_route.get_path(session=session, current_user=_USER, path_id=path_id)
        is course_service.get_path_detail.return_value
    )
    assert (
        courses_route.list_ai_assignments(
            session=session, current_user=_USER, path_id=path_id
        )
        is ai_assignment_service.list_student_ai_assignments.return_value
    )
    assert (
        courses_route.list_weekly_tasks(
            session=session, current_user=_USER, path_id=path_id
        )
        is weekly_task_service.list_student_weekly_tasks.return_value
    )
    assert (
        courses_route.update_ai_assignment_completion(
            session=session,
            current_user=_USER,
            path_id=path_id,
            assignment_id=assignment_id,
            body=SimpleNamespace(item_id="item-1", completed=True),
        )
        is ai_assignment_service.update_student_completion.return_value
    )
    assert (
        courses_route.get_ai_check(
            session=session,
            current_user=_USER,
            path_id=path_id,
            assignment_id=assignment_id,
            run_id=uuid.uuid4(),
        )
        is ai_assignment_service.get_student_ai_check.return_value
    )
    room_id = uuid.uuid4()
    assert (
        courses_route.get_room(session=session, current_user=_USER, room_id=room_id)
        is course_service.get_room_student_detail.return_value
    )
    course_service.get_room_student_detail.assert_called_once_with(
        session, user_id=_USER.id, room_id=room_id
    )


def test_course_pdf_routes_return_inline_file_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ai_assignment_service = MagicMock()
    ai_assignment_service.get_student_ai_assignment_source_document.return_value = (
        "/tmp/source.pdf",
        "source.pdf",
    )
    weekly_task_service = MagicMock()
    weekly_task_service.get_student_weekly_task_pdf.return_value = (
        "/tmp/week.pdf",
        "week.pdf",
    )
    monkeypatch.setattr(courses_route, "ai_assignment_service", ai_assignment_service)
    monkeypatch.setattr(courses_route, "weekly_task_service", weekly_task_service)

    weekly = courses_route.get_weekly_task_pdf(
        session=MagicMock(),
        current_user=_USER,
        path_id=uuid.uuid4(),
        week_id=uuid.uuid4(),
        file_id=uuid.uuid4(),
    )
    source = courses_route.get_ai_assignment_source_document(
        session=MagicMock(),
        current_user=_USER,
        path_id=uuid.uuid4(),
        assignment_id=uuid.uuid4(),
    )

    for response, name in ((weekly, "week.pdf"), (source, "source.pdf")):
        assert isinstance(response, FileResponse)
        assert response.media_type == "application/pdf"
        assert response.filename == name
        assert response.headers["content-disposition"].startswith("inline")


def test_course_practice_machines_without_linked_class_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course_service = MagicMock()
    course_service.get_student_class_for_path.return_value = None
    monkeypatch.setattr(courses_route, "course_service", course_service)
    session = MagicMock()

    assert (
        courses_route.list_practice_machines(
            session=session, current_user=_USER, path_id=uuid.uuid4()
        )
        == []
    )
    session.exec.assert_not_called()


async def test_course_submit_answer_broadcasts_progress_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_id = uuid.uuid4()
    progress_service = MagicMock()
    progress_service.submit_answer.return_value = ("result", path_id, {"e": 1})
    broadcast = AsyncMock()
    monkeypatch.setattr(courses_route, "_enforce_submit_rate_limit", AsyncMock())
    monkeypatch.setattr(courses_route, "progress_service", progress_service)
    monkeypatch.setattr(courses_route.course_progress_hub, "broadcast", broadcast)

    result = await courses_route.submit_answer(
        session=MagicMock(),
        current_user=_USER,
        question_id=uuid.uuid4(),
        data=SimpleNamespace(answer="flag{x}"),
    )

    assert result == "result"
    broadcast.assert_awaited_once_with(path_id, {"e": 1})


# ─── course_environments ─────────────────────────────────────────────────────


def test_course_environment_routes_delegate_to_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MagicMock()
    service.get_environment_file.return_value = (
        "env",
        SimpleNamespace(filename="handout.pdf"),
    )
    service.stored_file_path.return_value = "/tmp/handout.pdf"
    monkeypatch.setattr(env_route, "environment_service", service)
    session = MagicMock()
    environment_id = uuid.uuid4()

    assert (
        env_route.list_environments(session=session, current_user=_USER)
        is service.list_environments.return_value
    )
    assert (
        env_route.list_published_environments(session=session, current_user=_USER)
        is service.list_published_environments.return_value
    )
    body = SimpleNamespace(draft_id=None)
    assert (
        env_route.create_environment_draft(
            body=body, session=session, current_user=_USER
        )
        is service.create_draft.return_value
    )
    session.get.assert_not_called()
    assert (
        env_route.get_environment(
            environment_id=environment_id, session=session, current_user=_USER
        )
        is service.serialize_latest.return_value
    )
    assert (
        env_route.create_environment(body=body, session=session, current_user=_USER)
        is service.create_environment.return_value
    )
    assert (
        env_route.update_environment(
            environment_id=environment_id,
            body=body,
            session=session,
            current_user=_USER,
        )
        is service.update_environment.return_value
    )
    response = env_route.download_environment_file(
        environment_id=environment_id,
        file_id=uuid.uuid4(),
        session=session,
        current_user=_USER,
    )
    assert isinstance(response, FileResponse)
    assert response.filename == "handout.pdf"


# ─── quick_practice ──────────────────────────────────────────────────────────


def test_quick_practice_routes_serialize_templates_and_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MagicMock()
    environment = SimpleNamespace(
        id=uuid.uuid4(), name="Web 練習", description="d", usage_scope="all"
    )
    version = SimpleNamespace(
        id=uuid.uuid4(), version=2, status="published", published_at=None
    )
    nodes = [
        SimpleNamespace(cpu=2, memory_mb=1024, disk_gb=10, model_dump=lambda: {"n": 1}),
        SimpleNamespace(cpu=1, memory_mb=512, disk_gb=8, model_dump=lambda: {"n": 2}),
    ]
    service.nodes_for_version.return_value = nodes
    service.list_published_templates.return_value = [(environment, version)]
    service.get_published_template.return_value = (environment, version)
    service.list_sessions.return_value = ["s1", "s2"]
    service.serialize_session.side_effect = lambda _session, item: {"item": item}
    monkeypatch.setattr(qp_route, "quick_practice_service", service)
    monkeypatch.setattr(
        qp_route,
        "get_schedule_policy",
        lambda session: SimpleNamespace(practice_session_hours=3),
    )
    session = MagicMock()

    templates = qp_route.list_templates(session=session, _current_user=_USER)
    assert templates[0]["per_student"] == {
        "machines": 2,
        "cpu_cores": 3,
        "memory_mb": 1536,
        "disk_gb": 18,
    }
    assert templates[0]["duration_hours"] == 3
    assert templates[0]["nodes"] == [{"n": 1}, {"n": 2}]

    single = qp_route.get_template(
        environment_id=environment.id, session=session, _current_user=_USER
    )
    assert single["version_id"] == version.id

    service.launch.return_value = "launched"
    assert qp_route.launch_template(
        environment_id=environment.id, session=session, current_user=_USER
    ) == {"item": "launched"}
    service.end_session.return_value = "ended"
    assert qp_route.end_session(
        practice_id=uuid.uuid4(), session=session, current_user=_USER
    ) == {"item": "ended"}
    assert qp_route.list_my_sessions(session=session, current_user=_USER) == [
        {"item": "s1"},
        {"item": "s2"},
    ]
    assert qp_route.list_all_sessions(session=session, _current_user=_USER) == [
        {"item": "s1"},
        {"item": "s2"},
    ]


# ─── classroom ───────────────────────────────────────────────────────────────


def _live_session() -> SimpleNamespace:
    return SimpleNamespace(
        id="session-1",
        vmid=101,
        mode=SimpleNamespace(value="broadcast"),
        class_id=uuid.uuid4(),
        started_by=uuid.uuid4(),
        controller_user_id=None,
        subscriber_count=2,
    )


async def test_classroom_safe_cluster_listing_swallows_pve_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> list[dict[str, Any]]:
        raise RuntimeError("PVE down")

    monkeypatch.setattr(classroom_route.proxmox_service, "list_all_resources", boom)
    assert await classroom_route._safe_cluster_listing() == []


async def test_classroom_broadcast_session_and_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_session()
    service = MagicMock()
    service.start_class_broadcast = AsyncMock(return_value=live)
    service.list_sessions_for.return_value = [live]
    monkeypatch.setattr(classroom_route, "classroom_service", service)
    monkeypatch.setattr(classroom_route, "require_classroom_monitor", MagicMock())

    created = await classroom_route.create_classroom_session(
        body=SimpleNamespace(mode="broadcast", vmid=101, class_id=live.class_id),
        session=MagicMock(),
        current_user=_USER,
    )
    assert created.mode == "broadcast"
    assert created.subscriber_count == 2
    service.start_class_broadcast.assert_awaited_once()
    service.start_class_watch.assert_not_called()

    listed = await classroom_route.list_classroom_sessions(current_user=_USER)
    assert [item.id for item in listed] == ["session-1"]


# ─── rubric ──────────────────────────────────────────────────────────────────


async def test_rubric_download_without_items_is_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rubric_route, "normalize_items_for_export", lambda items: [])
    with pytest.raises(HTTPException) as exc_info:
        await rubric_route.download_excel(
            current_user=_USER, payload=SimpleNamespace(items=[], summary=None)
        )
    assert exc_info.value.status_code == 400


async def test_rubric_download_returns_xlsx(monkeypatch: pytest.MonkeyPatch) -> None:
    export = MagicMock(return_value=b"xlsx-bytes")
    monkeypatch.setattr(
        rubric_route, "normalize_items_for_export", lambda items: ["item"]
    )
    monkeypatch.setattr(rubric_route, "export_to_excel", export)

    response = await rubric_route.download_excel(
        current_user=_USER, payload=SimpleNamespace(items=["raw"], summary="總結")
    )

    assert response.body == b"xlsx-bytes"
    assert response.headers["content-disposition"] == (
        "attachment; filename=rubric.xlsx"
    )
    export.assert_called_once_with(["item"], summary="總結")
