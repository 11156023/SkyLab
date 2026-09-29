"""附件上傳 route 必須在 threadpool 執行，不能卡住 event loop。

附件解析（pdfplumber 最多 200 頁、.doc 走 LibreOffice 最長 30 秒）是阻塞工作；
若 route 寫成 ``async def``，解析期間同一個 worker 的其他請求與 WebSocket
都會停住。這裡讓 ``parse_document`` 卡在 threading.Event 上，確認另一個
請求在上傳尚未完成時仍能正常回應。
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.ai.teacher_judge import attachment_service
from app.api.deps.auth import get_current_instructor_or_admin
from app.api.deps.database import get_db
from app.api.routes import teacher_judge_sessions
from app.models.teacher_judge_session import TeacherJudgeSession


@pytest.fixture()
def upload_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[FastAPI, uuid.UUID, uuid.UUID], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'judge.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    class_id = uuid.uuid4()
    with Session(engine) as db:
        item = TeacherJudgeSession(teaching_class_id=class_id, title="Threadpool")
        db.add(item)
        db.commit()
        db.refresh(item)
        session_id = item.id

    def _db() -> Generator[Session, None, None]:
        with Session(engine) as db:
            yield db

    app = FastAPI()
    app.include_router(teacher_judge_sessions.router)

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_instructor_or_admin] = lambda: (
        SimpleNamespace(id=uuid.uuid4())
    )
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(attachment_service, "ATTACHMENT_ROOT", tmp_path / "files")
    yield app, class_id, session_id
    engine.dispose()


def test_blocking_attachment_parse_does_not_block_event_loop(
    upload_app: tuple[FastAPI, uuid.UUID, uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, class_id, session_id = upload_app
    parse_started = threading.Event()
    release_parse = threading.Event()

    def blocking_parse(filename: str, file_bytes: bytes) -> str:
        parse_started.set()
        assert release_parse.wait(timeout=10), "test never released the parser"
        return file_bytes.decode("utf-8")

    monkeypatch.setattr(attachment_service, "parse_document", blocking_parse)

    # 必須用 context manager：TestClient 才會讓所有請求共用同一個 event loop，
    # 否則每個請求各自開 loop，測不出 route 是否阻塞 loop。
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        upload = pool.submit(
            client.post,
            f"/teaching-classes/{class_id}/judge/sessions/{session_id}/attachments",
            files={"file": ("notes.md", b"# Notes\nport 8080", "text/markdown")},
        )
        try:
            assert parse_started.wait(timeout=10), "upload never reached the parser"
            ping = pool.submit(client.get, "/ping").result(timeout=5)
            assert ping.status_code == 200
            assert not upload.done()
        finally:
            release_parse.set()

        response = upload.result(timeout=10)

    assert response.status_code == 200, response.text
    attachment = response.json()["attachment"]
    assert attachment["session_id"] == str(session_id)
    assert attachment["original_filename"] == "notes.md"
