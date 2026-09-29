"""推播 endpoint 不能利用 urlsplit 與送出端 urllib3 的解析差異繞過白名單（SSRF）。

``https://127.0.0.1\\@fcm.googleapis.com/x`` 在 urlsplit 看來主機是
fcm.googleapis.com，但 pywebpush → requests → urllib3 實際會連到 127.0.0.1。
這類 endpoint 必須在訂閱時被拒絕，已存好的也要在送出前被刪除、絕不送出。
"""

from __future__ import annotations

import socket
import sys
import uuid
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from urllib3.util import parse_url

from app.models import PushSubscription
from app.schemas.push import PushSubscriptionCreate, is_allowed_push_endpoint
from app.services.notification import web_push_service
from app.services.notification.push_policy import test_message as push_test_message
from app.services.notification.web_push_service import EndpointVerdict

AMBIGUOUS = [
    # 反斜線：urlsplit 取 @ 後面的主機，urllib3 在反斜線處截斷、連到前面的主機
    "https://127.0.0.1:8006\\@fcm.googleapis.com/fcm/send/x",
    "https://attacker.example:8443\\@fcm.googleapis.com/x",
    "https://127.0.0.1\\@fcm.googleapis.com/x",
    "https://internal.example\\@fcm.googleapis.com/x",
    # 反斜線讓 urlsplit 的主機以 .push.apple.com 結尾，urllib3 卻連到 127.0.0.1
    "https://127.0.0.1\\.push.apple.com/x",
    # userinfo：真正的推播 endpoint 不會帶帳密
    "https://user@fcm.googleapis.com/x",
    "https://user:pw@fcm.googleapis.com/fcm/send/x",
    "https://127.0.0.1:8006%5C@fcm.googleapis.com/x",
    # 空白與控制字元：urlsplit 會默默刪掉 tab／換行，urllib3 則照原樣編碼
    "https://fcm.googleapis.com /x",
    "https://evil\tx@fcm.googleapis.com/x",
    "https://fcm.goog\nleapis.com/x",
    "https://fcm.googleapis.com\x00.evil/x",
    # 非 ASCII 主機：urlsplit 的主機以白名單後綴結尾，urllib3 會轉成 punycode
    "https://ä.push.apple.com/x",
]


def _keys() -> dict[str, str]:
    return {"p256dh": "k", "auth": "a"}


@pytest.mark.parametrize("endpoint", AMBIGUOUS)
def test_ambiguous_endpoint_is_rejected_at_subscribe(endpoint: str) -> None:
    assert not is_allowed_push_endpoint(endpoint)
    with pytest.raises(ValidationError):
        PushSubscriptionCreate(endpoint=endpoint, keys=_keys())


@pytest.mark.parametrize(
    ("endpoint", "expected_host"),
    [
        ("https://fcm.googleapis.com/fcm/send/abc", "fcm.googleapis.com"),
        ("https://fcm.googleapis.com:443/fcm/send/abc", "fcm.googleapis.com"),
        ("https://FCM.googleapis.com./fcm/send/abc", "fcm.googleapis.com"),
        (
            "https://updates.push.services.mozilla.com/wpush/v2/abc",
            "updates.push.services.mozilla.com",
        ),
        (
            "https://wns2-par02p.notify.windows.com/w/?token=abc",
            "wns2-par02p.notify.windows.com",
        ),
        ("https://web.push.apple.com/QGf3abc", "web.push.apple.com"),
    ],
)
def test_accepted_endpoint_connects_to_the_validated_host(
    endpoint: str, expected_host: str
) -> None:
    """通過驗證的 endpoint，urllib3 實際連線的就是白名單內的推播服務主機與 443 埠。"""
    body = PushSubscriptionCreate(endpoint=endpoint, keys=_keys())
    parsed = parse_url(body.endpoint)
    assert parsed.auth is None
    assert parsed.port in (None, 443)
    assert (parsed.host or "").lower().rstrip(".") == expected_host


# ─── 送出端：修正前就存好的訂閱 ─────────────────────────────────────────────


def _must_not_resolve(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("should not resolve")


def _public_resolver(host: str, port: Any, *args: Any, **kwargs: Any) -> Any:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.10", 443))]


class _FakeSession:
    def add(self, obj: Any) -> None:
        pass

    def commit(self) -> None:
        pass


@pytest.fixture
def webpush_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """換掉 pywebpush／py_vapid 與設定讀取，記錄 webpush() 是否被呼叫。"""
    calls: list[dict[str, Any]] = []

    fake_pywebpush = ModuleType("pywebpush")

    class WebPushException(Exception):
        pass

    def webpush(**kwargs: Any) -> None:
        calls.append(kwargs)

    fake_pywebpush.WebPushException = WebPushException  # type: ignore[attr-defined]
    fake_pywebpush.webpush = webpush  # type: ignore[attr-defined]
    fake_vapid = ModuleType("py_vapid")
    fake_vapid.Vapid = SimpleNamespace(from_pem=lambda pem: "vapid")  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "pywebpush", fake_pywebpush)
    monkeypatch.setitem(sys.modules, "py_vapid", fake_vapid)
    monkeypatch.setattr(
        web_push_service.push_repo,
        "get_web_push_config",
        lambda *, session: SimpleNamespace(subject="mailto:x@y"),
    )
    monkeypatch.setattr(
        web_push_service.push_repo,
        "get_vapid_private_key",
        lambda *, session, config: "PEM",
    )
    return calls


@pytest.fixture
def removed_ids(monkeypatch: pytest.MonkeyPatch) -> list[list[uuid.UUID]]:
    removed: list[list[uuid.UUID]] = []
    monkeypatch.setattr(
        web_push_service.push_repo,
        "delete_subscriptions_by_ids",
        lambda *, session, ids: removed.append(list(ids)) or len(ids),
    )
    return removed


def _subscription(endpoint: str) -> PushSubscription:
    return PushSubscription(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        endpoint=endpoint,
        p256dh="p",
        auth="a",
        language="zh-TW",
        failure_count=0,
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1\\@fcm.googleapis.com/x",
        "https://127.0.0.1:8006\\@fcm.googleapis.com/fcm/send/x",
        "https://127.0.0.1\\.push.apple.com/x",
        "https://user:pw@fcm.googleapis.com/fcm/send/x",
    ],
)
def test_check_endpoint_blocks_ambiguous_endpoint_without_resolving(
    monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _must_not_resolve)
    assert web_push_service.check_endpoint(endpoint) is EndpointVerdict.BLOCKED


def test_stored_backslash_endpoint_is_deleted_and_never_sent(
    monkeypatch: pytest.MonkeyPatch,
    webpush_calls: list[dict[str, Any]],
    removed_ids: list[list[uuid.UUID]],
) -> None:
    # fcm.googleapis.com 解析到公網，舊的 DNS 檢查會放行；白名單檢查必須先擋下
    monkeypatch.setattr(socket, "getaddrinfo", _public_resolver)
    bad = _subscription("https://127.0.0.1:8006\\@fcm.googleapis.com/fcm/send/x")
    good = _subscription("https://fcm.googleapis.com/fcm/send/abc")
    report = web_push_service.send_messages(
        session=_FakeSession(),  # type: ignore[arg-type]
        subscriptions=[bad, good],
        message_for_language=push_test_message("zh-TW"),
    )
    assert report.sent == 1
    assert report.removed == 1
    assert removed_ids == [[bad.id]]
    assert [call["subscription_info"]["endpoint"] for call in webpush_calls] == [
        good.endpoint
    ]
