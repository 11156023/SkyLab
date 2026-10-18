"""Coverage tests for app/api/routes/teacher_judge_sessions.py route branches."""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.ai.teacher_judge.schemas import (
    TeacherJudgeScriptRunCreateRequest,
    TeacherJudgeScriptSetRunRequest,
    TeacherJudgeSessionCreateRequest,
    TeacherJudgeSessionForkRequest,
    TeacherJudgeSessionMessageCreateRequest,
    TeacherJudgeSessionScriptCreateRequest,
    TeacherJudgeSessionUpdateRequest,
)
from app.api.routes import teacher_judge_sessions as routes
from app.models.teacher_judge_attachment import TeacherJudgeSessionAttachment
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptArtifact
from app.models.teacher_judge_session import (
    TeacherJudgeMessageRole,
    TeacherJudgeSession,
    TeacherJudgeSessionMessage,
    TeacherJudgeSessionStatus,
)
from tests.ai.teacher_judge.helpers import make_session, make_teacher_judge_file

USER = SimpleNamespace(id=uuid.uuid4())
WEEK_ID = uuid.uuid4()


@pytest.fixture
def free_week(monkeypatch: pytest.MonkeyPatch):
    """建立／複製的分支測試不在意週次規則（另有專門測試），一律當成空的週次。"""
    monkeypatch.setattr(routes, "_require_free_week", lambda *args, **kwargs: None)
    return WEEK_ID


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch):
    with make_session() as db:
        class_id = uuid.uuid4()
        item = TeacherJudgeSession(teaching_class_id=class_id, title="Coverage")
        db.add(item)
        db.commit()
        db.refresh(item)
        monkeypatch.setattr(routes, "_access", lambda *args: None)
        # 週次發布的檢查另外測（test_session_week_binding）
        monkeypatch.setattr(routes, "ensure_week_published", lambda *args: None)
        yield db, class_id, item


def _integrity(message: str) -> IntegrityError:
    return IntegrityError("INSERT", {}, Exception(message))


def _raise(exc: BaseException):
    def raiser(*args, **kwargs):
        raise exc

    return raiser


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def test_save_workflow_message_commit_failure_returns_none(ctx, monkeypatch) -> None:
    db, _, item = ctx
    rolled_back: list[bool] = []
    original_rollback = db.rollback

    def rollback():
        rolled_back.append(True)
        original_rollback()

    monkeypatch.setattr(db, "commit", _raise(RuntimeError("db down")))
    monkeypatch.setattr(db, "rollback", rollback)

    result = routes._save_workflow_message(db, item, content="x", metadata={})

    assert result is None
    assert rolled_back == [True]


def test_save_workflow_message_survives_summary_failure(ctx, monkeypatch) -> None:
    db, _, item = ctx
    monkeypatch.setattr(routes, "schedule_summary", _raise(RuntimeError("boom")))

    result = routes._save_workflow_message(
        db, item, content="done", metadata={"status": "ok"}, created_by=USER.id
    )

    assert result is not None
    assert result.content == "done"
    assert db.get(TeacherJudgeSessionMessage, result.id) is not None


def test_access_missing_class_is_404(monkeypatch) -> None:
    fake_db = SimpleNamespace(get=lambda model, ident: None)
    with pytest.raises(HTTPException) as exc:
        routes._access(fake_db, uuid.uuid4(), USER)
    assert exc.value.status_code == 404


def test_access_checks_teaching_owner(monkeypatch) -> None:
    owner_id = uuid.uuid4()
    seen: list[tuple] = []
    fake_db = SimpleNamespace(
        get=lambda model, ident: SimpleNamespace(owner_id=owner_id)
    )
    monkeypatch.setattr(
        routes,
        "require_teaching_access",
        lambda user, owner: seen.append((user, owner)),
    )

    routes._access(fake_db, uuid.uuid4(), USER)

    assert seen == [(USER, owner_id)]


