"""Coverage for CLI entry points: app.initial_data, app.reset_totp and app.backend_pre_start."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app import backend_pre_start, initial_data
from app import reset_totp as reset_totp_cli


class _FakeSession:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1


# ---------------------------------------------------------------- initial_data


def test_initial_data_main_runs_init_db(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(initial_data, "Session", _FakeSession)
    monkeypatch.setattr(initial_data, "init_db", lambda session: seen.append(session))

    initial_data.main()

    assert len(seen) == 1
    assert isinstance(seen[0], _FakeSession)


# ---------------------------------------------------------------- reset_totp


def test_reset_totp_unknown_user_returns_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reset_totp_cli, "Session", _FakeSession)
    monkeypatch.setattr(
        reset_totp_cli.user_repo, "get_user_by_email", lambda **_kwargs: None
    )

    assert reset_totp_cli.reset_totp("ghost@example.com") == 1


def test_reset_totp_user_without_totp_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    user = SimpleNamespace(
        totp_enabled=False, totp_secret_encrypted=None, token_version=3
    )
    sessions: list[_FakeSession] = []

    def _session(*args: Any, **kwargs: Any) -> _FakeSession:
        session = _FakeSession()
        sessions.append(session)
        return session

    monkeypatch.setattr(reset_totp_cli, "Session", _session)
    monkeypatch.setattr(
        reset_totp_cli.user_repo, "get_user_by_email", lambda **_kwargs: user
    )

    assert reset_totp_cli.reset_totp("a@example.com") == 0
    assert user.token_version == 3
    assert sessions[0].commits == 0


@pytest.mark.parametrize("argv", [["reset_totp.py"], ["reset_totp.py", "a", "b"]])
def test_reset_totp_main_usage_error(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc_info:
        reset_totp_cli.main()
    assert exc_info.value.code == 2


def test_reset_totp_main_strips_email_and_exits_with_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _fake_reset(email: str) -> int:
        calls.append(email)
        return 1

    monkeypatch.setattr(sys, "argv", ["reset_totp.py", "  admin@example.com "])
    monkeypatch.setattr(reset_totp_cli, "reset_totp", _fake_reset)
    with pytest.raises(SystemExit) as exc_info:
        reset_totp_cli.main()
    assert exc_info.value.code == 1
    assert calls == ["admin@example.com"]


# ---------------------------------------------------------------- backend_pre_start


def test_backend_pre_start_init_logs_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _broken_session(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("db not awake")

    errors: list[Any] = []
    monkeypatch.setattr(backend_pre_start, "Session", _broken_session)
    monkeypatch.setattr(backend_pre_start.logger, "error", errors.append)

    # 直接呼叫未經 tenacity 包裝的函式，避免 300 次 × 1 秒的重試
    with pytest.raises(RuntimeError, match="db not awake"):
        backend_pre_start.init.__wrapped__(MagicMock())  # type: ignore[attr-defined]
    assert len(errors) == 1


def test_backend_pre_start_main_calls_init(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(backend_pre_start, "init", lambda engine: seen.append(engine))

    backend_pre_start.main()

    assert seen == [backend_pre_start.engine]
