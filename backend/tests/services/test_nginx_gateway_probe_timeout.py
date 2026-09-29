"""probe_health 對 SSH 讀取加上逾時，讓每個呼叫端不需自行包裝也不會被卡住的主機拖住。"""

from __future__ import annotations

from typing import Any

from app.services.network import nginx_gateway_service


class _Channel:
    def __init__(self, data: bytes = b"") -> None:
        self._data = data
        self.channel = self

    def read(self) -> bytes:
        return self._data

    def recv_exit_status(self) -> int:
        return 0

    def write(self, _data: str) -> None:  # pragma: no cover - 不走 stdin
        pass

    def shutdown_write(self) -> None:  # pragma: no cover - 不走 stdin
        pass


class _Client:
    def __init__(self) -> None:
        self.timeouts: list[Any] = []

    def exec_command(self, command: str, timeout: Any = None) -> tuple[Any, Any, Any]:
        self.timeouts.append(timeout)
        return _Channel(), _Channel(b""), _Channel(b"")


def test_probe_health_defaults_to_bounded_read_timeout() -> None:
    client = _Client()
    nginx_gateway_service.probe_health(client, wireguard_unit="wg-quick@wg0")
    assert client.timeouts == [10]


def test_probe_health_timeout_is_overridable() -> None:
    client = _Client()
    nginx_gateway_service.probe_health(
        client, wireguard_unit="wg-quick@wg0", timeout=3
    )
    assert client.timeouts == [3]