def test_validate_week_rejects_foreign_or_missing_week() -> None:
    class_id = uuid.uuid4()
    routes._validate_week(SimpleNamespace(), class_id, None)

    missing = SimpleNamespace(get=lambda model, ident: None)
    with pytest.raises(HTTPException) as exc:
        routes._validate_week(missing, class_id, uuid.uuid4())
    assert exc.value.status_code == 400

    foreign = SimpleNamespace(
        get=lambda model, ident: SimpleNamespace(class_id=uuid.uuid4())
    )
    with pytest.raises(HTTPException):
        routes._validate_week(foreign, class_id, uuid.uuid4())

    own = SimpleNamespace(get=lambda model, ident: SimpleNamespace(class_id=class_id))
    routes._validate_week(own, class_id, uuid.uuid4())


# ---------------------------------------------------------------------------
# session CRUD
# ---------------------------------------------------------------------------


def test_list_sessions_returns_active_rows(ctx) -> None:
    db, class_id, item = ctx
    archived = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Old",
        status=TeacherJudgeSessionStatus.archived,
    )
    db.add(archived)
    db.commit()

    result = routes.list_sessions(
        class_id, db, USER, TeacherJudgeSessionStatus.active, 0, 50
    )

    assert [row.id for row in result] == [str(item.id)]


def test_create_session_blank_mode_creates_rubric(ctx, monkeypatch, free_week) -> None:
    db, class_id, _ = ctx
    file = make_teacher_judge_file(db, class_id)
    calls: list[dict] = []

    def fake_blank(**kwargs):
        calls.append(kwargs)
        return file

    monkeypatch.setattr(routes, "create_blank_file", fake_blank)

    result = routes.create_session(
        class_id,
        TeacherJudgeSessionCreateRequest(
            teaching_class_week_id=WEEK_ID, title="  Blank  ", creation_mode="blank", rubric_name="My rubric"
        ),
        db,
        USER,
    )

    assert result.selected_file_id == str(file.id)
    assert calls[0]["display_name"] == "My rubric"
    assert calls[0]["environment_keys"] == []


def test_create_session_selected_file_conflict_is_409(ctx, monkeypatch, free_week) -> None:
    db, class_id, _ = ctx
    monkeypatch.setattr(routes, "validate_selected_file", lambda *args: None)
    monkeypatch.setattr(
        db,
        "commit",
        _raise(_integrity("uq_teacher_judge_sessions_selected_file")),
    )

    with pytest.raises(HTTPException) as exc:
        routes.create_session(
            class_id, TeacherJudgeSessionCreateRequest(teaching_class_week_id=WEEK_ID, title="Dup"), db, USER
        )

    assert exc.value.status_code == 409


def test_create_session_other_integrity_error_is_reraised(ctx, monkeypatch, free_week) -> None:
    db, class_id, _ = ctx
    monkeypatch.setattr(routes, "validate_selected_file", lambda *args: None)
    monkeypatch.setattr(db, "commit", _raise(_integrity("other")))

    with pytest.raises(IntegrityError):
        routes.create_session(
            class_id, TeacherJudgeSessionCreateRequest(teaching_class_week_id=WEEK_ID, title="Dup"), db, USER
        )


def test_fork_and_detail_return_public_session(ctx, monkeypatch, free_week) -> None:
    db, class_id, item = ctx
    clone = TeacherJudgeSession(teaching_class_id=class_id, title="Clone")
    db.add(clone)
    db.commit()
    db.refresh(clone)
    calls: list[dict] = []

    def fake_fork(session, source, **kwargs):
        calls.append({"source": source.id, **kwargs})
        return clone

    monkeypatch.setattr(routes, "fork_session_data", fake_fork)

    forked = routes.fork_session(
        class_id, item.id, TeacherJudgeSessionForkRequest(title="Clone", teaching_class_week_id=WEEK_ID), db, USER
    )
    detail = routes.get_session_detail(class_id, item.id, db, USER)

    assert forked.id == str(clone.id)
    assert calls == [
        {"source": item.id, "title": "Clone", "created_by": USER.id, "week_id": WEEK_ID}
    ]
    assert detail.id == str(item.id)


def test_update_archived_session_is_read_only(ctx) -> None:
    db, class_id, item = ctx
    item.status = TeacherJudgeSessionStatus.archived
    db.add(item)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        routes.update_session(
            class_id,
            item.id,
            TeacherJudgeSessionUpdateRequest(title="Renamed"),
            db,
            USER,
        )

    assert exc.value.status_code == 409


