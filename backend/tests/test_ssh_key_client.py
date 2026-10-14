"""Key-based SSH connection deadlines and cleanup on failed setup."""

from __future__ import annotations

from typing import Any

import pytest

from app.infrastructure.ssh import client as ssh


@pytest.mark.parametrize("failure_stage", [None, "host_key", "key", "connect"])
@pytest.mark.parametrize("error_type", [TimeoutError, KeyboardInterrupt])
def test_key_client_bounds_channel_open_and_closes_failed_connections(
    monkeypatch: pytest.MonkeyPatch, failure_stage: str | None,
    error_type: type[BaseException],
) -> None:
    calls: dict[str, Any] = {}

    class Client:
        def connect(self, **kwargs: Any) -> None:
            calls["connect"] = kwargs
            if failure_stage == "connect":
                raise error_type("synthetic SSH failure")

        def close(self) -> None:
            calls["close_count"] = calls.get("close_count", 0) + 1

    client = Client()

    def host_key(_client: Any) -> None:
        if failure_stage == "host_key":
            raise error_type("synthetic host key setup failure")

    def key(*args: Any) -> Any:
        if failure_stage == "key":
            raise error_type("synthetic key setup failure")
        return "test-key-object"

    monkeypatch.setattr(ssh.paramiko, "SSHClient", lambda: client)
    monkeypatch.setattr(ssh, "_configure_host_key_verification", host_key)
    monkeypatch.setattr(ssh.paramiko.Ed25519Key, "from_private_key", key)
    if failure_stage:
        with pytest.raises(error_type):
            ssh.create_key_client("127.0.0.1", 22, "root", "TEST_KEY", timeout=60)
        assert calls["close_count"] == 1
    else:
        assert ssh.create_key_client("127.0.0.1", 22, "root", "TEST_KEY", timeout=60) is client
        assert "close_count" not in calls
        assert all(calls["connect"][field] == 60 for field in (
            "timeout", "banner_timeout", "auth_timeout", "channel_timeout",
        ))
        assert calls["connect"]["allow_agent"] is False
        assert calls["connect"]["look_for_keys"] is False
