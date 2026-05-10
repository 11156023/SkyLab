"""Structured JSON logging configuration.

Outputs one JSON object per log record with stable keys so they can be
ingested by Loki/CloudWatch/ELK without extra parsing.

Enable via ``configure_logging()`` at app startup (idempotent).

Optional fields ``request_id``, ``user_id``, ``ip_address`` are pulled from
the current ``request_context`` if available.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.request_context import get_request_context

# Standard logging attributes we don't want to duplicate inside `extra`.
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Attach request-scoped context if present.
        ctx = get_request_context()
        if ctx.ip_address:
            payload["ip_address"] = ctx.ip_address
        if ctx.user_agent:
            payload["user_agent"] = ctx.user_agent

        # Attach any extra fields passed via logger.info(..., extra={...}).
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            if key in payload:
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info

        return json.dumps(payload, ensure_ascii=False, default=str)


class WebSocketNoiseFilter(logging.Filter):
    """Hide successful WebSocket connection chatter while keeping errors."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        message = record.getMessage()
        if record.name == "uvicorn.error":
            if message in {"connection open", "connection closed"}:
                return False
            if "WebSocket /ws/" in message and "[accepted]" in message:
                return False
        return True


_CONFIGURED = False


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Install a single stdout handler with JSON or plain formatting.

    Safe to call multiple times — only the first call has effect.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    websocket_noise_filter = WebSocketNoiseFilter()
    handler.addFilter(websocket_noise_filter)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s - %(message)s"
            )
        )

    root = logging.getLogger()
    # Replace any default handlers (e.g. uvicorn's default) so all logs share format.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Tame noisy third-party loggers.
    logging.getLogger("uvicorn.access").setLevel("WARNING")
    logging.getLogger("uvicorn.error").addFilter(websocket_noise_filter)
    logging.getLogger("httpx").setLevel("WARNING")

    _CONFIGURED = True
