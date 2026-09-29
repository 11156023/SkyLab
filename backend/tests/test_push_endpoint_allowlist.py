"""推播 endpoint 只接受已知瀏覽器推播服務的網域（SSRF 防護）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.push import (
    PushSubscriptionCreate,
    is_allowed_push_endpoint,
    is_allowed_push_host,
)

ACCEPTED = [
    "https://fcm.googleapis.com/fcm/send/abc",
    "https://updates.push.services.mozilla.com/wpush/v2/abc",
    "https://wns2-par02p.notify.windows.com/w/?token=abc",
    "https://web.push.apple.com/QGf3abc",
    "https://FCM.googleapis.com./fcm/send/abc",
]

REJECTED = [
    "https://evil.example.com/x",
    "https://10.0.0.5.nip.io/x",
    "https://push.apple.com.evil.com/x",
    "https://notify.windows.com.attacker.net/x",
    "https://8.8.8.8/x",
    "https://fcm.googleapis.com:8443/x",
    "http://fcm.googleapis.com/x",
]


@pytest.mark.parametrize("endpoint", ACCEPTED)
def test_known_push_services_are_accepted(endpoint: str) -> None:
    assert is_allowed_push_endpoint(endpoint)
    body = PushSubscriptionCreate(endpoint=endpoint, keys={"p256dh": "k", "auth": "a"})
    assert body.endpoint == endpoint


@pytest.mark.parametrize("endpoint", REJECTED)
def test_unknown_hosts_are_rejected(endpoint: str) -> None:
    assert not is_allowed_push_endpoint(endpoint)
    with pytest.raises(ValidationError):
        PushSubscriptionCreate(endpoint=endpoint, keys={"p256dh": "k", "auth": "a"})


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("fcm.googleapis.com", True),
        ("web.push.apple.com", True),
        ("api.push.apple.com", True),
        ("push.apple.com", False),
        ("evilpush.apple.com", False),
        ("googleapis.com", False),
        ("notify.windows.com", False),
    ],
)
def test_is_allowed_push_host(host: str, expected: bool) -> None:
    assert is_allowed_push_host(host) is expected
