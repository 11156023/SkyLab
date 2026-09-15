from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from app.ai.teacher_judge import file_service, session_service
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricItem,
)
from app.models.teacher_judge_file import TeacherJudgeFile, TeacherJudgeFileStatus
from app.models.teacher_judge_session import TeacherJudgeSession


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _analysis(summary: str = "rubric") -> TeacherJudgeRubricAnalysis:
    return TeacherJudgeRubricAnalysis(
        items=[
            TeacherJudgeRubricItem(
                id="item-1",
                title="Web UI",
                description="確認服務可存取",
                checked=False,
                detectable="auto",
                detection_method="檢查 listening sockets",
                check_steps=[
                    {
                        "template_key": "linux",
                        "command_key": "system.run_command",
                        "parameters": {
                            "argv": ["ss", "-lnt"],
                            "timeout_seconds": 10,
                            "success_criteria": "命令成功並取得 listening sockets",
                        },
                    }
                ],
            )
        ],
        total_items=1,
        auto_count=1,
        summary=summary,
    )


def test_blank_file_has_created_source_metadata() -> None:
    session = _session()
    file = file_service.create_blank_file(
        session=session,
        teaching_class_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        display_name="Python 期中檢查表",
        environment_keys=["python", "linux", "python"],
    )

    session.commit()
    session.refresh(file)

    assert file.source_type == "created"
    assert file.original_filename is None
    assert file.file_hash is None
    assert file.display_name == "Python 期中檢查表"
    assert file.environment_keys == ["python", "linux"]
    assert file.template_key == "python"
    assert file.analysis_revision == 1
    assert file.analysis_json["items"] == []


def test_blank_file_accepts_postgresql_environment() -> None:
    session = _session()
    file = file_service.create_blank_file(
        session=session,
        teaching_class_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        display_name="PostgreSQL 檢查表",
        environment_keys=["postgresql"],
    )

    assert file.template_key == "postgresql"
    assert file.environment_keys == ["postgresql"]


def test_analysis_update_requires_current_revision() -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()
    file = file_service.create_blank_file(
        session=session,
        teaching_class_id=teaching_class_id,
        created_by=uuid.uuid4(),
        display_name="Revision rubric",
        environment_keys=["linux"],
    )
    session.commit()
    session.refresh(file)

    changed_analysis = _analysis("first")
    changed_analysis.detectability_needs_review = True
    changed_analysis.pending_review_item_ids = ["item-1"]
    updated = file_service.update_file_analysis(
        session=session,
        teaching_class_id=teaching_class_id,
        file_id=file.id,
        analysis=changed_analysis,
        expected_revision=1,
    )
    assert updated.analysis_revision == 2
    assert updated.analysis_json["pending_review_item_ids"] == ["item-1"]
    stored_before = session.get(TeacherJudgeFile, file.id)
    assert stored_before is not None
    before_json = stored_before.analysis_json

    with pytest.raises(HTTPException) as exc_info:
        file_service.update_file_analysis(
            session=session,
            teaching_class_id=teaching_class_id,
            file_id=file.id,
            analysis=_analysis("stale"),
            expected_revision=1,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "teacher_judge_analysis_revision_conflict"
    stored_after = session.get(TeacherJudgeFile, file.id)
    assert stored_after is not None
    assert stored_after.analysis_json == before_json


def test_historical_uploaded_file_can_still_be_downloaded_and_forked(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(file_service, "DATA_ROOT", tmp_path)
    session = _session()
    teaching_class_id = uuid.uuid4()
    source = TeacherJudgeFile(
        teaching_class_id=teaching_class_id,
        uploaded_by=uuid.uuid4(),
        original_filename="midterm.pdf",
        file_hash="a" * 64,
        template_key="linux",
        source_type="uploaded",
        display_name="midterm",
        environment_keys=["linux"],
        analysis_json=_analysis("source").model_dump(mode="json"),
        analysis_revision=1,
        status=TeacherJudgeFileStatus.active,
    )
    session.add(source)
    session.flush()
    stored_path = tmp_path / f"{source.id}.pdf"
    stored_path.write_bytes(b"source bytes")
    owner = TeacherJudgeSession(
        teaching_class_id=teaching_class_id,
        title="Uploaded source",
        selected_file_id=source.id,
    )
    session.add(owner)
    session.commit()
    session.refresh(owner)

    path, filename = file_service.get_file_download(
        session=session,
        teaching_class_id=teaching_class_id,
        file_id=source.id,
    )
    assert path == stored_path
    assert filename == "midterm.pdf"

    cloned_session = session_service.fork_session_data(
        session,
        owner,
        title="Uploaded copy",
        created_by=uuid.uuid4(),
    )
    cloned_file = session.get(TeacherJudgeFile, cloned_session.selected_file_id)
    assert cloned_file is not None
    assert cloned_file.id != source.id
    assert cloned_file.original_filename == "midterm (2).pdf"
    assert (tmp_path / f"{cloned_file.id}.pdf").read_bytes() == b"source bytes"
    assert cloned_file.analysis_json == source.analysis_json
