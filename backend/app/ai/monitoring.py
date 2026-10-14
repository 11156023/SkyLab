"""Shared helpers for recording built-in AI feature usage."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import TracebackType
from typing import Any

from sqlmodel import Session

from app.core import metrics as core_metrics
from app.core.db import engine
from app.core.request_context import get_request_context
from app.services.llm_gateway import ai_gateway_service
from app.services.llm_gateway.usage_writer import get_usage_writer
from app.services.monitoring import ai_metrics

CALL_AI_NAVIGATION = "ai_nav"
CALL_AI_CONTEXTUAL_HELP = "ai_help"
logger = logging.getLogger(__name__)


def new_ai_request_id() -> str:
    return str(uuid.uuid4())


@dataclass
class AIWorkflowObservation:
    """One log summary per System AI workflow; model accounting stays per call."""

    route: str
    model: str
    outcome: str = "success"
    terminal_reason: str = "reply"
    started: float = field(default_factory=lambda: time.perf_counter())
    request_id: str = field(
        default_factory=lambda: get_request_context().request_id or new_ai_request_id()
    )
    calls: dict[str, int] = field(
        default_factory=lambda: {"model": 0, "adherence": 0, "tool": 0}
    )
    seconds: dict[str, float] = field(
        default_factory=lambda: {"model": 0.0, "adherence": 0.0, "tool": 0.0}
    )
    call_request_ids: list[str] = field(default_factory=list)

    def __enter__(self) -> AIWorkflowObservation:
        return self

    @contextmanager
    def phase(self, stage: str, *, request_id: str | None = None) -> Iterator[None]:
        self.calls[stage] += 1
        if request_id is not None:
            self.call_request_ids.append(request_id)
        started = time.perf_counter()
        try:
            yield
        except Exception:
            self.outcome = "error"
            self.terminal_reason = f"{stage}_error"
            raise
        finally:
            self.seconds[stage] += max(0.0, time.perf_counter() - started)

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if isinstance(exc, asyncio.CancelledError):
            self.outcome, self.terminal_reason = "cancelled", "cancelled"
        elif exc is not None:
            self.outcome, self.terminal_reason = "error", "error"
        try:
            summary = {
                "event": "ai_workflow_finished",
                "workflow_request_id": self.request_id,
                "route": self.route,
                "source": "platform",
                "model": ai_metrics.model_label(self.model, served=False),
                "outcome": self.outcome,
                "terminal_reason": self.terminal_reason,
                "workflow_duration_ms": round(
                    max(0.0, time.perf_counter() - self.started) * 1000, 3
                ),
                "call_request_ids": self.call_request_ids,
                **{f"{stage}_calls": count for stage, count in self.calls.items()},
                **{
                    f"{stage}_ms": round(seconds * 1000, 3)
                    for stage, seconds in self.seconds.items()
                },
            }
        except Exception:
            # Constructing observation data is also best-effort.
            return
        try:
            core_metrics.AI_WORKFLOWS.labels(
                route=self.route,
                outcome=self.outcome,
                terminal_reason=self.terminal_reason,
            ).inc()
        except Exception:
            pass
        try:
            logger.info("AI workflow finished", extra=summary)
        except Exception:
            # Observation failure must never change the feature response.
            pass


def usage_metrics(
    response_data: dict[str, Any],
    elapsed: float,
    *,
    request_id: str | None = None,
    started_at: datetime | None = None,
) -> dict[str, Any]:
    """從 OpenAI 相容回應的 ``usage`` 區塊整理 token 數與耗時（缺值一律視為 0）。"""
    raw_usage = response_data.get("usage")
    usage_reported = isinstance(raw_usage, dict)
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    completed_at = datetime.now(timezone.utc)
    effective_elapsed = max(elapsed, 0.0)
    return {
        "request_id": request_id,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": int(
            usage.get("total_tokens") or prompt_tokens + completion_tokens
        ),
        "elapsed_seconds": round(effective_elapsed, 3),
        "usage_reported": usage_reported,
        "response_model": str(response_data.get("model") or "")[:255] or None,
        "started_at": started_at or completed_at - timedelta(seconds=effective_elapsed),
        "completed_at": completed_at,
    }


def _token_count(metrics: Mapping[str, Any], key: str) -> int:
    try:
        return int(metrics.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _duration_ms(metrics: Mapping[str, Any] | None) -> int | None:
    if not metrics:
        return None
    elapsed = metrics.get("elapsed_seconds")
    if elapsed is None:
        return None
    try:
        return int(round(float(elapsed) * 1000))
    except (TypeError, ValueError):
        return None


def _truncate_error(error_message: str | None) -> str | None:
    if not error_message:
        return None
    return error_message[:1000]


def record_ai_template_call(
    *,
    session: Session | None,
    user_id: uuid.UUID | None,
    call_type: str,
    model_name: str | None,
    preset: str | None = None,
    metrics: Mapping[str, Any] | None = None,
    status: str = "success",
    error_message: str | None = None,
) -> None:
    """Best-effort usage logging for platform-owned LLM calls."""
    ai_metrics.observe_call(
        source="platform",
        model=model_name,
        request_type=call_type,
        record_status=status,
        error_message=error_message,
        duration_ms=_duration_ms(metrics),
        input_tokens=_token_count(metrics or {}, "prompt_tokens"),
        output_tokens=_token_count(metrics or {}, "completion_tokens"),
        stream=bool((metrics or {}).get("stream", False)),
    )
    if session is None or user_id is None:
        return
    # Snapshot all inputs before leaving the caller. Never capture its Session.
    values = dict(metrics or {})
    kwargs = {
        "user_id": user_id,
        "call_type": call_type,
        "model_name": (model_name or "unknown")[:255],
        "preset": preset,
        "request_id": str(values.get("request_id") or new_ai_request_id())[:255],
        "input_tokens": _token_count(values, "prompt_tokens"),
        "output_tokens": _token_count(values, "completion_tokens"),
        "request_duration_ms": _duration_ms(values),
        "stream": bool(values.get("stream", False)),
        "usage_reported": bool(values.get("usage_reported", False)),
        "response_model": str(values.get("response_model") or "")[:255] or None,
        "status": status,
        "error_message": _truncate_error(error_message),
        "started_at": values.get("started_at"),
        "completed_at": values.get("completed_at"),
    }

    def write() -> None:
        with Session(engine) as usage_session:
            ai_gateway_service.record_template_call(session=usage_session, **kwargs)

    get_usage_writer().submit(write, source="platform")
