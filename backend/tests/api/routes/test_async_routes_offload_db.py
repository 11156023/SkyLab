"""async 路由裡的同步 DB 讀寫必須丟到 worker thread，不能在 event loop 上跑。

每個案例都在「會碰 DB 的函式」裡記下執行緒，確認不是 event loop 所在的執行緒；
需要 event loop 的部分（模型呼叫、上傳串流、session manager 的記憶體狀態）
則留在 loop 上。
"""

from __future__ import annotations

import threading
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.ai import monitoring as ai_monitoring
from app.ai.pve_log import chat as pve_chat_module
from app.ai.teacher_judge.schemas import TeacherJudgeSessionMessageCreateRequest
from app.api.routes import classroom as classroom_routes
from app.api.routes import course_environments as course_environment_routes
from app.api.routes import teacher_judge_sessions
from app.api.routes import teaching_classes as teaching_class_routes
from app.exceptions import NotFoundError
from app.models.teacher_judge_session import TeacherJudgeSession
from app.services.classroom import classroom_service
from app.services.classroom.vnc_session_manager import ClassroomSession, SessionMode
from app.services.llm_gateway.usage_writer import UsageWriter
from tests.ai.teacher_judge.helpers import make_session


class _Threads:
    def __init__(self) -> None:
        self.loop_thread = threading.get_ident()
        self.seen: dict[str, int] = {}

    def mark(self, name: str) -> None:
        self.seen[name] = threading.get_ident()

    def off_loop(self, name: str) -> bool:
        return name in self.seen and self.seen[name] != self.loop_thread


