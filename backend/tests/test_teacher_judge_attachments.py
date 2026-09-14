from __future__ import annotations

import uuid
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from app.ai.teacher_judge import attachment_service, session_service
from app.models.teacher_judge_attachment import TeacherJudgeSessionAttachment
from app.models.teacher_judge_session import (
    TeacherJudgeMessageRole,
    TeacherJudgeSession,
    TeacherJudgeSessionMessage,
)
from app.services.rubric_parser import parse_document


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_parse_document_supports_markdown_and_text() -> None:
    assert parse_document("notes.md", b"# Title\n\ncontent") == "# Title\n\ncontent"
    assert parse_document("notes.txt", b"\xef\xbb\xbfplain text") == "plain text"


def test_create_attachment_is_persisted_and_reintroduced_in_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = _session()
    session = TeacherJudgeSession(teaching_class_id=uuid.uuid4(), title="Attachments")
    db.add(session)
    db.commit()
    db.refresh(session)
    monkeypatch.setattr(attachment_service, "ATTACHMENT_ROOT", tmp_path)

    attachment = attachment_service.create_attachment(
        db,
        session_id=session.id,
        uploaded_by=uuid.uuid4(),
        filename="requirements.md",
        media_type="text/markdown",
        file_bytes=b"# Requirements\nMust expose port 8080.",
    )
    message = TeacherJudgeSessionMessage(
        session_id=session.id,
        role=TeacherJudgeMessageRole.user,
        content="請讀取這份文件",
    )
    db.add(message)
    db.flush()
    attachment.message_id = message.id
    db.add(attachment)
    db.commit()

    history = session_service.bounded_history(db, session.id)

    assert history[-1].content.startswith("請讀取這份文件")
    assert "requirements.md" in history[-1].content
    assert "port 8080" in history[-1].content
    assert attachment_service.storage_path(attachment).exists()

    db.delete(attachment)
    db.commit()
    attachment_service.storage_path(attachment).unlink(missing_ok=True)


def test_legacy_doc_reports_missing_converter(monkeypatch) -> None:
    monkeypatch.setattr("app.services.rubric_parser.shutil.which", lambda _name: None)

    try:
        parse_document("legacy.doc", b"not a real doc")
    except ValueError as exc:
        assert "LibreOffice" in str(exc)
    else:
        raise AssertionError("legacy .doc should require an available converter")


def test_attachment_model_is_registered() -> None:
    assert TeacherJudgeSessionAttachment.__tablename__ in SQLModel.metadata.tables


def test_delete_session_data_removes_attachment_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = _session()
    session = TeacherJudgeSession(teaching_class_id=uuid.uuid4(), title="Delete")
    db.add(session)
    db.commit()
    db.refresh(session)
    monkeypatch.setattr(attachment_service, "ATTACHMENT_ROOT", tmp_path)
    attachment = attachment_service.create_attachment(
        db,
        session_id=session.id,
        uploaded_by=None,
        filename="notes.txt",
        media_type="text/plain",
        file_bytes=b"notes",
    )
    path = attachment_service.storage_path(attachment)

    session_service.delete_session_data(db, session)

    assert not path.exists()
