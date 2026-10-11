#!/usr/bin/env python3
"""Run a controlled multi-key load test against the public Campus AI API.

The runner intentionally does not create users or API credentials.  Prepare
short-lived ``ccai_*`` credentials on the remote test environment first, then
place them in the local, ignored ``.env.ai-api-load.local`` file:

    AI_API_PUBLIC_BASE_URL=https://example.test/api/v1
    CCAI_API_KEY_01=ccai_...
    CCAI_API_KEY_02=ccai_...

Run from the repository root (or use the default paths resolved from this
file):

    python scripts/test_ai_api_load.py --requests 100 --concurrency 10

The report is always written to ``test-results/ai-api-load/latest.json`` by
default.  Secrets are never printed or written to the report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = REPO_ROOT / ".env.ai-api-load.local"
DEFAULT_REPORT_FILE = REPO_ROOT / "test-results" / "ai-api-load" / "latest.json"
DEFAULT_PROMPT = "Reply with OK."
CCAI_KEY_NAME_RE = re.compile(r"^CCAI_API_KEY_(\d+)$")
CCAI_KEY_VALUE_RE = re.compile(r"^ccai_[A-Za-z0-9_-]+$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LoadConfigurationError(ValueError):
    """A local load-test configuration is missing or unsafe to use."""


class RemoteAPIError(RuntimeError):
    """The remote public API failed its preflight contract."""


@dataclass(frozen=True)
class Credential:
    """An in-memory credential reference; the value never enters a report."""

    index: int
    value: str


@dataclass(frozen=True)
class LoadCase:
    sequence: int
    model: str
    stream: bool
    credential: Credential


@dataclass(frozen=True)
class RequestResult:
    sequence: int
    model: str
    stream: bool
    key_index: int
    status_code: int | None
    ok: bool
    error: str | None
    error_detail: str | None
    duration_ms: int | None
    ttft_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    request_id: str | None
    scheduled_ms: int | None = None
    sent_ms: int | None = None
    finish_reason: str | None = None
    truncated: bool = False


class RequestCancelled(asyncio.CancelledError):
    """Propagate cancellation while retaining partial request evidence."""

    def __init__(self, result: RequestResult) -> None:
        super().__init__()
        self.result = result


def _unquote_dotenv(value: str, *, path: Path, line_number: int) -> str:
    """Parse the small dotenv subset needed by the local load file."""
    value = value.strip()
    if not value:
        return ""
    if value[0] not in {"'", '"'}:
        # A comment is recognized only when separated from the value by
        # whitespace; URLs and tokens containing '#' remain intact.
        return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()

    quote = value[0]
    closing_index = value.find(quote, 1)
    if closing_index < 0:
        raise LoadConfigurationError(
            f"{path}:{line_number}: quoted value is not closed"
        )
    remainder = value[closing_index + 1 :].strip()
    if remainder and not remainder.startswith("#"):
        raise LoadConfigurationError(
            f"{path}:{line_number}: unexpected characters after quoted value"
        )
    return value[1:closing_index]


def parse_dotenv(path: Path) -> dict[str, str]:
    """Read a minimal dotenv file without interpolation or secret logging."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise LoadConfigurationError(f"cannot read env file: {path}") from exc

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise LoadConfigurationError(f"{path}:{line_number}: expected KEY=value")
        raw_name, raw_value = line.split("=", 1)
        name = raw_name.strip()
        if not ENV_NAME_RE.fullmatch(name):
            raise LoadConfigurationError(
                f"{path}:{line_number}: invalid environment variable name"
            )
        if name in values:
            raise LoadConfigurationError(
                f"{path}:{line_number}: duplicate environment variable {name}"
            )
        values[name] = _unquote_dotenv(raw_value, path=path, line_number=line_number)
    return values


