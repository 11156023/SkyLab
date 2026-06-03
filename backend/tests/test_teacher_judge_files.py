from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from app import models  # noqa: F401
from app.ai.teacher_judge import file_service, script_artifact_service
from app.ai.teacher_judge.schemas import RubricAnalysis, RubricItem
from app.models.teacher_judge_file import TeacherJudgeFile, TeacherJudgeFileStatus
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)

SAFE_SCRIPT = """
import json
print(json.dumps({
    "schema_version": "teacher_judge_result.v1",
    "metadata": {},
    "summary": "ok",
    "checks": [],
    "errors": [],
}, ensure_ascii=False))
""".strip()


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _analysis(summary: str = "rubric") -> RubricAnalysis:
    return RubricAnalysis(
        items=[
            RubricItem(
                id="item-1",
                title="Web UI",
                description="確認服務可存取",
                checked=False,
                detectable="auto",
                detection_method="檢查 localhost",
                fallback=None,
                check_steps=[],
            )
        ],
        total_items=1,
        auto_count=1,
        summary=summary,
    )


def test_active_file_by_name_can_lock_existing_row_for_overwrite() -> None:
    captured_statement: Any = None

    class Result:
        def first(self) -> None:
            return None

    class DummySession:
        def exec(self, statement: Any) -> Result:
            nonlocal captured_statement
            captured_statement = statement
            return Result()

    file_service._active_file_by_name(
        session=DummySession(),
        group_id=uuid.uuid4(),
        original_filename="rubric.pdf",
        for_update=True,
    )

    assert captured_statement is not None
    assert getattr(captured_statement, "_for_update_arg", None) is not None