def test_update_session_changes_all_fields(ctx, monkeypatch) -> None:
    db, class_id, item = ctx
    week_id = uuid.uuid4()
    new_file_id = uuid.uuid4()
    item.pinned_at = datetime.now(timezone.utc)
    db.add(item)
    db.commit()
    seen: list[object] = []
    cleared = [SimpleNamespace(id="attachment")]
    monkeypatch.setattr(
        routes, "_validate_week", lambda *args: seen.append(("week", args[2]))
    )
    monkeypatch.setattr(
        routes, "validate_selected_file", lambda *args: seen.append("validate")
    )
    monkeypatch.setattr(
        routes,
        "ensure_selected_file_available",
        lambda *args, **kwargs: seen.append(("available", kwargs)),
    )
    monkeypatch.setattr(
        routes, "clear_session_messages", lambda *args, **kwargs: cleared
    )
    monkeypatch.setattr(
        routes, "finalize_cleared_attachments", lambda rows: seen.append(rows)
    )

    result = routes.update_session(
        class_id,
        item.id,
        TeacherJudgeSessionUpdateRequest(
            title="  Renamed ",
            teaching_class_week_id=week_id,
            selected_file_id=new_file_id,
            status="archived",
            is_pinned=False,
        ),
        db,
        USER,
    )

    assert result.title == "Renamed"
    assert result.status == "archived"
    db.refresh(item)
    assert item.teaching_class_week_id == week_id
    assert item.selected_file_id == new_file_id
    assert item.pinned_at is None
    assert ("week", week_id) in seen
    assert cleared in seen


def test_update_session_cannot_pin_while_archiving(ctx) -> None:
    db, class_id, item = ctx

    with pytest.raises(HTTPException) as exc:
        routes.update_session(
            class_id,
            item.id,
            TeacherJudgeSessionUpdateRequest(status="archived", is_pinned=True),
            db,
            USER,
        )

    assert exc.value.status_code == 409


def test_update_session_selected_file_conflict_is_409(ctx, monkeypatch) -> None:
    db, class_id, item = ctx
    monkeypatch.setattr(
        db,
        "commit",
        _raise(_integrity("teacher_judge_sessions.selected_file_id unique")),
    )

    with pytest.raises(HTTPException) as exc:
        routes.update_session(
            class_id,
            item.id,
            TeacherJudgeSessionUpdateRequest(title="x"),
            db,
            USER,
        )

    assert exc.value.status_code == 409


def test_update_session_other_integrity_error_is_reraised(ctx, monkeypatch) -> None:
    db, class_id, item = ctx
    monkeypatch.setattr(db, "commit", _raise(_integrity("not null failed")))

    with pytest.raises(IntegrityError):
        routes.update_session(
            class_id,
            item.id,
            TeacherJudgeSessionUpdateRequest(title="x"),
            db,
            USER,
        )


def test_archive_and_delete_session(ctx, monkeypatch) -> None:
    db, class_id, item = ctx
    deleted: list[uuid.UUID] = []
    monkeypatch.setattr(
        routes, "delete_session_data", lambda session, row: deleted.append(row.id)
    )

    archived = routes.archive_session(class_id, item.id, db, USER)
    routes.delete_session(class_id, item.id, db, USER)

    assert archived.status == "archived"
    assert deleted == [item.id]


# ---------------------------------------------------------------------------
# attachments and messages
# ---------------------------------------------------------------------------


def _upload(content: bytes = b"hello") -> SimpleNamespace:
    return SimpleNamespace(
        file=io.BytesIO(content), filename="notes.txt", content_type="text/plain"
    )


def test_upload_attachment_rejects_when_limit_reached(ctx, monkeypatch) -> None:
    db, class_id, item = ctx
    monkeypatch.setattr(routes, "MAX_ATTACHMENT_COUNT", 0)

    with pytest.raises(HTTPException) as exc:
        routes.upload_session_attachment(class_id, item.id, db, USER, _upload())

    assert exc.value.status_code == 400