async def test_teacher_judge_chat_runs_db_work_off_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _Threads()
    db = make_session()
    class_id = uuid.uuid4()
    item = TeacherJudgeSession(teaching_class_id=class_id, title="Off loop")
    db.add(item)
    db.commit()
    db.refresh(item)

    monkeypatch.setattr(
        teacher_judge_sessions, "_access", lambda *a: threads.mark("access")
    )
    monkeypatch.setattr(
        teacher_judge_sessions,
        "get_enabled_template_commands",
        lambda *a, **k: [],
    )

    def history(*args: Any, **kwargs: Any) -> list[Any]:
        threads.mark("history")
        return []

    async def fake_chat(messages, rubric_context, **kwargs):
        threads.mark("model")
        return "好的。", None, {}

    def record_usage(**kwargs: Any) -> None:
        threads.mark("usage")

    monkeypatch.setattr(teacher_judge_sessions, "bounded_history", history)
    monkeypatch.setattr(teacher_judge_sessions, "chat_with_rubric", fake_chat)
    monkeypatch.setattr(teacher_judge_sessions, "record_ai_template_call", record_usage)

    result = await teacher_judge_sessions.create_message(
        class_id,
        item.id,
        TeacherJudgeSessionMessageCreateRequest(content="先討論"),
        db,
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert result.assistant_message.content == "好的。"
    assert threads.off_loop("access")
    assert threads.off_loop("history")
    assert threads.off_loop("usage")
    # 等模型的部分留在 event loop
    assert threads.seen["model"] == threads.loop_thread


async def test_teacher_judge_script_set_runs_off_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _Threads()

    def preflight(**kwargs: Any) -> None:
        threads.mark("preflight")
        raise HTTPException(status_code=422, detail="not ready")

    monkeypatch.setattr(
        teacher_judge_sessions, "_session_rubric_for_script_set", preflight
    )

    with pytest.raises(HTTPException):
        await teacher_judge_sessions.create_session_script_set(
            uuid.uuid4(), uuid.uuid4(), object(), SimpleNamespace(id=uuid.uuid4())
        )
    with pytest.raises(HTTPException):
        await teacher_judge_sessions.regenerate_session_script_set(
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            object(),
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert threads.off_loop("preflight")


async def test_upload_week_file_checks_class_off_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _Threads()

    def get_class(*args: Any) -> None:
        threads.mark("get_class")
        raise NotFoundError("missing")

    monkeypatch.setattr(teaching_class_routes, "_get_class", get_class)

    with pytest.raises(NotFoundError):
        await teaching_class_routes.upload_week_file(
            uuid.uuid4(),
            uuid.uuid4(),
            object(),
            SimpleNamespace(id=uuid.uuid4()),
            file=SimpleNamespace(filename="a.txt"),
        )

    assert threads.off_loop("get_class")


async def test_upload_environment_file_looks_up_environment_off_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _Threads()

    def get_environment(*args: Any) -> None:
        threads.mark("get_environment")
        raise NotFoundError("missing")

    monkeypatch.setattr(
        course_environment_routes.environment_service,
        "get_environment",
        get_environment,
    )

    with pytest.raises(NotFoundError):
        await course_environment_routes.upload_environment_file(
            uuid.uuid4(), object(), SimpleNamespace(id=uuid.uuid4()), file=None
        )

    assert threads.off_loop("get_environment")


async def test_environment_file_record_is_written_off_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上傳串流留在 loop 上讀，寫入檔案紀錄（同步 DB commit）丟到 worker thread。"""
    from app.services.course_environment import environment_service

    threads = _Threads()
    file_id = uuid.uuid4()

    async def save_upload(*args: Any, **kwargs: Any) -> tuple[uuid.UUID, str, int]:
        threads.mark("upload")
        return file_id, "stored.bin", 12

    def record(session: Any, environment: Any, **kwargs: Any) -> dict[str, Any]:
        threads.mark("record")
        assert kwargs["file_id"] == file_id
        assert kwargs["size_bytes"] == 12
        return {"recorded": True}

    monkeypatch.setattr(environment_service.upload_store, "save_upload", save_upload)
    monkeypatch.setattr(environment_service, "_record_file", record)

    result = await environment_service.add_file(
        object(),
        SimpleNamespace(id=uuid.uuid4()),
        SimpleNamespace(filename="notes.pdf"),
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert result == {"recorded": True}
    assert threads.seen["upload"] == threads.loop_thread
    assert threads.off_loop("record")


async def test_classroom_live_reads_db_off_loop_and_sessions_on_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _Threads()
    class_id = uuid.uuid4()
    broadcast = ClassroomSession(
        id="s1",
        vmid=101,
        mode=SessionMode.broadcast,
        class_id=class_id,
        started_by=uuid.uuid4(),
        controller_user_id=None,
        subscriber_count=0,
    )

    class Manager:
        def list_sessions(self) -> list[ClassroomSession]:
            threads.mark("manager")
            return [broadcast]

    def class_ids(session: Any, user_id: uuid.UUID) -> set[uuid.UUID]:
        threads.mark("class_ids")
        return {class_id}

    monkeypatch.setattr(classroom_service, "vnc_session_manager", Manager())
    monkeypatch.setattr(classroom_service, "get_class_ids_of_user", class_ids)

    result = await classroom_routes.get_live_broadcast(
        object(), SimpleNamespace(id=uuid.uuid4())
    )

    assert result.session is not None and result.session.id == "s1"
    assert result.taken_over_vmids == []
    assert threads.off_loop("class_ids")
    # session manager 不是 thread-safe，只能在 event loop 上讀
    assert threads.seen["manager"] == threads.loop_thread


async def test_classroom_broadcast_sources_query_off_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _Threads()

    async def no_cluster() -> list[dict[str, Any]]:
        return []

    def sources(*args: Any, **kwargs: Any) -> list[Any]:
        threads.mark("sources")
        return []

    monkeypatch.setattr(classroom_routes, "_safe_cluster_listing", no_cluster)
    monkeypatch.setattr(
        classroom_routes.classroom_service, "list_class_broadcast_sources", sources
    )

    assert (
        await classroom_routes.list_teaching_class_broadcast_sources(
            uuid.uuid4(), object(), SimpleNamespace(id=uuid.uuid4())
        )
        == []
    )
    assert threads.off_loop("sources")


async def test_classroom_students_query_off_loop_and_presence_on_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _Threads()
    class_id = uuid.uuid4()
    online = {uuid.uuid4()}

    async def no_cluster() -> list[dict[str, Any]]:
        return []

    def presence(requested: uuid.UUID) -> set[uuid.UUID]:
        threads.mark("presence")
        assert requested == class_id
        return online

    def students(*args: Any, **kwargs: Any) -> list[Any]:
        threads.mark("students")
        assert kwargs["online_user_ids"] is online
        return []

    monkeypatch.setattr(classroom_routes, "_safe_cluster_listing", no_cluster)
    monkeypatch.setattr(
        classroom_routes.classroom_presence_hub, "online_user_ids_for_class", presence
    )
    monkeypatch.setattr(
        classroom_routes.classroom_service, "list_class_students", students
    )

    assert (
        await classroom_routes.list_teaching_class_students(
            class_id, object(), SimpleNamespace(id=uuid.uuid4())
        )
        == []
    )
    # presence hub 是 event loop 上的記憶體狀態：在 loop 上讀；DB 查詢丟到 worker thread
    assert threads.seen["presence"] == threads.loop_thread
    assert threads.off_loop("students")


async def test_pve_chat_usage_is_recorded_off_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _Threads()
    writer = UsageWriter()
    # 呼叫端的 DB session；這條路徑只拿它判斷要不要記錄用量，寫入要另開 session
    caller_session = SimpleNamespace()
    write_sessions: list[Any] = []

    def write_usage_row(*, session: Any, **kwargs: Any) -> None:
        write_sessions.append(session)
        threads.mark(kwargs["call_type"])

    class FakeClient:
        async def create_chat_completion(self, payload, **kwargs):
            return {
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "節點正常。"},
                    }
                ],
            }

    monkeypatch.setattr(
        ai_monitoring.ai_gateway_service, "record_template_call", write_usage_row
    )
    monkeypatch.setattr(ai_monitoring, "get_usage_writer", lambda: writer)
    monkeypatch.setattr(pve_chat_module, "vllm_client", FakeClient())
    monkeypatch.setattr(
        pve_chat_module,
        "settings",
        SimpleNamespace(
            VLLM_BASE_URL="http://offline/v1",
            VLLM_MODEL_NAME="test-model",
            VLLM_TIMEOUT=30,
            VLLM_CHAT_MAX_TOKENS=512,
            VLLM_ENABLE_THINKING=False,
        ),
    )

    await pve_chat_module.chat(
        message="列出節點", session=caller_session, requester_id=uuid.uuid4()
    )
    # 用量交給背景 writer 寫：等它寫完再檢查
    await writer.close()

    assert threads.off_loop("pve_chat")
    assert threads.off_loop("pve_chat_adherence")
    assert write_sessions and all(item is not caller_session for item in write_sessions)