def test_save_file_requires_conflict_strategy_for_same_active_name(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(file_service, "DATA_ROOT", tmp_path)
    session = _session()
    group_id = uuid.uuid4()

    first = file_service.save_analyzed_file(
        session=session,
        group_id=group_id,
        uploaded_by=uuid.uuid4(),
        original_filename="rubric.pdf",
        file_hash="a" * 64,
        template_key="linux",
        file_bytes=b"one",
        analysis=_analysis("one"),
        conflict_strategy=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        file_service.save_analyzed_file(
            session=session,
            group_id=group_id,
            uploaded_by=uuid.uuid4(),
            original_filename="rubric.pdf",
            file_hash="b" * 64,
            template_key="linux",
            file_bytes=b"two",
            analysis=_analysis("two"),
            conflict_strategy=None,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["file_id"] == first.id


def test_copy_strategy_creates_filename_copy(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(file_service, "DATA_ROOT", tmp_path)
    session = _session()
    group_id = uuid.uuid4()

    file_service.save_analyzed_file(
        session=session,
        group_id=group_id,
        uploaded_by=uuid.uuid4(),
        original_filename="rubric.pdf",
        file_hash="a" * 64,
        template_key="linux",
        file_bytes=b"one",
        analysis=_analysis("one"),
        conflict_strategy=None,
    )
    copy = file_service.save_analyzed_file(
        session=session,
        group_id=group_id,
        uploaded_by=uuid.uuid4(),
        original_filename="rubric.pdf",
        file_hash="b" * 64,
        template_key="linux",
        file_bytes=b"two",
        analysis=_analysis("two"),
        conflict_strategy="copy",
    )

    assert copy.original_filename == "rubric (2).pdf"
    assert copy.status == "active"
    assert len(file_service.list_files(session=session, group_id=group_id)) == 2


def test_save_file_write_failure_rolls_back_db(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(file_service, "DATA_ROOT", tmp_path)
    session = _session()
    group_id = uuid.uuid4()

    def fail_write_bytes(self, data):
        raise OSError("disk full")

    monkeypatch.setattr(file_service.Path, "write_bytes", fail_write_bytes)

    with pytest.raises(OSError):
        file_service.save_analyzed_file(
            session=session,
            group_id=group_id,
            uploaded_by=uuid.uuid4(),
            original_filename="rubric.pdf",
            file_hash="a" * 64,
            template_key="linux",
            file_bytes=b"one",
            analysis=_analysis("one"),
            conflict_strategy=None,
        )

    files = session.exec(select(TeacherJudgeFile)).all()
    assert files == []


def test_active_filename_unique_constraint_blocks_duplicate_active_files() -> None:
    session = _session()
    group_id = uuid.uuid4()
    session.add(
        TeacherJudgeFile(
            group_id=group_id,
            uploaded_by=uuid.uuid4(),
            original_filename="rubric.pdf",
            file_hash="a" * 64,
            template_key="linux",
            analysis_json=_analysis("one").model_dump(mode="json"),
            status=TeacherJudgeFileStatus.active,
        )
    )
    session.commit()
    session.add(
        TeacherJudgeFile(
            group_id=group_id,
            uploaded_by=uuid.uuid4(),
            original_filename="rubric.pdf",
            file_hash="b" * 64,
            template_key="linux",
            analysis_json=_analysis("two").model_dump(mode="json"),
            status=TeacherJudgeFileStatus.active,
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.asyncio
async def test_overwrite_linked_file_marks_old_file_replaced_and_keeps_script(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(file_service, "DATA_ROOT", tmp_path)
    session = _session()
    group_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async def fake_build_reviewed_script(*, rubric_snapshot, template_key):
        return (
            SAFE_SCRIPT,
            {"approved": True, "blocked": False, "risk_level": "low", "issues": []},
            {"approved": True, "risk_level": "low", "issues": []},
            TeacherJudgeScriptStatus.reviewed,
        )

    monkeypatch.setattr(
        script_artifact_service,
        "build_reviewed_script",
        fake_build_reviewed_script,
    )

    first = file_service.save_analyzed_file(
        session=session,
        group_id=group_id,
        uploaded_by=user_id,
        original_filename="rubric.pdf",
        file_hash="a" * 64,
        template_key="linux",
        file_bytes=b"one",
        analysis=_analysis("one"),
        conflict_strategy=None,
    )
    artifact = await script_artifact_service.create_artifact(
        session=session,
        group_id=group_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_analysis=_analysis("one"),
        created_by=user_id,
        source_file_id=uuid.UUID(first.id),
    )
    second = file_service.save_analyzed_file(
        session=session,
        group_id=group_id,
        uploaded_by=user_id,
        original_filename="rubric.pdf",
        file_hash="b" * 64,
        template_key="linux",
        file_bytes=b"two",
        analysis=_analysis("two"),
        conflict_strategy="overwrite",
    )

    old_file = session.get(TeacherJudgeFile, uuid.UUID(first.id))
    active_files = session.exec(
        select(TeacherJudgeFile).where(
            TeacherJudgeFile.group_id == group_id,
            TeacherJudgeFile.status == TeacherJudgeFileStatus.active,
        )
    ).all()

    assert old_file is not None
    assert old_file.status == TeacherJudgeFileStatus.replaced
    assert second.id != first.id
    assert len(active_files) == 1
    assert active_files[0].id == uuid.UUID(second.id)
    assert artifact.source_file_id == first.id
    assert artifact.source_file_snapshot_json["original_filename"] == "rubric.pdf"


@pytest.mark.asyncio
async def test_delete_file_keeps_linked_script_with_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(file_service, "DATA_ROOT", tmp_path)
    session = _session()
    group_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async def fake_build_reviewed_script(*, rubric_snapshot, template_key):
        return (
            SAFE_SCRIPT,
            {"approved": True, "blocked": False, "risk_level": "low", "issues": []},
            {"approved": True, "risk_level": "low", "issues": []},
            TeacherJudgeScriptStatus.reviewed,
        )

    monkeypatch.setattr(
        script_artifact_service,
        "build_reviewed_script",
        fake_build_reviewed_script,
    )

    saved_file = file_service.save_analyzed_file(
        session=session,
        group_id=group_id,
        uploaded_by=user_id,
        original_filename="rubric.pdf",
        file_hash="a" * 64,
        template_key="linux",
        file_bytes=b"one",
        analysis=_analysis("one"),
        conflict_strategy=None,
    )
    artifact = await script_artifact_service.create_artifact(
        session=session,
        group_id=group_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_analysis=_analysis("one"),
        created_by=user_id,
        source_file_id=uuid.UUID(saved_file.id),
    )

    file_service.delete_file(
        session=session,
        group_id=group_id,
        file_id=uuid.UUID(saved_file.id),
    )
    db_artifact = session.get(TeacherJudgeScriptArtifact, uuid.UUID(artifact.id))

    assert db_artifact is not None
    assert db_artifact.source_file_id is None
    assert db_artifact.script_content == SAFE_SCRIPT
    assert db_artifact.source_file_snapshot_json["original_filename"] == "rubric.pdf"
