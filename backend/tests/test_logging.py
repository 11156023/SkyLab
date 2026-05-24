"""Tests for app.core.logging — JsonFormatter behaviour.

Pure unit tests; do not call configure_logging() because it mutates the
global root handler. We exercise the formatter directly.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.core.logging import JsonFormatter, WebSocketNoiseFilter, configure_logging
from app.core.request_context import (
    RequestContext,
    set_request_context,
)


@pytest.fixture
def formatter() -> JsonFormatter:
    return JsonFormatter()


def _make_record(
    *,
    name: str = "app.test",
    level: int = logging.INFO,
    msg: str = "hello",
    args: tuple = (),
    extra: dict | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=10,
        msg=msg,
        args=args,
        exc_info=None,
    )
    if extra:
        for key, value in extra.items():
            setattr(record, key, value)
    return record


def test_formatter_emits_valid_json(formatter: JsonFormatter) -> None:
    record = _make_record(msg="ping")
    out = formatter.format(record)
    payload = json.loads(out)
    assert payload["message"] == "ping"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert "timestamp" in payload


def test_formatter_renders_message_args(formatter: JsonFormatter) -> None:
    record = _make_record(msg="hello %s", args=("world",))
    payload = json.loads(formatter.format(record))
    assert payload["message"] == "hello world"


def test_formatter_includes_extra_fields(formatter: JsonFormatter) -> None:
    record = _make_record(extra={"user_id": "abc-123", "vmid": 100})
    payload = json.loads(formatter.format(record))
    assert payload["user_id"] == "abc-123"
    assert payload["vmid"] == 100


def test_formatter_handles_non_json_serializable_extra(
    formatter: JsonFormatter,
) -> None:
    """Non-JSON-safe extras get repr()'d instead of crashing."""

    class Custom:
        def __repr__(self) -> str:
            return "<Custom!>"

    record = _make_record(extra={"obj": Custom()})
    payload = json.loads(formatter.format(record))
    assert payload["obj"] == "<Custom!>"


def test_formatter_attaches_request_context(formatter: JsonFormatter) -> None:
    set_request_context(
        RequestContext(ip_address="1.2.3.4", user_agent="pytest-ua/1.0"),
    )
    try:
        record = _make_record()
        payload = json.loads(formatter.format(record))
        assert payload["ip_address"] == "1.2.3.4"
        assert payload["user_agent"] == "pytest-ua/1.0"
    finally:
        # Reset for other tests
        set_request_context(RequestContext())


def test_formatter_omits_context_when_empty(formatter: JsonFormatter) -> None:
    set_request_context(RequestContext())
    record = _make_record()
    payload = json.loads(formatter.format(record))
    assert "ip_address" not in payload
    assert "user_agent" not in payload


def test_formatter_includes_exception_info(formatter: JsonFormatter) -> None:
    try:
        raise ValueError("kaboom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="app.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    payload = json.loads(formatter.format(record))
    assert payload["level"] == "ERROR"
    assert "ValueError" in payload["exception"]
    assert "kaboom" in payload["exception"]


def test_websocket_noise_filter_hides_successful_uvicorn_ws_logs() -> None:
    filter_ = WebSocketNoiseFilter()

    accepted = _make_record(
        name="uvicorn.error",
        msg='127.0.0.1:57100 - "WebSocket /ws/jobs?token=abc" [accepted]',
    )
    opened = _make_record(name="uvicorn.error", msg="connection open")
    error = _make_record(
        name="uvicorn.error",
        level=logging.ERROR,
        msg="WebSocket /ws/jobs failed",
    )

    assert filter_.filter(accepted) is False
    assert filter_.filter(opened) is False
    assert filter_.filter(error) is True


def test_configure_logging_is_idempotent() -> None:
    """Calling configure_logging twice should not stack handlers."""
    configure_logging(level="WARNING", json_output=False, file_enabled=False)
    root = logging.getLogger()
    handlers_after_first = len(root.handlers)

    configure_logging(level="DEBUG", json_output=True, file_enabled=False)
    handlers_after_second = len(root.handlers)

    assert handlers_after_first == handlers_after_second
