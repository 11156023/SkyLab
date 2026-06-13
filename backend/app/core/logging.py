"""Logging configuration: colored console output + rotating file handlers.

Console: ANSI-colored human-readable format (disable with LOG_JSON=true).
Files:   JSON per line, rotated daily (app.log) and weekly (error.log).
         Stored under LOG_DIR (default: logs/).

Call ``configure_logging()`` once at app startup (idempotent).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.request_context import get_request_context

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName",
}

# ANSI escape codes
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_LEVEL_COLORS: dict[str, str] = {
    "DEBUG":    "\033[36m",    # cyan
    "INFO":     "\033[32m",    # green
    "WARNING":  "\033[33m",    # yellow
    "ERROR":    "\033[31m",    # red
    "CRITICAL": "\033[35;1m",  # bold magenta
}


class JsonFormatter(logging.Formatter):
    """Single-line JSON per log record — suitable for file output and log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        ctx = get_request_context()
        if ctx.ip_address:
            payload["ip_address"] = ctx.ip_address
        if ctx.user_agent:
            payload["user_agent"] = ctx.user_agent

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


class ColoredConsoleFormatter(logging.Formatter):
    """Human-readable colored console output using ANSI escape codes.

    Format: ``HH:MM:SS  LEVEL    logger.name - message``
    """

    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelname, "")
        ts = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S")
        level_tag = f"{color}{_BOLD}{record.levelname:<8}{_RESET}"
        logger_tag = f"{_DIM}\033[36m{record.name}{_RESET}"
        line = f"{_DIM}{ts}{_RESET}  {level_tag}  {logger_tag} - {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            line += "\n" + record.stack_info
        return line


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


class _ErrorOnlyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.ERROR


_configure_state = {"configured": False}


def configure_logging(
    *,
    level: str = "INFO",
    json_output: bool = False,
    log_dir: str = "logs",
    file_enabled: bool = True,
) -> None:
    """Install console + optional rotating file handlers.

    Safe to call multiple times — only the first call has effect.

    Args:
        level:        Root log level string (e.g. ``"INFO"``, ``"DEBUG"``).
        json_output:  Use JSON format on console (True) or ANSI color (False).
        log_dir:      Directory for log files.
        file_enabled: Whether to write log files at all.
    """
    if _configure_state["configured"]:
        return

    ws_filter = WebSocketNoiseFilter()

    # --- Console handler ---
    console = logging.StreamHandler(stream=sys.stdout)
    console.addFilter(ws_filter)
    console.setFormatter(
        JsonFormatter() if json_output else ColoredConsoleFormatter()
    )

    handlers: list[logging.Handler] = [console]

    # --- File handlers ---
    if file_enabled:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        json_fmt = JsonFormatter()

        # app.log — all levels, rotate at midnight UTC, keep 30 days
        app_h = logging.handlers.TimedRotatingFileHandler(
            log_path / "app.log",
            when="midnight",
            backupCount=30,
            encoding="utf-8",
            utc=True,
        )
        app_h.setFormatter(json_fmt)
        app_h.addFilter(ws_filter)
        handlers.append(app_h)

        # error.log — ERROR+ only, rotate every Monday, keep ~3 months
        error_h = logging.handlers.TimedRotatingFileHandler(
            log_path / "error.log",
            when="W0",
            backupCount=13,
            encoding="utf-8",
            utc=True,
        )
        error_h.setFormatter(json_fmt)
        error_h.addFilter(_ErrorOnlyFilter())
        handlers.append(error_h)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level.upper())

    logging.getLogger("uvicorn.access").setLevel("WARNING")
    logging.getLogger("uvicorn.error").addFilter(ws_filter)
    logging.getLogger("httpx").setLevel("WARNING")

    _configure_state["configured"] = True
