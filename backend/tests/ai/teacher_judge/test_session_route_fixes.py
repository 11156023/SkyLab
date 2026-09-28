"""回歸測試：Teacher Judge session 路由。

- 等待 LLM 期間不能握著 DB 交易（PgBouncer transaction pooling 下會佔住連線）。
- 錯誤訊息走 i18n，不再寫死中文。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai.teacher_judge.schemas import TeacherJudgeSessionMessageCreateRequest
from app.api.routes import teacher_judge_sessions
from app.core.request_context import RequestContext, set_request_context
from app.models.teacher_judge_attachment import TeacherJudgeSessionAttachment
from app.models.teacher_judge_session import TeacherJudgeSession
from tests.ai.teacher_judge.helpers import make_session


@pytest.fixture
def english():
    set_request_context(RequestContext(language="en"))
    yield
    set_request_context(RequestContext())


def _chat_session(db) -> tuple[uuid.UUID, TeacherJudgeSession]:
    class_id = uuid.uuid4()
    item = TeacherJudgeSession(teaching_class_id=class_id, title="Chat first")
    db.add(item)
    db.commit()
    db.refresh(item)
    return class_id, item


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(
        teacher_judge_sessions,
        "get_enabled_template_commands",
        lambda *args, **kwargs: [],
    )


@pytest.mark.asyncio
async def test_chat_llm_call_runs_without_an_open_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id, item = _chat_session(db)
    seen: list[bool] = []

    async def fake_chat(messages, rubric_context, **kwargs):
        seen.append(db.in_transaction())
        assert messages[-1].content == "先討論檢查需求"
        return "可以，先描述目標環境。", None, {}

    _patch_common(monkeypatch)
    monkeypatch.setattr(teacher_judge_sessions, "chat_with_rubric", fake_chat)

    result = await teacher_judge_sessions.create_message(
        class_id,
        item.id,
        TeacherJudgeSessionMessageCreateRequest(content="先討論檢查需求"),
        db,
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert seen == [False]
    assert result.assistant_message.content == "可以，先描述目標環境。"


@pytest.mark.asyncio
async def test_itemwise_attachment_call_runs_without_an_open_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id, item = _chat_session(db)
    attachment = TeacherJudgeSessionAttachment(
        session_id=item.id,
        original_filename="notes.txt",
        media_type="text/plain",
        size_bytes=5,
        file_hash="0" * 64,
        storage_key=f"{uuid.uuid4().hex}.txt",
        extracted_text="需要檢查 nginx 是否啟動",
    )
    db.add(attachment)
    db.commit()
    seen: list[bool] = []

    async def fake_itemwise(**kwargs):
        seen.append(db.in_transaction())
        # 附件內容必須在結束交易前就讀好，不能在 await 途中才延遲載入
        assert "nginx" in kwargs["attachment_context"]
        return SimpleNamespace(
            reply="已看過附件。",
            proposal=None,
            metrics={},
            item_results=[],
            error=None,
        )

    _patch_common(monkeypatch)
    monkeypatch.setattr(
        teacher_judge_sessions, "analyze_attachments_itemwise", fake_itemwise
    )

    result = await teacher_judge_sessions.create_message(
        class_id,
        item.id,
        TeacherJudgeSessionMessageCreateRequest(
            content="", attachment_ids=[attachment.id]
        ),
        db,
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert seen == [False]
    assert result.assistant_message.content == "已看過附件。"


@pytest.mark.asyncio
async def test_empty_message_error_is_translated(
    monkeypatch: pytest.MonkeyPatch, english: None
) -> None:
    db = make_session()
    class_id, item = _chat_session(db)
    _patch_common(monkeypatch)

    with pytest.raises(HTTPException) as caught:
        await teacher_judge_sessions.create_message(
            class_id,
            item.id,
            TeacherJudgeSessionMessageCreateRequest(content="   "),
            db,
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert caught.value.status_code == 422
    assert caught.value.detail == "Enter a message or add at least one attachment."


def test_missing_attachment_error_is_translated(
    monkeypatch: pytest.MonkeyPatch, english: None
) -> None:
    db = make_session()
    class_id, item = _chat_session(db)
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)

    with pytest.raises(HTTPException) as caught:
        teacher_judge_sessions.delete_session_attachment(
            class_id, item.id, uuid.uuid4(), db, SimpleNamespace(id=uuid.uuid4())
        )

    assert caught.value.status_code == 404
    assert caught.value.detail == "Attachment not found."
