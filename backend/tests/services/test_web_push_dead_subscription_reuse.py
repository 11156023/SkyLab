"""同一輪 tick 內對同一位使用者送多則推播時，已刪除的訂閱不能再被送或寫回。

``_process_user`` 把同一份訂閱清單交給多次 ``send_messages``（多個任務同時結束、
再加上提醒）。第一則推播發現訂閱失效（404／410）就刪掉它；後面幾則若仍拿同一個
物件去送，會白打一次推播服務，而且送達失敗時要把 failure_count 寫回已刪除的列，
session.add 直接丟例外，這位使用者這一輪剩下的通知全部遺失。
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Generator
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from sqlmodel import Session

from app.core.db import engine
from app.models import PushSubscription, User
from app.services.notification import web_push_service
from app.services.notification.push_policy import test_message as push_test_message
from tests.utils.user import create_random_user

DEAD_ENDPOINT = "https://fcm.googleapis.com/fcm/send/dead-" + uuid.uuid4().hex
LIVE_ENDPOINT = "https://fcm.googleapis.com/fcm/send/live-" + uuid.uuid4().hex


@pytest.fixture
def webpush_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """第一次對失效 endpoint 送出回 410，之後回一般錯誤（無狀態碼）。"""
    calls: list[str] = []
    fake_pywebpush = ModuleType("pywebpush")

    class WebPushException(Exception):
        def __init__(self, message: str, response: Any = None) -> None:
            super().__init__(message)
            self.response = response

    def webpush(**kwargs: Any) -> None:
        endpoint = kwargs["subscription_info"]["endpoint"]
        calls.append(endpoint)
        if endpoint == DEAD_ENDPOINT:
            if calls.count(DEAD_ENDPOINT) == 1:
                raise WebPushException(
                    "gone", response=SimpleNamespace(status_code=410)
                )
            raise RuntimeError("push service timeout")

    fake_pywebpush.WebPushException = WebPushException  # type: ignore[attr-defined]
    fake_pywebpush.webpush = webpush  # type: ignore[attr-defined]
    fake_vapid = ModuleType("py_vapid")
    fake_vapid.Vapid = SimpleNamespace(from_pem=lambda pem: "vapid")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pywebpush", fake_pywebpush)
    monkeypatch.setitem(sys.modules, "py_vapid", fake_vapid)
    monkeypatch.setattr(
        web_push_service.push_repo,
        "get_vapid_private_key",
        lambda *, session, config: "PEM",
    )
    # 不能換掉全域 getaddrinfo：DB 連線也靠它解析主機
    monkeypatch.setattr(
        web_push_service,
        "check_endpoint",
        lambda endpoint: web_push_service.EndpointVerdict.ALLOWED,
    )
    return calls


@pytest.fixture
def push_user(db: Session) -> Generator[User, None, None]:
    user = create_random_user(db)
    yield user
    with Session(engine) as cleanup:
        for sub in web_push_service.push_repo.list_subscriptions_for_user(
            session=cleanup, user_id=user.id
        ):
            cleanup.delete(sub)
        cleanup.commit()


def test_dead_subscription_is_not_reused_within_same_tick(
    push_user: User, webpush_calls: list[str]
) -> None:
    with Session(engine) as session:
        for endpoint in (DEAD_ENDPOINT, LIVE_ENDPOINT):
            session.add(
                PushSubscription(
                    user_id=push_user.id, endpoint=endpoint, p256dh="p", auth="a"
                )
            )
        session.commit()
        subscriptions = list(
            web_push_service.push_repo.list_subscriptions_for_user(
                session=session, user_id=push_user.id
            )
        )
        message = push_test_message("zh-TW")

        first = web_push_service.send_messages(
            session=session, subscriptions=subscriptions, message_for_language=message
        )
        second = web_push_service.send_messages(
            session=session, subscriptions=subscriptions, message_for_language=message
        )

    assert first.sent == 1 and first.removed == 1
    assert second.sent == 1
    # 失效的訂閱只在第一則推播被送過一次
    assert webpush_calls.count(DEAD_ENDPOINT) == 1
    assert webpush_calls.count(LIVE_ENDPOINT) == 2
    with Session(engine) as check:
        remaining = list(
            web_push_service.push_repo.list_subscriptions_for_user(
                session=check, user_id=push_user.id
            )
        )
    assert [sub.endpoint for sub in remaining] == [LIVE_ENDPOINT]