def load_local_values(
    env_file: Path, *, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Load the ignored file, with explicitly exported process values winning."""
    values = parse_dotenv(env_file) if env_file.is_file() else {}
    process_values = os.environ if environ is None else environ
    for name, value in process_values.items():
        if (
            name == "AI_API_PUBLIC_BASE_URL" or CCAI_KEY_NAME_RE.fullmatch(name)
        ) and value.strip():
            values[name] = value.strip()
    return values


def load_credentials(values: Mapping[str, str]) -> tuple[Credential, ...]:
    """Validate and return numbered ``ccai_*`` credentials in numeric order."""
    found: list[tuple[int, str, str]] = []
    seen_indexes: set[int] = set()
    seen_values: set[str] = set()

    for name, raw_value in values.items():
        match = CCAI_KEY_NAME_RE.fullmatch(name)
        if not match:
            continue
        index = int(match.group(1))
        if index < 1:
            raise LoadConfigurationError(
                f"{name} must use a positive numeric key index"
            )
        value = raw_value.strip()
        if index in seen_indexes:
            raise LoadConfigurationError(f"duplicate numeric key index: {index:02d}")
        if not CCAI_KEY_VALUE_RE.fullmatch(value):
            raise LoadConfigurationError(
                f"{name} must contain a valid ccai_* credential"
            )
        if value in seen_values:
            raise LoadConfigurationError(
                f"duplicate credential value at key index {index:02d}"
            )
        seen_indexes.add(index)
        seen_values.add(value)
        found.append((index, name, value))

    if len(found) < 2:
        raise LoadConfigurationError(
            "at least two CCAI_API_KEY_XX credentials are required"
        )
    found.sort(key=lambda item: item[0])
    return tuple(Credential(index=index, value=value) for index, _, value in found)


def public_proxy_url(base_url: str) -> str:
    """Validate the public Campus API root and append ``/ai-proxy`` once."""
    candidate = base_url.strip().rstrip("/")
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LoadConfigurationError(
            "AI_API_PUBLIC_BASE_URL must be an absolute http(s) URL"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LoadConfigurationError(
            "AI_API_PUBLIC_BASE_URL must not contain credentials, query, or fragment"
        )
    if candidate.endswith("/ai-proxy"):
        return candidate
    if not candidate.endswith("/api/v1"):
        raise LoadConfigurationError("AI_API_PUBLIC_BASE_URL must end with /api/v1")
    return f"{candidate}/ai-proxy"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _usage_fields(usage: Any) -> tuple[int | None, int | None, int | None]:
    if not isinstance(usage, dict):
        return None, None, None
    return (
        _int_or_none(usage.get("prompt_tokens")),
        _int_or_none(usage.get("completion_tokens")),
        _int_or_none(usage.get("total_tokens")),
    )


def _finish_reason(value: Any) -> str | None:
    if value is None:
        return None
    return (
        value
        if value in ("stop", "length", "tool_calls", "function_call", "content_filter")
        else "other"
    )


def _not_sent(case: LoadCase, index: int, arrival_rpm: float) -> RequestResult:
    return RequestResult(
        sequence=case.sequence,
        model=case.model,
        stream=case.stream,
        key_index=case.credential.index,
        status_code=None,
        ok=False,
        error="not_sent",
        error_detail=None,
        duration_ms=None,
        ttft_ms=None,
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        request_id=None,
        scheduled_ms=round(index * 60000 / arrival_rpm),
    )


def _result(
    *,
    case: LoadCase,
    status_code: int | None,
    ok: bool,
    error: str | None,
    error_detail: str | None,
    started_at: float,
    ttft_ms: int | None = None,
    usage: Any = None,
    request_id: str | None = None,
    finish_reason: str | None = None,
) -> RequestResult:
    prompt_tokens, completion_tokens, total_tokens = _usage_fields(usage)
    return RequestResult(
        sequence=case.sequence,
        model=case.model,
        stream=case.stream,
        key_index=case.credential.index,
        status_code=status_code,
        ok=ok,
        error=error,
        error_detail=error_detail,
        duration_ms=max(0, int((time.perf_counter() - started_at) * 1000)),
        ttft_ms=ttft_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        request_id=request_id,
        finish_reason=finish_reason,
        truncated=finish_reason == "length",
    )


def _request_payload(
    model: str, *, stream: bool, max_tokens: int, prompt: str = DEFAULT_PROMPT
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": stream,
    }


async def _request_one(
    client: httpx.AsyncClient,
    proxy_url: str,
    case: LoadCase,
    *,
    max_tokens: int,
    secrets: Sequence[str],
    prompt: str = DEFAULT_PROMPT,
) -> RequestResult:
    started_at = time.perf_counter()
    headers = {
        "Accept": "text/event-stream" if case.stream else "application/json",
        "Authorization": f"Bearer {case.credential.value}",
        "Content-Type": "application/json",
    }
    payload = _request_payload(
        case.model, stream=case.stream, max_tokens=max_tokens, prompt=prompt
    )
    # Keep partial stream evidence even when transport fails after first output.
    first_token_ms: int | None = None
    usage: Any = None
    request_id: str | None = None
    finish_reason: str | None = None
    status_code: int | None = None

    try:
        if not case.stream:
            response = await client.post(
                f"{proxy_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            request_id = response.headers.get("x-request-id")
            status_code = response.status_code
            if response.status_code != 200:
                return _result(
                    case=case,
                    status_code=response.status_code,
                    ok=False,
                    error=f"http_{response.status_code}",
                    error_detail=None,
                    started_at=started_at,
                    request_id=request_id,
                    finish_reason=finish_reason,
                )
            try:
                body = response.json()
            except ValueError:
                return _result(
                    case=case,
                    status_code=response.status_code,
                    ok=False,
                    error="invalid_response",
                    error_detail="response_not_json",
                    started_at=started_at,
                    request_id=request_id,
                    finish_reason=finish_reason,
                )
            choices = body.get("choices") if isinstance(body, dict) else None
            usage = body.get("usage") if isinstance(body, dict) else None
            first_choice = choices[0] if isinstance(choices, list) and choices else None
            if isinstance(first_choice, dict):
                finish_reason = _finish_reason(first_choice.get("finish_reason"))
            if (
                not isinstance(first_choice, dict)
                or not isinstance(first_choice.get("message"), dict)
                or not isinstance(usage, dict)
                or not any(
                    first_choice["message"].get(key) not in (None, "", [])
                    for key in ("content", "tool_calls", "function_call")
                )
            ):
                return _result(
                    case=case,
                    status_code=response.status_code,
                    ok=False,
                    error="invalid_response",
                    error_detail="choices_or_usage_missing",
                    started_at=started_at,
                    request_id=request_id,
                    finish_reason=finish_reason,
                )
            return _result(
                case=case,
                status_code=response.status_code,
                ok=True,
                error=None,
                error_detail=None,
                started_at=started_at,
                usage=usage,
                request_id=request_id,
                finish_reason=finish_reason,
            )

        saw_done = False
        saw_content = False
        async with client.stream(
            "POST",
            f"{proxy_url}/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            request_id = response.headers.get("x-request-id")
            status_code = response.status_code
            if response.status_code != 200:
                return _result(
                    case=case,
                    status_code=response.status_code,
                    ok=False,
                    error=f"http_{response.status_code}",
                    error_detail=None,
                    started_at=started_at,
                    request_id=request_id,
                    finish_reason=finish_reason,
                )

            async for line in response.aiter_lines():
                line = line.strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    saw_done = True
                    break
                try:
                    event = json.loads(data)
                except (TypeError, ValueError):
                    return _result(
                        case=case,
                        status_code=response.status_code,
                        ok=False,
                        error="invalid_response",
                        error_detail="stream_event_not_json",
                        started_at=started_at,
                        ttft_ms=first_token_ms,
                        usage=usage,
                        request_id=request_id,
                        finish_reason=finish_reason,
                    )
                if not isinstance(event, dict):
                    continue
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
                choices = event.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                first_choice = choices[0]
                if not isinstance(first_choice, dict):
                    continue
                finish_reason = (
                    _finish_reason(first_choice.get("finish_reason")) or finish_reason
                )
                delta = first_choice.get("delta")
                if isinstance(delta, dict) and any(
                    delta.get(key) not in (None, "", [])
                    for key in ("content", "tool_calls", "function_call")
                ):
                    saw_content = True
                    if first_token_ms is None:
                        first_token_ms = max(
                            0, int((time.perf_counter() - started_at) * 1000)
                        )

            if not saw_done:
                return _result(
                    case=case,
                    status_code=response.status_code,
                    ok=False,
                    error="invalid_response",
                    error_detail="stream_done_missing",
                    started_at=started_at,
                    ttft_ms=first_token_ms,
                    usage=usage,
                    request_id=request_id,
                    finish_reason=finish_reason,
                )
            if not saw_content or not isinstance(usage, dict):
                return _result(
                    case=case,
                    status_code=response.status_code,
                    ok=False,
                    error="invalid_response",
                    error_detail="stream_content_or_usage_missing",
                    started_at=started_at,
                    ttft_ms=first_token_ms,
                    usage=usage,
                    request_id=request_id,
                    finish_reason=finish_reason,
                )
            return _result(
                case=case,
                status_code=response.status_code,
                ok=True,
                error=None,
                error_detail=None,
                started_at=started_at,
                ttft_ms=first_token_ms,
                usage=usage,
                request_id=request_id,
                finish_reason=finish_reason,
            )
    except asyncio.CancelledError:
        raise RequestCancelled(
            _result(
                case=case,
                status_code=status_code,
                ok=False,
                error="cancelled",
                error_detail=None,
                started_at=started_at,
                ttft_ms=first_token_ms,
                usage=usage,
                request_id=request_id,
                finish_reason=finish_reason,
            )
        ) from None
    except httpx.TimeoutException:
        return _result(
            case=case,
            status_code=None,
            ok=False,
            error="timeout",
            error_detail="request_timeout",
            started_at=started_at,
            ttft_ms=first_token_ms,
            usage=usage,
            request_id=request_id,
            finish_reason=finish_reason,
        )
    except httpx.HTTPError as exc:
        detail = type(exc).__name__
        for secret in secrets:
            detail = detail.replace(secret, "<redacted>")
        return _result(
            case=case,
            status_code=None,
            ok=False,
            error="transport_error",
            error_detail=detail,
            started_at=started_at,
            ttft_ms=first_token_ms,
            usage=usage,
            request_id=request_id,
            finish_reason=finish_reason,
        )
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
        detail = type(exc).__name__
        for secret in secrets:
            detail = detail.replace(secret, "<redacted>")
        return _result(
            case=case,
            status_code=None,
            ok=False,
            error="client_error",
            error_detail=detail,
            started_at=started_at,
            ttft_ms=first_token_ms,
            usage=usage,
            request_id=request_id,
            finish_reason=finish_reason,
        )


async def _fetch_models(
    client: httpx.AsyncClient,
    proxy_url: str,
    credential: Credential,
) -> tuple[str, ...]:
    try:
        response = await client.get(
            f"{proxy_url}/models",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {credential.value}",
            },
        )
    except httpx.TimeoutException as exc:
        raise RemoteAPIError(
            f"key {credential.index:02d} model discovery timed out"
        ) from exc
    except httpx.HTTPError as exc:
        raise RemoteAPIError(
            f"key {credential.index:02d} model discovery failed: {type(exc).__name__}"
        ) from exc

    if response.status_code != 200:
        raise RemoteAPIError(
            f"key {credential.index:02d} model discovery returned HTTP {response.status_code}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise RemoteAPIError(
            f"key {credential.index:02d} model discovery returned invalid JSON"
        ) from exc
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise RemoteAPIError(
            f"key {credential.index:02d} model discovery is missing data"
        )
    models: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id.strip() and model_id not in models:
            models.append(model_id)
    if not models:
        raise RemoteAPIError(f"key {credential.index:02d} returned no models")
    return tuple(models)


async def preflight_models(
    client: httpx.AsyncClient,
    proxy_url: str,
    credentials: Sequence[Credential],
) -> tuple[str, ...]:
    """Validate every key and require a consistent public model catalogue."""
    expected: tuple[str, ...] | None = None
    expected_set: set[str] = set()
    for credential in credentials:
        models = await _fetch_models(client, proxy_url, credential)
        model_set = set(models)
        if expected is None:
            expected = models
            expected_set = model_set
        elif model_set != expected_set:
            raise RemoteAPIError(
                f"key {credential.index:02d} exposes a different model catalogue"
            )
    if expected is None:
        raise RemoteAPIError("model discovery returned no credentials")
    return expected


def choose_models(
    available: Sequence[str], requested: Sequence[str]
) -> tuple[str, ...]:
    if not requested:
        return tuple(available)
    selected: list[str] = []
    available_set = set(available)
    for model in requested:
        if model not in available_set:
            raise LoadConfigurationError(
                f"model is not available through public API: {model}"
            )
        if model not in selected:
            selected.append(model)
    return tuple(selected)


def build_cases(
    models: Sequence[str],
    credentials: Sequence[Credential],
    *,
    requests_per_scenario: int,
    stream_modes: Sequence[bool],
) -> tuple[LoadCase, ...]:
    cases: list[LoadCase] = []
    sequence = 0
    for model in models:
        for stream in stream_modes:
            for _ in range(requests_per_scenario):
                cases.append(
                    LoadCase(
                        sequence=sequence,
                        model=model,
                        stream=stream,
                        credential=credentials[sequence % len(credentials)],
                    )
                )
                sequence += 1
    return tuple(cases)


async def run_cases(
    client: httpx.AsyncClient,
    proxy_url: str,
    cases: Sequence[LoadCase],
    *,
    concurrency: int,
    max_tokens: int,
    secrets: Sequence[str],
    arrival_rpm: float = 6.0,
    prompt: str = DEFAULT_PROMPT,
    result_slots: list[RequestResult | None] | None = None,
    started: float | None = None,
) -> tuple[RequestResult, ...]:
    """Fixed arrivals with bounded workers; delayed sends remain observable."""
    if concurrency < 1 or not 0 < arrival_rpm < float("inf"):
        raise LoadConfigurationError(
            "concurrency and finite arrival RPM must be positive"
        )
    results = result_slots if result_slots is not None else [None] * len(cases)
    if len(results) != len(cases):
        raise LoadConfigurationError("result slots must match planned cases")
    queue: asyncio.Queue[int] = asyncio.Queue()
    for index in range(len(cases)):
        queue.put_nowait(index)
    started = time.perf_counter() if started is None else started

    async def worker() -> None:
        while True:
            try:
                case_index = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                scheduled = case_index * 60.0 / arrival_rpm
                await asyncio.sleep(max(0.0, started + scheduled - time.perf_counter()))
                sent_ms = max(0, round((time.perf_counter() - started) * 1000))
                try:
                    results[case_index] = await _request_one(
                        client,
                        proxy_url,
                        cases[case_index],
                        max_tokens=max_tokens,
                        secrets=secrets,
                        prompt=prompt,
                    )
                except RequestCancelled as exc:
                    results[case_index] = replace(
                        exc.result,
                        scheduled_ms=round(scheduled * 1000),
                        sent_ms=sent_ms,
                    )
                    raise
                results[case_index] = replace(
                    results[case_index],
                    scheduled_ms=round(scheduled * 1000),
                    sent_ms=sent_ms,
                )
            finally:
                queue.task_done()

    worker_count = min(concurrency, len(cases))
    workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
    try:
        await asyncio.gather(*workers)
    except BaseException:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
    if any(result is None for result in results):
        raise RuntimeError("load workers completed without a result")
    return tuple(result for result in results if result is not None)


def percentile(values: Sequence[int], ratio: float) -> float | None:
    """Return an interpolated percentile in milliseconds."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * ratio
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 2)