def test_upload_attachment_invalid_file_is_415(ctx, monkeypatch) -> None:
    db, class_id, item = ctx
    monkeypatch.setattr(
        routes, "create_attachment", _raise(ValueError("unsupported type"))
    )

    with pytest.raises(HTTPException) as exc:
        routes.upload_session_attachment(class_id, item.id, db, USER, _upload())

    assert exc.value.status_code == 415
    assert exc.value.detail == "unsupported type"


def test_delete_attachment_success(ctx, monkeypatch) -> None:
    db, class_id, item = ctx
    attachment = TeacherJudgeSessionAttachment(
        session_id=item.id,
        original_filename="notes.txt",
        media_type="text/plain",
        size_bytes=5,
        file_hash="0" * 64,
        storage_key=f"{uuid.uuid4().hex}.txt",
        extracted_text="hello",
    )
    db.add(attachment)
    db.commit()
    deleted: list[uuid.UUID] = []
    monkeypatch.setattr(
        routes, "delete_attachment", lambda session, row: deleted.append(row.id)
    )

    routes.delete_session_attachment(class_id, item.id, attachment.id, db, USER)

    assert deleted == [attachment.id]


def _messages(db, item) -> list[TeacherJudgeSessionMessage]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        TeacherJudgeSessionMessage(
            session_id=item.id,
            role=TeacherJudgeMessageRole.user,
            content=f"m{index}",
            created_at=base + timedelta(minutes=index),
        )
        for index in range(3)
    ]
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


def test_list_messages_pages_with_cursor(ctx) -> None:
    db, class_id, item = ctx
    rows = _messages(db, item)

    latest = routes.list_messages(class_id, item.id, db, USER, None, 2)
    older = routes.list_messages(class_id, item.id, db, USER, rows[2].id, 50)

    assert [row.content for row in latest] == ["m1", "m2"]
    assert [row.content for row in older] == ["m0", "m1"]


def test_list_messages_rejects_foreign_cursor(ctx) -> None:
    db, class_id, item = ctx
    other = TeacherJudgeSession(teaching_class_id=class_id, title="Other")
    db.add(other)
    db.commit()
    foreign = _messages(db, other)[0]

    with pytest.raises(HTTPException) as exc:
        routes.list_messages(class_id, item.id, db, USER, foreign.id, 50)
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException):
        routes.list_messages(class_id, item.id, db, USER, uuid.uuid4(), 50)


