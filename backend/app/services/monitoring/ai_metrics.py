"""AI 呼叫的 Prometheus 指標（``skylab_ai_*``）。

使用者 API 金鑰（``source=api_key``）與內建 AI 功能（``source=platform``）的每次
呼叫在寫用量紀錄時一併更新指標；Grafana「SkyLab AI」儀表板用這些指標看 Campus
這一側的請求量、錯誤、延遲與 token，vLLM 引擎本身的佇列／KV cache 則直接抓 vLLM。

``model`` 是呼叫端填的字串，不能直接當 label：只有 LiteLLM 成功服務過、或 AI
健康探測從 LiteLLM 查到的模型名稱才保留，其餘歸成 ``other``（上限
``MAX_MODEL_LABELS``），時間序列數量才有上限。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable

from app.core import metrics

logger = logging.getLogger(__name__)

MAX_MODEL_LABELS = 100
OTHER_MODEL = "other"
UNKNOWN_MODEL = "unknown"

_lock = threading.Lock()
_known_models: set[str] = set()


def _clean(model: str | None) -> str:
    return (model or "").strip()[:100]


def remember_models(names: Iterable[str]) -> None:
    """AI 健康探測查到的模型：之後即使呼叫失敗也用真名當 label。"""
    with _lock:
        for name in names:
            cleaned = _clean(name)
            if cleaned and len(_known_models) < MAX_MODEL_LABELS:
                _known_models.add(cleaned)


def model_label(model: str | None, *, served: bool) -> str:
    """呼叫端的 model → 指標 label。``served``：LiteLLM 成功回應（alias 一定有效）。"""
    name = _clean(model)
    if not name:
        return UNKNOWN_MODEL
    with _lock:
        if name in _known_models:
            return name
        if served and len(_known_models) < MAX_MODEL_LABELS:
            _known_models.add(name)
            return name
    return OTHER_MODEL


def reset_known_models() -> None:
    """測試用。"""
    with _lock:
        _known_models.clear()


def outcome(record_status: str | None, error_message: str | None) -> str:
    """用量紀錄的 status／error_message → 少數幾種結果類別。"""
    if record_status == "success":
        return "success"
    if record_status == "cancelled":
        return "cancelled"
    message = error_message or ""
    if message == "upstream_unavailable":
        return "unavailable"
    if message == "upstream_stream_error":
        return "stream_error"
    if message.startswith("upstream_http_"):
        code = message.removeprefix("upstream_http_")
        if code == "429":
            return "rate_limited"
        if code.startswith("4"):
            return "client_error"
        if code.startswith("5"):
            return "upstream_error"
    return "error"


def observe_call(
    *,
    source: str,
    model: str | None,
    request_type: str,
    record_status: str | None,
    error_message: str | None = None,
    duration_ms: int | None = None,
    first_token_ms: int | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    stream: bool = False,
) -> None:
    """一次 AI 呼叫結束時更新指標；指標出錯絕不影響呼叫本身。"""
    try:
        result = outcome(record_status, error_message)
        label = model_label(model, served=result == "success")
        metrics.AI_REQUESTS.labels(
            source=source, model=label, request_type=request_type, outcome=result
        ).inc()
        if duration_ms is not None and result != "cancelled":
            metrics.AI_REQUEST_DURATION.labels(
                source=source, model=label, stream="true" if stream else "false"
            ).observe(max(duration_ms, 0) / 1000)
        if first_token_ms is not None:
            metrics.AI_TIME_TO_FIRST_TOKEN.labels(source=source, model=label).observe(
                max(first_token_ms, 0) / 1000
            )
        if input_tokens > 0:
            metrics.AI_TOKENS.labels(source=source, model=label, direction="input").inc(
                input_tokens
            )
        if output_tokens > 0:
            metrics.AI_TOKENS.labels(source=source, model=label, direction="output").inc(
                output_tokens
            )
    except Exception:
        logger.debug("Updating AI call metrics failed", exc_info=True)


__all__ = [
    "MAX_MODEL_LABELS",
    "OTHER_MODEL",
    "model_label",
    "observe_call",
    "outcome",
    "remember_models",
    "reset_known_models",
]
