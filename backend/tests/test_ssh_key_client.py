"""Key-based SSH connection deadlines and cleanup on failed setup."""

from __future__ import annotations

from typing import Any

import pytest

from app.infrastructure.ssh import client as ssh


@pytest.mark.parametrize("failure_stage", [None, "host_key", "key", "connect"])
def test_key_client_bounds_channel_open_and_closes_failed_connections(
    monkeypatch: pytest.MonkeyPatch, failure_stage: str | None,
) -> None:
    calls: dict[str, Any] = {}

    class Client:
        def connect(self, **kwargs: Any) -> None:
            calls["connect"] = kwargs
            if failure_stage == "connect":
                raise TimeoutError("synthetic SSH timeout")

        def close(self) -> None:
            calls["closed"] = True

    client = Client()

    def host_key(_client: Any) -> None:
        if failure_stage == "host_key":
            raise TimeoutError("synthetic host key setup failure")

    def key(*args: Any) -> Any:
        if failure_stage == "key":
            raise TimeoutError("synthetic key setup failure")
        return "test-key-object"

    monkeypatch.setattr(ssh.paramiko, "SSHClient", lambda: client)
    monkeypatch.setattr(ssh, "_configure_host_key_verification", host_key)
    monkeypatch.setattr(ssh.paramiko.Ed25519Key, "from_private_key", key)
    if failure_stage:
        with pytest.raises(TimeoutError):
            ssh.create_key_client("127.0.0.1", 22, "root", "TEST_KEY", timeout=60)
        assert calls["closed"] is True
    else:
        assert ssh.create_key_client("127.0.0.1", 22, "root", "TEST_KEY", timeout=60) is client
        assert "closed" not in calls
        assert all(calls["connect"][field] == 60 for field in (
            "timeout", "banner_timeout", "auth_timeout", "channel_timeout",
        ))
        assert calls["connect"]["allow_agent"] is False
        assert calls["connect"]["look_for_keys"] is False
