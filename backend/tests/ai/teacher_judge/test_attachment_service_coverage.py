"""Coverage for app.ai.teacher_judge.attachment_service validation and cleanup paths."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.ai.teacher_judge import attachment_service as svc
from app.models.teacher_judge_attachment import (
    TeacherJudgeAttachmentStatus,
    TeacherJudgeMessageAttachment,
    TeacherJudgeSessionAttachment,
)
from tests.ai.teacher_judge.helpers import make_session


@pytest.fixture(autouse=True)
def _isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "ATTACHMENT_ROOT", tmp_path / "attachments")
    monkeypatch.setattr(svc, "settings", SimpleNamespace(VLLM_MAX_UPLOAD_SIZE_MB=1))
    monkeypatch.setattr(svc, "parse_document", lambda name, data: data.decode())


def _attachment(
    session_id: uuid.UUID,
    status: TeacherJudgeAttachmentStatus = TeacherJudgeAttachmentStatus.ready,
    text: str = "content",
) -> TeacherJudgeSessionAttachment:
    return TeacherJudgeSessionAttachment(
        session_id=session_id,
        original_filename="notes.md",
        media_type="text/markdown",
        size_bytes=7,
        file_hash="a" * 64,
        storage_key=f"{uuid.uuid4()}.md",
        extracted_text=text,
        status=status,
    )


def _save(db: Session, *rows: Any) -> None:
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)


def _create(db: Any, filename: str | None = "notes.md", data: bytes = b"hello"):
    return svc.create_attachment(
        db,
        session_id=uuid.uuid4(),
        uploaded_by=None,
        filename=filename,
        media_type=None,
        file_bytes=data,
    )


def test_safe_filename_blank_falls_back() -> None:
    assert svc._safe_filename("   ") == "attachment"
    assert svc._safe_filename(None) == "attachment"


def test_sent_attachment_ids_empty() -> None:
    assert svc._sent_attachment_ids(make_session(), []) == set()


def test_get_pending_attachments_validation() -> None:
    db = make_session()
    sid = uuid.uuid4()
    ready = _attachment(sid)
    failed = _attachment(sid, status=TeacherJudgeAttachmentStatus.failed)
    sent = _attachment(sid)
    _save(db, ready, failed, sent)
    _save(
        db,
        TeacherJudgeMessageAttachment(attachment_id=sent.id, message_id=uuid.uuid4()),
    )

    with pytest.raises(HTTPException) as dup:
        svc.get_pending_attachments(db, sid, [ready.id, ready.id])
    assert dup.value.status_code == 400
    with pytest.raises(HTTPException) as missing:
        svc.get_pending_attachments(db, sid, [uuid.uuid4()])
    assert missing.value.status_code == 400
    with pytest.raises(HTTPException) as already:
        svc.get_pending_attachments(db, sid, [sent.id])
    assert already.value.status_code == 409
    with pytest.raises(HTTPException) as not_ready:
        svc.get_pending_attachments(db, sid, [failed.id])
    assert not_ready.value.status_code == 409
    assert svc.get_pending_attachments(db, sid, [ready.id]) == [ready]


def test_create_attachment_rejects_bad_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    db = make_session()
    with pytest.raises(ValueError, match="附件格式不支援"):
        _create(db, filename="evil.exe")
    with pytest.raises(ValueError, match="MB"):
        _create(db, data=b"x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="空白"):
        _create(db, data=b"")
    with pytest.raises(ValueError, match="沒有可讀取"):
        _create(db, data=b"   ")

    def raise_value(name: str, data: bytes) -> str:
        raise ValueError("parser says no")

    monkeypatch.setattr(svc, "parse_document", raise_value)
    with pytest.raises(ValueError, match="parser says no"):
        _create(db)

    def crash(name: str, data: bytes) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(svc, "parse_document", crash)
    with pytest.raises(ValueError, match="附件解析失敗"):
        _create(db)


class _FailingDb:
    def __init__(self) -> None:
        self.rolled_back = False

    def add(self, _obj: Any) -> None:
        raise RuntimeError("db down")

    def rollback(self) -> None:
        self.rolled_back = True


class _StubbornPath:
    def write_bytes(self, _data: bytes) -> None:
        return None

    def unlink(self, missing_ok: bool = False) -> None:
        raise OSError("locked")


def test_create_attachment_db_failure_unlink_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svc, "storage_path", lambda attachment: _StubbornPath())
    db = _FailingDb()
    with pytest.raises(RuntimeError, match="db down"):
        _create(db)
    assert db.rolled_back


def test_delete_attachment_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    db = make_session()
    sid = uuid.uuid4()
    sent = _attachment(sid)
    loose = _attachment(sid)
    stubborn = _attachment(sid)
    _save(db, sent, loose, stubborn)
    _save(
        db,
        TeacherJudgeMessageAttachment(attachment_id=sent.id, message_id=uuid.uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        svc.delete_attachment(db, sent)
    assert exc.value.status_code == 409

    svc.ATTACHMENT_ROOT.mkdir(parents=True, exist_ok=True)
    file_path = svc.storage_path(loose)
    file_path.write_bytes(b"x")
    svc.delete_attachment(db, loose)
    assert not file_path.exists()
    assert db.get(TeacherJudgeSessionAttachment, loose.id) is None

    monkeypatch.setattr(svc, "storage_path", lambda attachment: _StubbornPath())
    svc.delete_attachment(db, stubborn)
    assert db.get(TeacherJudgeSessionAttachment, stubborn.id) is None


def test_attachment_compact_context_empty_and_listed() -> None:
    assert svc.attachment_compact_context([]) == "（本次訊息沒有附件）"
    text = svc.attachment_compact_context([_attachment(uuid.uuid4(), text="abc")])
    assert "notes.md（解析文字 3 字，已收斂）" in text
