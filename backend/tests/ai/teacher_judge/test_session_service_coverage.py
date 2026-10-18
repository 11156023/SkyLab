"""Branch coverage for app.ai.teacher_judge.session_service."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.ai.teacher_judge import session_service
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricChatMessage,
    TeacherJudgeRubricItem,
)
from app.models.teacher_judge_attachment import TeacherJudgeSessionAttachment
from app.models.teacher_judge_file import TeacherJudgeFileStatus
from app.models.teacher_judge_session import (
    TeacherJudgeMessageRole,
    TeacherJudgeSession,
    TeacherJudgeSessionMessage,
)
from tests.ai.teacher_judge.helpers import make_session, make_teacher_judge_file

_T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _add_session(db: Session, **kwargs: Any) -> TeacherJudgeSession:
    kwargs.setdefault("teaching_class_id", uuid.uuid4())
    kwargs.setdefault("title", "Coverage")
    item = TeacherJudgeSession(**kwargs)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _add_messages(
    db: Session,
    session_id: uuid.UUID,
    count: int,
    *,
    role: TeacherJudgeMessageRole = TeacherJudgeMessageRole.assistant,
    content: str = "answer",
    offset: int = 0,
) -> list[TeacherJudgeSessionMessage]:
    rows = [
        TeacherJudgeSessionMessage(
            session_id=session_id,
            role=role,
            content=f"{content}-{index}",
            created_at=_T0 + timedelta(seconds=offset + index),
        )
        for index in range(count)
    ]
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


def _add_attachment(
    db: Session, session_id: uuid.UUID
) -> TeacherJudgeSessionAttachment:
    attachment = TeacherJudgeSessionAttachment(
        session_id=session_id,
        original_filename="notes.txt",
        file_hash="c" * 64,
        storage_key=f"{uuid.uuid4()}.txt",
        extracted_text="hello",
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return attachment


# --- pure projections -----------------------------------------------------


def test_normalize_workflow_item_results_projects_safe_fields() -> None:
    rows = session_service.normalize_workflow_item_results(
        [
            "not-a-dict",
            {
                "operation": {"item": {"id": "op-item"}},
                "status": "bogus",
                "title": {"nested": True},
                "description": "desc text",
                "step_id": "s1",
                "step_index": 2,
                "issues": [
                    {"step_id": "s", "step_index": 1, "detail": "```code```"},
                    "skip",
                ],
                "source_index": 3,
                "source_label": "Label",
                "detail": "Traceback (most recent call last)",
            },
        ]
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["item_id"] == "op-item"
    assert row["title"] == "未命名項目"
    assert row["status"] == "analysis_error"
    assert row["known_information"] == ["desc text"]
    assert row["step_id"] == "s1"
    assert row["step_index"] == 2
    assert row["source_index"] == 3
    assert row["source_label"] == "Label"
    assert row["detail"] == "審查回報含詳細內容，已省略原文。"
    assert row["issues"] == [
        {"step_id": "s", "step_index": 1, "detail": "審查回報含詳細內容，已省略原文。"}
    ]
    assert row["operation"] == {"id": "op-item"}


def test_conversation_focus_is_capped() -> None:
    rows = [
        {"item_id": f"i{index}", "status": "partial", "title": f"T{index}"}
        for index in range(12)
    ]
    focus = session_service.conversation_focus_from_item_results(
        rows, turn_kind="bogus"
    )
    assert focus["turn_kind"] == "follow_up"
    assert len(focus["requirements"]) == session_service.WORKFLOW_FOCUS_LIMIT


def test_script_blocker_lines_for_each_status() -> None:
    message = session_service.script_blocker_workflow_message(
        [
            {"item_id": "u", "title": "U", "status": "unsupported"},
            {"item_id": "e", "title": "E", "status": "error"},
            {"item_id": "r", "title": "R", "status": "ready"},
            "ignored",
        ],
        source_file_id=None,
        analysis_revision=None,
    )
    content = message["content"]
    assert "「U」目前沒有可安全執行的取證方式" in content
    assert "「E」這次核對未完成，請稍後重試。" in content
    assert "「R」目前不需要額外處理。" in content
    assert message["metadata"]["status"] == "analysis_error"


def test_workflow_metadata_artifact_and_issue_summary() -> None:
    artifact_id = uuid.uuid4()
    metadata = session_service._workflow_metadata(
        status="ok",
        stage="review",
        source_file_id=None,
        analysis_revision=None,
        artifact_id=artifact_id,
        issue_summary=["first", "", "second"],
    )
    assert metadata["artifact_id"] == str(artifact_id)
    assert metadata["issue_summary"] == ["first", "second"]


def test_reanalysis_message_merges_duplicate_blockers_and_proposals() -> None:
    message = session_service.reanalysis_workflow_message(
        [
            {"item_id": "a", "title": "A", "status": "partial", "detail": "x"},
            {
                "item_id": "a",
                "title": "A",
                "status": "failed",
                "detail": "y",
                "issues": [{"step_id": "s2", "step_index": 2, "detail": "z"}],
            },
        ],
        source_file_id=None,
        analysis_revision=3,
        proposal=[
            "bad",
            {"title": "no id"},
            {
                "item": {"id": "a", "title": "A"},
                "operation": "update",
                "status": "ready",
            },
        ],
    )
    metadata = message["metadata"]
    assert metadata["status"] == "analysis_error"
    assert len(metadata["item_results"]) == 1
    merged = metadata["item_results"][0]
    assert merged["item_id"] == "a"
    assert merged["operation"] == {"id": "a"}
    assert merged["status"] == "analysis_error"
    details = {issue["detail"] for issue in merged["issues"]}
    assert {"x", "y", "z"} <= details


def test_apply_proposal_operations_handles_deletes_and_invalid_rows() -> None:
    analysis = TeacherJudgeRubricAnalysis(
        items=[
            TeacherJudgeRubricItem(id="a", title="A"),
            TeacherJudgeRubricItem(id="b", title="B"),
        ]
    )
    result = session_service.apply_proposal_operations_to_analysis(
        analysis,
        [
            "bad",
            {"operation": "delete", "id": "a"},
            {"operation": "remove"},
            {"title": "no id"},
            {"id": "b", "detectable": "invalid"},
            {"id": "c", "title": "C", "detectable": "auto", "action": "add"},
        ],
    )
    assert [item.id for item in result.items] == ["b", "c"]
    assert result.total_items == 2
    assert result.auto_count == 1
    assert result.manual_count == 1


# --- DB helpers ------------------------------------------------------------


def test_require_selected_file_and_get_session_errors() -> None:
    db = make_session()
    item = _add_session(db)
    with pytest.raises(HTTPException) as no_file:
        session_service.require_selected_file(db, item)
    assert no_file.value.status_code == 409

    other_file = make_teacher_judge_file(db, uuid.uuid4())
    item.selected_file_id = other_file.id
    with pytest.raises(HTTPException) as unavailable:
        session_service.require_selected_file(db, item)
    assert unavailable.value.status_code == 409

    with pytest.raises(HTTPException) as missing:
        session_service.get_session(db, uuid.uuid4(), item.id)
    assert missing.value.status_code == 404

    session_service.validate_selected_file(db, item.teaching_class_id, None)
    assert session_service.session_public_many(db, []) == []
    assert session_service.message_attachments_by_message_ids(db, []) == {}


def test_delete_session_data_rolls_back_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    item = _add_session(db)
    restored: list[object] = []
    monkeypatch.setattr(session_service, "restore_file_delete", restored.append)

    def boom() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, "commit", boom)
    with pytest.raises(RuntimeError, match="commit failed"):
        session_service.delete_session_data(db, item)
    assert restored == [None]


def test_finalize_cleared_attachments_ignores_os_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Broken:
        def unlink(self, missing_ok: bool = False) -> None:
            raise OSError("busy")

    monkeypatch.setattr(session_service, "storage_path", lambda row: _Broken())
    session_service.finalize_cleared_attachments([SimpleNamespace(storage_key="k")])


def test_clear_session_messages_deletes_attachments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        session_service, "storage_path", lambda row: tmp_path / row.storage_key
    )
    db = make_session()
    item = _add_session(db, summary="old")
    _add_attachment(db, item.id)
    _add_messages(db, item.id, 2)

    cleared = session_service.clear_session_messages(db, item)

    assert len(cleared) == 1
    assert db.exec(select(TeacherJudgeSessionAttachment)).all() == []
    assert db.exec(select(TeacherJudgeSessionMessage)).all() == []
    assert item.summary == ""


class _TitleDB:
    def __init__(self, titles: set[str]) -> None:
        self.titles = titles

    def exec(self, statement: object) -> list[SimpleNamespace]:
        return [SimpleNamespace(title=title) for title in self.titles]


def test_fork_title_picks_next_free_suffix_and_exhausts() -> None:
    class_id = uuid.uuid4()
    db = _TitleDB({"T（副本）", "T（副本 2）"})
    assert session_service._fork_title(db, class_id, "T") == "T（副本 3）"  # type: ignore[arg-type]

    exhausted = _TitleDB(
        {"T（副本）", *(f"T（副本 {index}）" for index in range(2, 1000))}
    )
    with pytest.raises(HTTPException) as exc:
        session_service._fork_title(exhausted, class_id, "T")  # type: ignore[arg-type]
    assert exc.value.status_code == 409


def test_fork_session_rejects_unavailable_file() -> None:
    db = make_session()
    item = _add_session(db, selected_file_id=uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        session_service.fork_session_data(db, item, title=None, created_by=None)
    assert exc.value.status_code == 409


def test_fork_session_discards_cloned_file_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    source_file = make_teacher_judge_file(db, class_id)
    item = _add_session(db, teaching_class_id=class_id, selected_file_id=source_file.id)
    cloned = SimpleNamespace(id=uuid.uuid4())
    discarded: list[object] = []
    monkeypatch.setattr(session_service, "clone_file_asset", lambda **kwargs: cloned)
    monkeypatch.setattr(session_service, "discard_file_asset", discarded.append)

    def boom() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, "commit", boom)
    with pytest.raises(RuntimeError):
        session_service.fork_session_data(db, item, title=" Copy ", created_by=None)
    assert discarded == [cloned]


# --- history and summary ---------------------------------------------------


def test_bounded_history_invalid_boundary_and_size_limit() -> None:
    db = make_session()
    item = _add_session(db)
    assert (
        session_service.bounded_history(db, item.id, through_message_id=uuid.uuid4())
        == []
    )
    big = "x" * 10000
    db.add_all(
        [
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.assistant,
                content=f"{index}{big}",
                created_at=_T0 + timedelta(seconds=index),
            )
            for index in range(3)
        ]
    )
    db.commit()
    history = session_service.bounded_history(db, item.id)
    assert len(history) == 2
    assert history[-1].content.startswith("2")


def test_prepare_summary_job_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    file = make_teacher_judge_file(db, class_id)
    item = _add_session(db, teaching_class_id=class_id, selected_file_id=file.id)
    assistants = _add_messages(db, item.id, 10)
    users = _add_messages(db, item.id, 1, role=TeacherJudgeMessageRole.user, offset=100)

    def prepare(**overrides: Any) -> object:
        params: dict[str, Any] = {
            "session_id": item.id,
            "boundary_message_id": assistants[-1].id,
            "assistant_count": 10,
            "selected_file_id": file.id,
            "analysis_revision": file.analysis_revision,
        }
        params.update(overrides)
        return session_service._prepare_summary_job(db, **params)

    assert prepare(session_id=uuid.uuid4()) is None
    assert prepare(analysis_revision=999) is None
    assert prepare(assistant_count=11) is None
    assert prepare(boundary_message_id=users[0].id) is None

    monkeypatch.setattr(session_service, "bounded_history", lambda *a, **k: [])
    assert prepare() is None

    item.summary_through_assistant_count = 10
    db.add(item)
    db.commit()
    assert prepare() is None


def test_persist_summary_keeps_previous_summary_for_file_source() -> None:
    db = make_session()
    class_id = uuid.uuid4()
    file = make_teacher_judge_file(db, class_id)
    item = _add_session(
        db, teaching_class_id=class_id, selected_file_id=file.id, summary="舊摘要"
    )
    boundary = _add_messages(db, item.id, 1)[0]
    snapshot = session_service._SummaryJobSnapshot(
        session_id=item.id,
        teaching_class_id=class_id,
        boundary_message_id=boundary.id,
        assistant_count=1,
        selected_file_id=file.id,
        analysis_revision=file.analysis_revision,
        messages=(TeacherJudgeRubricChatMessage(role="assistant", content="x"),),
        previous_summary="舊摘要 token=abc",
    )
    assert session_service._persist_summary_if_current(db, snapshot, "  ") is True
    db.refresh(item)
    assert item.summary == "舊摘要 token=[REDACTED]"
    assert item.summary_through_assistant_count == 1


def _worker_engine(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(session_service, "engine", engine)
    return engine


async def test_run_summary_job_returns_when_nothing_to_summarize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _worker_engine(monkeypatch)
    called: list[object] = []

    async def fake_summary(*args: object) -> tuple[str, dict[str, object]]:
        called.append(args)
        return "never", {}

    monkeypatch.setattr(session_service, "summarize_conversation", fake_summary)
    await session_service.run_summary_job(uuid.uuid4(), uuid.uuid4(), 10, None, None)
    assert called == []


async def test_run_summary_job_logs_persistence_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _worker_engine(monkeypatch)
    snapshot = session_service._SummaryJobSnapshot(
        session_id=uuid.uuid4(),
        teaching_class_id=uuid.uuid4(),
        boundary_message_id=uuid.uuid4(),
        assistant_count=10,
        selected_file_id=None,
        analysis_revision=None,
        messages=(TeacherJudgeRubricChatMessage(role="user", content="hi"),),
        previous_summary="",
    )
    monkeypatch.setattr(
        session_service, "_prepare_summary_job", lambda db, **kwargs: snapshot
    )

    async def fake_summary(*args: object) -> tuple[str, dict[str, object]]:
        return "summary", {}

    def broken_persist(*args: object) -> bool:
        raise RuntimeError("db down")

    monkeypatch.setattr(session_service, "summarize_conversation", fake_summary)
    monkeypatch.setattr(session_service, "_persist_summary_if_current", broken_persist)
    with caplog.at_level("ERROR", logger=session_service.logger.name):
        await session_service.run_summary_job(
            snapshot.session_id, snapshot.boundary_message_id, 10, None, None
        )
    assert "summary persistence failed" in caplog.text


def _fake_submit(result: str | Exception):
    def submit(coro, **kwargs: object) -> str:
        coro.close()
        if isinstance(result, Exception):
            raise result
        return result

    return submit


def test_schedule_summary_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    db = make_session()
    monkeypatch.setattr(session_service, "submit", _fake_submit("task"))
    ghost = TeacherJudgeSession(teaching_class_id=uuid.uuid4(), title="ghost")
    assert session_service.schedule_summary(db, ghost) == ""

    item = _add_session(db, summary_through_assistant_count=10)
    _add_messages(db, item.id, 10)
    assert session_service.schedule_summary(db, item) == ""

    fresh = _add_session(db)
    _add_messages(db, fresh.id, 10)
    user = _add_messages(db, fresh.id, 1, role=TeacherJudgeMessageRole.user, offset=50)[
        0
    ]
    assert (
        session_service.schedule_summary(db, fresh, boundary_message_id=user.id) == ""
    )


def test_schedule_summary_skips_inactive_file_and_logs_submit_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    file = make_teacher_judge_file(db, class_id)
    file.status = TeacherJudgeFileStatus.replaced
    db.add(file)
    db.commit()
    item = _add_session(db, teaching_class_id=class_id, selected_file_id=file.id)
    _add_messages(db, item.id, 10)
    monkeypatch.setattr(session_service, "submit", _fake_submit("task"))
    assert session_service.schedule_summary(db, item) == ""

    file.status = TeacherJudgeFileStatus.active
    db.add(file)
    db.commit()
    monkeypatch.setattr(
        session_service, "submit", _fake_submit(RuntimeError("queue down"))
    )
    with caplog.at_level("ERROR", logger=session_service.logger.name):
        assert session_service.schedule_summary(db, item) == ""
    assert "Unable to schedule Teacher Judge summary" in caplog.text