def _latency_summary(
    results: Sequence[RequestResult], field: str
) -> dict[str, float | int | None]:
    values = [getattr(result, field) for result in results]
    numeric_values = [value for value in values if isinstance(value, int)]
    return {
        "samples": len(numeric_values),
        "p50": percentile(numeric_values, 0.50),
        "p95": percentile(numeric_values, 0.95),
        "p99": percentile(numeric_values, 0.99),
    }


def summarize_results(results: Sequence[RequestResult]) -> dict[str, Any]:
    status_counts = Counter(
        str(result.status_code)
        if result.status_code is not None
        else (
            result.error if result.error in {"cancelled", "not_sent"} else "transport"
        )
        for result in results
    )
    success_count = sum(result.ok for result in results)
    http_failure_count = sum(
        not result.ok and result.status_code is not None for result in results
    )
    error_count = len(results) - success_count - http_failure_count
    usage_total = sum(result.total_tokens or 0 for result in results if result.ok)

    scenarios: dict[tuple[str, bool], list[RequestResult]] = defaultdict(list)
    for result in results:
        scenarios[(result.model, result.stream)].append(result)
    scenario_summaries: list[dict[str, Any]] = []
    for (model, stream), scenario_results in scenarios.items():
        scenario_success = sum(result.ok for result in scenario_results)
        scenario_summaries.append(
            {
                "title": f"{model} / {'stream' if stream else 'non-stream'}",
                "model": model,
                "stream": stream,
                "total": len(scenario_results),
                "success": scenario_success,
                "failed": len(scenario_results) - scenario_success,
                "latency_ms": _latency_summary(scenario_results, "duration_ms"),
                "ttft_ms": _latency_summary(scenario_results, "ttft_ms"),
                "success_latency_ms": _latency_summary(
                    [r for r in scenario_results if r.ok], "duration_ms"
                ),
                "failed_latency_ms": _latency_summary(
                    [r for r in scenario_results if not r.ok], "duration_ms"
                ),
                "missing_ttft": sum(
                    r.stream and r.ttft_ms is None for r in scenario_results
                ),
            }
        )
    scheduled = [
        r for r in results if r.sent_ms is not None and r.scheduled_ms is not None
    ]
    delays = [max(0, r.sent_ms - r.scheduled_ms) for r in scheduled]
    last_finish = max((r.sent_ms + (r.duration_ms or 0) for r in scheduled), default=0)
    send_span = max((r.sent_ms for r in scheduled), default=0) - min(
        (r.sent_ms for r in scheduled), default=0
    )
    return {
        "total": len(results),
        "success": success_count,
        "failed": len(results) - success_count,
        "http_failures": http_failure_count,
        "client_errors": error_count,
        "success_rate": round(success_count / len(results), 4) if results else 0.0,
        "status_counts": dict(sorted(status_counts.items())),
        "latency_ms": _latency_summary(results, "duration_ms"),
        "ttft_ms": _latency_summary(results, "ttft_ms"),
        "success_latency_ms": _latency_summary(
            [r for r in results if r.ok], "duration_ms"
        ),
        "failed_latency_ms": _latency_summary(
            [r for r in results if not r.ok], "duration_ms"
        ),
        "missing_ttft": sum(r.stream and r.ttft_ms is None for r in results),
        "error_counts": dict(Counter(r.error for r in results if r.error)),
        "not_sent": sum(r.error == "not_sent" for r in results),
        "cancelled": sum(r.error == "cancelled" for r in results),
        "truncated": sum(r.truncated for r in results),
        "finish_reason_counts": dict(
            Counter(r.finish_reason for r in results if r.finish_reason)
        ),
        # ok means transport/parser success; length is not transport failure.
        "complete_success": sum(
            r.ok and r.finish_reason in {"stop", "tool_calls", "function_call"}
            for r in results
        ),
        "complete_success_latency_ms": _latency_summary(
            [
                r
                for r in results
                if r.ok and r.finish_reason in {"stop", "tool_calls", "function_call"}
            ],
            "duration_ms",
        ),
        "unknown_finish_reason": sum(
            r.ok and r.finish_reason in {None, "other"} for r in results
        ),
        "filtered_output": sum(r.finish_reason == "content_filter" for r in results),
        "scheduled_samples": len(scheduled),
        "send_delay_ms": {
            "p50": percentile(delays, 0.5),
            "p95": percentile(delays, 0.95),
            "max": max(delays, default=None),
        },
        "delayed_over_100ms": sum(delay > 100 for delay in delays),
        "actual_arrival_rpm": round((len(scheduled) - 1) * 60000 / send_span, 3)
        if send_span > 0
        else None,
        "completed_requests_per_second": round(
            sum(r.error != "cancelled" for r in scheduled) * 1000 / last_finish, 3
        )
        if last_finish > 0
        else None,
        "load_duration_ms": last_finish if scheduled else None,
        "prompt_tokens": sum(r.prompt_tokens or 0 for r in results if r.ok),
        "completion_tokens": sum(r.completion_tokens or 0 for r in results if r.ok),
        "total_tokens": usage_total,
        "token_scope": "successful_transport_rows",
        "all_reported_tokens": {
            "prompt": sum(r.prompt_tokens or 0 for r in results),
            "completion": sum(r.completion_tokens or 0 for r in results),
            "total": sum(r.total_tokens or 0 for r in results),
            "missing_usage": sum(
                r.duration_ms is not None and r.total_tokens is None for r in results
            ),
        },
        "scenarios": scenario_summaries,
    }