# ---------------------------------------------------------------------------
# create_message failure recovery
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_ctx(ctx, monkeypatch):
    db, class_id, item = ctx
    file = make_teacher_judge_file(db, class_id)
    item.selected_file_id = file.id
    db.add(item)
    db.commit()
    db.refresh(item)
    monkeypatch.setattr(routes, "schedule_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(routes, "record_ai_template_call", lambda **kwargs: None)
    monkeypatch.setattr(
        routes, "get_enabled_template_commands", lambda *args, **kwargs: []
    )
    monkeypatch.setattr(
        routes,
        "teacher_judge_settings",
        SimpleNamespace(REQUEST_TIMEOUT_SECONDS=5, VLLM_MODEL_NAME="test"),
    )
    return db, class_id, item, file


async def test_recovery_skips_when_session_vanished(chat_ctx, monkeypatch) -> None:
    db, class_id, item, _ = chat_ctx

    def delete_then_fail(*args, **kwargs):
        db.delete(db.get(TeacherJudgeSession, item.id))
        db.commit()
        raise RuntimeError("context failed")

    monkeypatch.setattr(routes, "uses_legacy_command_context", delete_then_fail)

    with pytest.raises(RuntimeError):
        await routes.create_message(
            class_id,
            item.id,
            TeacherJudgeSessionMessageCreateRequest(content="hello"),
            db,
            USER,
        )

    assistants = db.exec(
        select(TeacherJudgeSessionMessage).where(
            TeacherJudgeSessionMessage.role == TeacherJudgeMessageRole.assistant
        )
    ).all()
    assert assistants == []


async def test_persist_deadline_records_timeout_failure(chat_ctx, monkeypatch) -> None:
    db, class_id, item, _ = chat_ctx
    calls = {"count": 0}

    def fake_perf_counter() -> float:
        calls["count"] += 1
        # prepare / wait_for / build_assistant see the start time; persist and the
        # failure recorder see a clock far past the deadline.
        return 0.0 if calls["count"] <= 3 else 1e9

    async def chat(*args, **kwargs):
        return "答覆", None, {}

    monkeypatch.setattr(routes, "perf_counter", fake_perf_counter)
    monkeypatch.setattr(routes, "chat_with_rubric", chat)

    with pytest.raises(HTTPException) as exc:
        await routes.create_message(
            class_id,
            item.id,
            TeacherJudgeSessionMessageCreateRequest(content="hello"),
            db,
            USER,
        )

    assert exc.value.status_code == 504
    rows = db.exec(select(TeacherJudgeSessionMessage)).all()
    failure = next(row for row in rows if row.role == TeacherJudgeMessageRole.assistant)
    assert failure.metadata_json["reason_code"] == "teacher_judge_workflow_timeout"
    user_row = next(row for row in rows if row.role == TeacherJudgeMessageRole.user)
    assert user_row.metadata_json["processing"] is False


# ---------------------------------------------------------------------------
# script sets
# ---------------------------------------------------------------------------


class _FakeRubric:
    @staticmethod
    def model_validate(data):
        return SimpleNamespace(items=list(data.get("items", [])))


@pytest.fixture
def script_ctx(ctx, monkeypatch):
    db, class_id, item = ctx
    file = SimpleNamespace(
        id=uuid.uuid4(),
        analysis_revision=3,
        analysis_json={"items": [{"id": "a"}]},
        template_key="linux",
    )
    failures: list[dict] = []
    monkeypatch.setattr(routes, "require_selected_file", lambda *args: file)
    monkeypatch.setattr(routes, "TeacherJudgeRubricAnalysis", _FakeRubric)
    monkeypatch.setattr(
        routes, "get_enabled_template_commands", lambda *args, **kwargs: []
    )
    monkeypatch.setattr(routes, "load_class_machine_nodes", lambda *args: ["web"])
    monkeypatch.setattr(
        routes, "ensure_script_generation_supported", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        routes,
        "_save_script_set_failure",
        lambda session, row, **kwargs: failures.append(kwargs),
    )
    return db, class_id, item, file, failures


async def test_script_set_without_items_is_not_ready(script_ctx, monkeypatch) -> None:
    db, class_id, item, file, failures = script_ctx
    file.analysis_json = {"items": []}
    monkeypatch.setattr(
        routes,
        "get_script_generation_blockers",
        lambda analysis, commands, **kwargs: [{"item_id": "a", **kwargs}],
    )

    with pytest.raises(HTTPException) as exc:
        await routes.create_session_script_set(class_id, item.id, db, USER)

    assert exc.value.status_code == 422
    assert exc.value.detail["items"] == [{"item_id": "a", "require_target_node": True}]
    assert failures[0]["stage"] == "script_preflight"


async def test_create_script_set_unexpected_error_is_recorded(
    script_ctx, monkeypatch
) -> None:
    db, class_id, item, _, failures = script_ctx
    monkeypatch.setattr(routes, "create_artifact_set", _raise(RuntimeError("bad")))

    with pytest.raises(RuntimeError):
        await routes.create_session_script_set(class_id, item.id, db, USER)

    assert failures[-1]["stage"] == "script_generation"
    assert failures[-1]["detail"] is None


def test_list_and_get_script_sets(ctx, monkeypatch) -> None:
    db, class_id, item = ctx
    set_id = uuid.uuid4()
    monkeypatch.setattr(routes, "list_artifact_sets", lambda **kwargs: [kwargs])
    monkeypatch.setattr(routes, "get_artifact_set", lambda **kwargs: kwargs)

    listed = routes.list_session_script_sets(class_id, item.id, db, USER)
    detail = routes.get_session_script_set(class_id, item.id, set_id, db, USER)

    assert listed[0]["session_id"] == item.id
    assert detail["artifact_set_id"] == set_id


async def test_regenerate_script_set_source_mismatch(script_ctx, monkeypatch) -> None:
    db, class_id, item, _, failures = script_ctx
    monkeypatch.setattr(
        routes,
        "get_artifact_set",
        lambda **kwargs: SimpleNamespace(source_file_id=str(uuid.uuid4())),
    )

    with pytest.raises(HTTPException) as exc:
        await routes.regenerate_session_script_set(
            class_id, item.id, uuid.uuid4(), db, USER
        )

    assert exc.value.status_code == 409
    assert failures[0]["stage"] == "persistence"


async def test_regenerate_script_set_success(script_ctx, monkeypatch) -> None:
    db, class_id, item, file, _ = script_ctx
    set_id = uuid.uuid4()
    calls: list[dict] = []
    monkeypatch.setattr(
        routes,
        "get_artifact_set",
        lambda **kwargs: SimpleNamespace(source_file_id=str(file.id)),
    )

    def fake_create(**kwargs):
        calls.append(kwargs)
        return "regenerated"

    monkeypatch.setattr(routes, "create_artifact_set", fake_create)

    result = await routes.regenerate_session_script_set(
        class_id,
        item.id,
        set_id,
        db,
        USER,
        TeacherJudgeSessionScriptCreateRequest(analysis_revision=3),
    )

    assert result == "regenerated"
    assert calls[0]["artifact_set_id"] == set_id


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------


def test_script_set_run_rejects_unsupported_scope(ctx, monkeypatch) -> None:
    db, class_id, item = ctx
    monkeypatch.setattr(routes, "get_artifact_set", lambda **kwargs: None)

    with pytest.raises(HTTPException) as exc:
        routes.create_session_script_set_run(
            class_id,
            item.id,
            uuid.uuid4(),
            TeacherJudgeScriptSetRunRequest.model_construct(target_scope="manual"),
            db,
            USER,
        )

    assert exc.value.status_code == 422


def test_run_batch_and_run_listing_routes(ctx, monkeypatch) -> None:
    db, class_id, item = ctx
    batch_id = uuid.uuid4()
    monkeypatch.setattr(routes, "get_script_run_batch_public", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        routes, "list_session_run_summaries", lambda *args: list(args[1:])
    )

    batch = routes.get_session_script_run_batch(class_id, item.id, batch_id, db, USER)
    runs = routes.list_session_runs(class_id, item.id, db, USER, 5, 10)

    assert batch["run_batch_id"] == batch_id
    assert runs == [class_id, item.id, 5, 10, None]


def _artifact_getter(db, monkeypatch, artifact):
    original_get = db.get

    def fake_get(model, ident, **kwargs):
        if model is TeacherJudgeScriptArtifact:
            return artifact
        return original_get(model, ident, **kwargs)

    monkeypatch.setattr(db, "get", fake_get)


def test_create_session_run_requires_session_artifact(ctx, monkeypatch) -> None:
    db, class_id, item = ctx
    _artifact_getter(
        db,
        monkeypatch,
        SimpleNamespace(teaching_class_id=class_id, session_id=uuid.uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        routes.create_session_run(
            class_id,
            item.id,
            uuid.uuid4(),
            TeacherJudgeScriptRunCreateRequest(target_vmids=[101]),
            db,
            USER,
        )

    assert exc.value.status_code == 404


def test_create_session_run_submits_run(ctx, monkeypatch) -> None:
    db, class_id, item = ctx
    artifact_id = uuid.uuid4()
    run_id = uuid.uuid4()
    submitted: list[str] = []
    created: list[dict] = []
    _artifact_getter(
        db,
        monkeypatch,
        SimpleNamespace(teaching_class_id=class_id, session_id=item.id),
    )

    def fake_create_run(**kwargs):
        created.append(kwargs)
        return SimpleNamespace(id=str(run_id))

    monkeypatch.setattr(routes, "create_script_run", fake_create_run)
    monkeypatch.setattr(routes, "execute_script_run", lambda rid: ("run", rid))
    monkeypatch.setattr(
        routes, "submit", lambda job, **kwargs: submitted.append(kwargs["task_id"])
    )
    monkeypatch.setattr(routes, "get_script_run_public", lambda **kwargs: kwargs)

    result = routes.create_session_run(
        class_id,
        item.id,
        artifact_id,
        TeacherJudgeScriptRunCreateRequest(target_vmids=[101, 101]),
        db,
        USER,
    )

    assert result["run_id"] == run_id
    assert result["artifact_id"] == artifact_id
    assert created[0]["target_vmids"] == [101]
    assert submitted == [f"teacher_judge_script_run:{run_id}"]
