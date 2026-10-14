"""app.services.classroom.broadcast_hub 的基底類別分支測試。"""

import pytest

from app.services.classroom.broadcast_hub import JsonBroadcastHub


def test_base_send_timeout_must_be_overridden() -> None:
    hub: JsonBroadcastHub = JsonBroadcastHub()
    with pytest.raises(NotImplementedError):
        hub._send_timeout()


async def test_send_to_without_connections_is_noop() -> None:
    hub: JsonBroadcastHub = JsonBroadcastHub()
    await hub._send_to([], {"type": "ping"})
    assert hub._connections == {}