async def probe_general_api(
    client: httpx.AsyncClient,
    url: str,
    *,
    requests: int,
    arrival_rpm: float,
    rows: list[dict[str, Any]] | None = None,
    started: float | None = None,
) -> list[dict[str, Any]]:
    """Fixed read-only liveness workload; this does not prove auth/DB health."""
    rows = [] if rows is None else rows
    started = time.perf_counter() if started is None else started
    for sequence in range(requests):
        scheduled = sequence * 60.0 / arrival_rpm
        await asyncio.sleep(max(0.0, started + scheduled - time.perf_counter()))
        sent = time.perf_counter()
        status = None
        error = None
        try:
            response = await client.get(url)
            status = response.status_code
        except httpx.HTTPError as exc:
            error = type(exc).__name__
        except asyncio.CancelledError:
            error = "cancelled"
            raise
        finally:
            rows.append(
                {
                    "sequence": sequence,
                    "scheduled_ms": round(scheduled * 1000),
                    "sent_ms": round((sent - started) * 1000),
                    "duration_ms": round((time.perf_counter() - sent) * 1000),
                    "status_code": status,
                    "error": error,
                }
            )
    return rows


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def build_report(
    *,
    started_at: datetime,
    completed_at: datetime,
    proxy_url: str,
    available_models: Sequence[str],
    selected_models: Sequence[str],
    credentials: Sequence[Credential],
    requests_per_scenario: int,
    concurrency: int,
    max_tokens: int,
    stream_modes: Sequence[bool],
    results: Sequence[RequestResult],
    arrival_rpm: float = 6.0,
    prompt: str = DEFAULT_PROMPT,
    general_api_results: Sequence[Mapping[str, Any]] = (),
    timeout_seconds: float = 120.0,
    interrupted: bool = False,
) -> dict[str, Any]:
    summary = summarize_results(results)
    probe_end = max(
        (r["sent_ms"] + r["duration_ms"] for r in general_api_results), default=None
    )
    ai_end = summary["load_duration_ms"]
    return {
        "schema_version": 2,
        "interrupted": interrupted,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "public_proxy_url": proxy_url,
        "available_models": list(available_models),
        "selected_models": list(selected_models),
        "credential_count": len(credentials),
        "credential_indexes": [credential.index for credential in credentials],
        "config": {
            "requests_per_scenario": requests_per_scenario,
            "concurrency": concurrency,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "timeout_seconds": timeout_seconds,
            "stream_modes": list(stream_modes),
            "arrival_rpm": arrival_rpm,
            # A fingerprint fixes workload identity without storing prompt text.
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        },
        "summary": summary,
        "results": [asdict(result) for result in results],
        "general_api": {
            "endpoint": proxy_url.removesuffix("/ai-proxy") + "/utils/health-check/",
            "scope": "liveness_only_not_authenticated_db",
            "results": list(general_api_results),
            "samples": len(general_api_results),
            "planned_samples": len(results),
            "not_sent": max(0, len(results) - len(general_api_results)),
            "first_sent_ms": min(
                (r["sent_ms"] for r in general_api_results), default=None
            ),
            "last_finished_ms": probe_end,
            "ai_last_finished_ms": ai_end,
            "covers_ai_completion": probe_end >= ai_end
            if probe_end is not None and ai_end is not None
            else None,
            "tail_unobserved_ms": max(0, ai_end - probe_end)
            if probe_end is not None and ai_end is not None
            else None,
            "p50_ms": percentile([r["duration_ms"] for r in general_api_results], 0.5),
            "p95_ms": percentile([r["duration_ms"] for r in general_api_results], 0.95),
            "failed": sum(r["status_code"] != 200 for r in general_api_results),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="本機 ignored dotenv，預設為 .env.ai-api-load.local",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="覆寫 AI_API_PUBLIC_BASE_URL；必須以 /api/v1 結尾",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        help="只測指定模型；可重複指定，未指定則測所有公開模型",
    )
    parser.add_argument(
        "--requests",
        type=_positive_int,
        default=10,
        dest="requests_per_scenario",
        help="每個模型／stream 模式的請求數，預設 10",
    )
    parser.add_argument(
        "--concurrency",
        type=_positive_int,
        default=2,
        help="本機同時發出的請求數，預設 2",
    )
    parser.add_argument(
        "--arrival-rpm",
        type=_positive_float,
        default=6.0,
        help="固定總到達 RPM，預設 6；併發上限造成的延後會記入報告",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="固定測試 prompt；比較長輸出時明確指定，報告只保留指紋",
    )
    parser.add_argument(
        "--stream",
        choices=("false", "true", "both"),
        default="false",
        help="測試 non-stream、stream 或 both；預設 false",
    )
    parser.add_argument(
        "--max-tokens",
        type=_positive_int,
        default=32,
        help="每次請求的 max_tokens，預設 32",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=120.0,
        help="單次 HTTP timeout 秒數，預設 120",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=DEFAULT_REPORT_FILE,
        help="JSON 報告路徑，預設 test-results/ai-api-load/latest.json",
    )
    return parser


def _stream_modes(value: str) -> tuple[bool, ...]:
    return {"false": (False,), "true": (True,), "both": (False, True)}[value]


async def async_main(args: argparse.Namespace) -> int:
    values = load_local_values(args.env_file)
    base_url = args.base_url or values.get("AI_API_PUBLIC_BASE_URL", "")
    if not base_url:
        raise LoadConfigurationError(
            f"missing AI_API_PUBLIC_BASE_URL in {args.env_file} or process environment"
        )
    proxy_url = public_proxy_url(base_url)
    credentials = load_credentials(values)
    stream_modes = _stream_modes(args.stream)

    timeout = httpx.Timeout(args.timeout)
    limits = httpx.Limits(
        max_connections=max(args.concurrency, len(credentials)),
        max_keepalive_connections=max(args.concurrency, 1),
    )
    secrets = tuple(credential.value for credential in credentials)
    started_at = datetime.now(timezone.utc)
    async with (
        httpx.AsyncClient(timeout=timeout, limits=limits) as client,
        httpx.AsyncClient(
            timeout=timeout, limits=httpx.Limits(max_connections=1)
        ) as general_client,
    ):
        available_models = await preflight_models(client, proxy_url, credentials)
        selected_models = choose_models(available_models, args.model)
        cases = build_cases(
            selected_models,
            credentials,
            requests_per_scenario=args.requests_per_scenario,
            stream_modes=stream_modes,
        )
        print(
            f"[PASS] preflight: {len(credentials)} keys, "
            f"{len(available_models)} public models"
        )
        print(
            f"[PASS] scenario: {len(cases)} requests, "
            f"concurrency={args.concurrency}, modes={','.join('stream' if item else 'non-stream' for item in stream_modes)}"
        )
        result_slots: list[RequestResult | None] = [None] * len(cases)
        general_results: list[dict[str, Any]] = []
        workload_started = time.perf_counter()
        interrupted = False
        workloads = [
            asyncio.create_task(
                run_cases(
                    client,
                    proxy_url,
                    cases,
                    concurrency=args.concurrency,
                    max_tokens=args.max_tokens,
                    secrets=secrets,
                    arrival_rpm=args.arrival_rpm,
                    prompt=args.prompt,
                    result_slots=result_slots,
                    started=workload_started,
                )
            ),
            asyncio.create_task(
                probe_general_api(
                    general_client,
                    proxy_url.removesuffix("/ai-proxy") + "/utils/health-check/",
                    requests=len(cases),
                    arrival_rpm=args.arrival_rpm,
                    rows=general_results,
                    started=workload_started,
                )
            ),
        ]
        try:
            await asyncio.gather(*workloads)
        except asyncio.CancelledError:
            interrupted = True
            for task in workloads:
                task.cancel()
            await asyncio.gather(*workloads, return_exceptions=True)
        results = tuple(
            row if row is not None else _not_sent(case, index, args.arrival_rpm)
            for index, (case, row) in enumerate(zip(cases, result_slots, strict=True))
        )
    completed_at = datetime.now(timezone.utc)

    report = build_report(
        started_at=started_at,
        completed_at=completed_at,
        proxy_url=proxy_url,
        available_models=available_models,
        selected_models=selected_models,
        credentials=credentials,
        requests_per_scenario=args.requests_per_scenario,
        concurrency=args.concurrency,
        max_tokens=args.max_tokens,
        stream_modes=stream_modes,
        results=results,
        arrival_rpm=args.arrival_rpm,
        prompt=args.prompt,
        general_api_results=general_results,
        timeout_seconds=args.timeout,
        interrupted=interrupted,
    )
    _atomic_write_json(args.report_file, report)

    summary = report["summary"]
    print(
        f"Totals: PASS={summary['success']} "
        f"FAIL={summary['http_failures']} ERROR={summary['client_errors']} "
        f"TOTAL={summary['total']}"
    )
    print(f"Report: {args.report_file}")
    if interrupted:
        print("[ERROR] interrupted; partial results saved", file=sys.stderr)
        return 130
    return 0 if summary["failed"] == 0 else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(async_main(args))
    except (LoadConfigurationError, RemoteAPIError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("[ERROR] interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
