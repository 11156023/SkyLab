"""POST /courses/questions/{id}/submit 的提交節流。

flag 題沒有嘗試次數上限時，低熵 flag（埠號、4 位數 PIN）可以用腳本暴力猜，
而且每次提交都寫一筆 audit log。路由在呼叫 progress_service 之前依
使用者×題目（每分鐘 10 次）與使用者（每分鐘 30 次）節流。

這裡把 Redis 限流換成記憶體版、直接呼叫路由函式，不需要 DB 或 Redis。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routes import courses as course_routes
from app.schemas.course import CourseAnswerResult, CourseAnswerSubmit


class _FakeLimiter:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.scopes: set[str] = set()

    async def check(
        self,
        _redis: Any,
        *,
        key: str,
        limit: int,
        window_seconds: int,
        scope: str = "",
    ) -> tuple[bool, dict[str, Any]]:
        self.scopes.add(scope)
        reset_at = datetime.now(timezone.utc) + timedelta(seconds=window_seconds)
        current = self.counts.get(key, 0)
        if current >= limit:
            return False, {"current": current, "reset_at": reset_at}
        self.counts[key] = current + 1
        return True, {"current": current + 1, "reset_at": reset_at}


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    limiter = _FakeLimiter()
    calls: list[uuid.UUID] = []

    async def get_redis() -> object:
        return object()

    def submit_answer(
        session: Any, *, user: Any, question_id: uuid.UUID, answer: str | None
    ) -> tuple[CourseAnswerResult, None, None]:
        # 真實實作在這裡寫 audit log 與進度；被節流擋下時不該走到這裡
        calls.append(question_id)
        return (
            CourseAnswerResult(
                correct=False,
                question_id=question_id,
                task_completed=False,
                room_progress_percent=0,
            ),
            None,
            None,
        )

    monkeypatch.setattr(course_routes, "get_redis", get_redis)
    monkeypatch.setattr(course_routes, "check_rate_limit_by_key", limiter.check)
    monkeypatch.setattr(
        course_routes.progress_service, "submit_answer", submit_answer
    )
    return {"limiter": limiter, "calls": calls}


def _submit(user: Any, question_id: uuid.UUID) -> CourseAnswerResult:
    return asyncio.run(
        course_routes.submit_answer(
            session=object(),  # type: ignore[arg-type]
            current_user=user,
            question_id=question_id,
            data=CourseAnswerSubmit(answer="wrong"),
        )
    )


def test_eleventh_wrong_submission_for_same_question_is_throttled(
    harness: dict[str, Any],
) -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    question_id = uuid.uuid4()

    for _ in range(10):
        assert _submit(user, question_id).correct is False

    with pytest.raises(HTTPException) as exc_info:
        _submit(user, question_id)

    assert exc_info.value.status_code == 429
    retry_after = int(exc_info.value.headers["Retry-After"])
    assert 1 <= retry_after <= 60
    # 第 11 次沒進到 service，所以不會寫進度也不會寫 audit log
    assert len(harness["calls"]) == 10
    # 不是認證類 scope，Redis 停用時仍放行
    assert harness["limiter"].scopes == {"course-submit"}
    from app.infrastructure.redis import FAIL_CLOSED_SCOPES

    assert "course-submit" not in FAIL_CLOSED_SCOPES


def test_per_question_budget_is_separate_per_user_and_question(
    harness: dict[str, Any],
) -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    other_user = SimpleNamespace(id=uuid.uuid4())
    q1, q2 = uuid.uuid4(), uuid.uuid4()

    for _ in range(10):
        _submit(user, q1)

    # 別題、別人各自有自己的額度
    _submit(user, q2)
    _submit(other_user, q1)
    assert len(harness["calls"]) == 12


def test_rotating_questions_hits_per_user_cap(harness: dict[str, Any]) -> None:
    user = SimpleNamespace(id=uuid.uuid4())

    for _ in range(30):
        _submit(user, uuid.uuid4())

    with pytest.raises(HTTPException) as exc_info:
        _submit(user, uuid.uuid4())

    assert exc_info.value.status_code == 429
    assert len(harness["calls"]) == 30
