"""Web Push 送出前的 endpoint 解析檢查（DNS rebinding／舊訂閱的 SSRF 防線）。"""

from __future__ import annotations

import socket
import sys
import uuid
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from app.models import PushSubscription
from app.services.notification import web_push_service
from app.services.notification.push_policy import test_message as push_test_message
from app.services.notification.web_push_service import EndpointVerdict


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        pass


def _subscription(endpoint: str, **overrides: Any) -> PushSubscription:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "endpoint": endpoint,
        "p256dh": "p",
        "auth": "a",
        "language": "zh-TW",
        "failure_count": 0,
    }
    base.update(overrides)
    return PushSubscription(**base)


def _resolver(*addresses: str) -> Any:
    def resolve(host: str, port: Any, *args: Any, **kwargs: Any) -> Any:
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, 443),
            )
            for address in addresses
        ]

    return resolve


def _failing_resolver(host: str, port: Any, *args: Any, **kwargs: Any) -> Any:
    raise socket.gaierror("name does not resolve")


@pytest.fixture
def webpush_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """換掉 pywebpush／py_vapid，記錄 webpush() 實際被呼叫的次數與參數。"""
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


def _send(*subs: PushSubscription) -> Any:
    return web_push_service.send_messages(
        session=_FakeSession(),  # type: ignore[arg-type]
        subscriptions=list(subs),
        message_for_language=push_test_message("zh-TW"),
    )


def _must_not_resolve(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("should not resolve")


# ─── check_endpoint ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "addresses",
    [
        ("127.0.0.1",),
        ("10.0.0.5",),
        ("192.168.100.20",),
        ("169.254.169.254",),
        ("100.64.0.1",),
        ("0.0.0.0",),
        ("224.0.0.1",),
        ("::1",),
        ("fe80::1%eth0",),
        ("::ffff:10.0.0.5",),
        # 只要有一個解析結果落在內網就封鎖
        ("142.250.72.10", "10.0.0.5"),
    ],
)
def test_internal_resolution_is_blocked(
    monkeypatch: pytest.MonkeyPatch, addresses: tuple[str, ...]
) -> None:
    # 白名單內的推播服務網域被改指向內網（DNS rebinding）
    monkeypatch.setattr(socket, "getaddrinfo", _resolver(*addresses))
    assert (
        web_push_service.check_endpoint("https://fcm.googleapis.com/fcm/send/abc")
        is EndpointVerdict.BLOCKED
    )


def test_public_resolution_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", _resolver("142.250.72.10", "2607:f8b0:4004:c07::5f")
    )
    assert (
        web_push_service.check_endpoint("https://fcm.googleapis.com/fcm/send/abc")
        is EndpointVerdict.ALLOWED
    )


def test_resolution_failure_is_not_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _failing_resolver)
    assert (
        web_push_service.check_endpoint(
            "https://updates.push.services.mozilla.com/wpush/v2/x"
        )
        is EndpointVerdict.UNRESOLVED
    )


def test_non_allowlisted_host_is_blocked_without_resolving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """白名單建立前存好的訂閱：主機不是推播服務，連解析都不做就封鎖。"""
    monkeypatch.setattr(socket, "getaddrinfo", _must_not_resolve)
    assert (
        web_push_service.check_endpoint("https://rebind.example.com/x")
        is EndpointVerdict.BLOCKED
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/x",
        # 非 443 埠：getaddrinfo 用 443 解析，但 pywebpush 會照 URL 的埠送出
        "https://fcm.googleapis.com:8080/x",
        # 殘缺的 IPv6 字面值會讓 urlsplit 丟 ValueError，不能漏出去
        "https://[::1/x",
        # 超出範圍的埠號會讓 .port 丟 ValueError
        "https://fcm.googleapis.com:99999/x",
    ],
)
def test_invalid_endpoint_is_blocked_without_resolving(
    monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _must_not_resolve)
    assert web_push_service.check_endpoint(endpoint) is EndpointVerdict.BLOCKED


# ─── send_messages ──────────────────────────────────────────────────────────


def test_rebound_endpoint_is_not_sent_and_subscription_removed(
    monkeypatch: pytest.MonkeyPatch,
    webpush_calls: list[dict[str, Any]],
    removed_ids: list[list[uuid.UUID]],
) -> None:
    # 白名單內的網域在送出當下被改指向 loopback（DNS rebinding）
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("127.0.0.1"))
    sub = _subscription("https://fcm.googleapis.com/fcm/send/abc")
    report = _send(sub)
    assert webpush_calls == []
    assert report.sent == 0
    assert report.removed == 1
    assert removed_ids == [[sub.id]]


def test_private_resolution_is_not_sent(
    monkeypatch: pytest.MonkeyPatch,
    webpush_calls: list[dict[str, Any]],
    removed_ids: list[list[uuid.UUID]],
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("10.0.0.5"))
    report = _send(
        _subscription("https://updates.push.services.mozilla.com/wpush/v2/x")
    )
    assert webpush_calls == []
    assert report.sent == 0
    assert report.removed == 1


def test_non_allowlisted_public_endpoint_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
    webpush_calls: list[dict[str, Any]],
    removed_ids: list[list[uuid.UUID]],
) -> None:
    """修正前存好的訂閱：主機解析到公網，但不是推播服務，一樣不送並刪除。"""
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("93.184.216.34"))
    sub = _subscription("https://rebind.example.com/x")
    report = _send(sub)
    assert webpush_calls == []
    assert report.sent == 0
    assert report.removed == 1
    assert removed_ids == [[sub.id]]


def test_stored_non_443_port_endpoint_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
    webpush_calls: list[dict[str, Any]],
    removed_ids: list[list[uuid.UUID]],
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("142.250.72.10"))
    sub = _subscription("https://fcm.googleapis.com:8080/fcm/send/abc")
    report = _send(sub)
    assert webpush_calls == []
    assert report.removed == 1
    assert removed_ids == [[sub.id]]


def test_malformed_endpoint_does_not_abort_the_batch(
    monkeypatch: pytest.MonkeyPatch,
    webpush_calls: list[dict[str, Any]],
    removed_ids: list[list[uuid.UUID]],
) -> None:
    """殘缺的 endpoint 不能讓整批送出中斷：其他訂閱照送、壞的那筆被刪。"""
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("142.250.72.10"))
    bad = _subscription("https://[::1/x")
    good = _subscription("https://fcm.googleapis.com/fcm/send/abc")
    report = _send(bad, good)
    assert report.sent == 1
    assert report.removed == 1
    assert removed_ids == [[bad.id]]
    assert len(webpush_calls) == 1


def test_unresolved_endpoint_is_skipped_and_counted_as_failure(
    monkeypatch: pytest.MonkeyPatch,
    webpush_calls: list[dict[str, Any]],
    removed_ids: list[list[uuid.UUID]],
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _failing_resolver)
    sub = _subscription("https://fcm.googleapis.com/fcm/send/abc")
    report = _send(sub)
    assert webpush_calls == []
    assert report.sent == 0
    assert report.removed == 0
    assert removed_ids == []
    assert sub.failure_count == 1


def test_public_endpoint_is_sent(
    monkeypatch: pytest.MonkeyPatch,
    webpush_calls: list[dict[str, Any]],
    removed_ids: list[list[uuid.UUID]],
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("142.250.72.10"))
    report = _send(_subscription("https://fcm.googleapis.com/fcm/send/abc"))
    assert report.sent == 1
    assert report.removed == 0
    assert len(webpush_calls) == 1
    assert (
        webpush_calls[0]["subscription_info"]["endpoint"]
        == "https://fcm.googleapis.com/fcm/send/abc"
    )
