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
import json
import os
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
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
    duration_ms: int
    ttft_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    request_id: str | None


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
            raise LoadConfigurationError(
                f"{path}:{line_number}: expected KEY=value"
            )
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
        values[name] = _unquote_dotenv(
            raw_value, path=path, line_number=line_number
        )
    return values


def load_local_values(
    env_file: Path, *, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Load the ignored file, with explicitly exported process values winning."""
    values = parse_dotenv(env_file) if env_file.is_file() else {}
    process_values = os.environ if environ is None else environ
    for name, value in process_values.items():
        if (name == "AI_API_PUBLIC_BASE_URL" or CCAI_KEY_NAME_RE.fullmatch(name)) and value.strip():
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
            raise LoadConfigurationError(
                f"duplicate numeric key index: {index:02d}"
            )
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
        raise LoadConfigurationError(
            "AI_API_PUBLIC_BASE_URL must end with /api/v1"
        )
    return f"{candidate}/ai-proxy"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def _content_text(value: Any) -> str:
    """Extract text from common OpenAI content shapes without logging it."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    return ""


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
    )


def _request_payload(model: str, *, stream: bool, max_tokens: int) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": DEFAULT_PROMPT}],
        "max_tokens": max_tokens,
        "stream": stream,
    }


async def _request_one(
    client: httpx.AsyncClient,
    proxy_url: str,
    case: LoadCase,
    *,
    max_tokens: int,
    secrets: Sequence[str],
) -> RequestResult:
    started_at = time.perf_counter()
    headers = {
        "Accept": "text/event-stream" if case.stream else "application/json",
        "Authorization": f"Bearer {case.credential.value}",
        "Content-Type": "application/json",
    }
    payload = _request_payload(case.model, stream=case.stream, max_tokens=max_tokens)

    try:
        if not case.stream:
            response = await client.post(
                f"{proxy_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            request_id = response.headers.get("x-request-id")
            if response.status_code != 200:
                return _result(
                    case=case,
                    status_code=response.status_code,
                    ok=False,
                    error=f"http_{response.status_code}",
                    error_detail=None,
                    started_at=started_at,
                    request_id=request_id,
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
                )
            choices = body.get("choices") if isinstance(body, dict) else None
            usage = body.get("usage") if isinstance(body, dict) else None
            first_choice = choices[0] if isinstance(choices, list) and choices else None
            if (
                not isinstance(first_choice, dict)
                or not isinstance(first_choice.get("message"), dict)
                or not isinstance(usage, dict)
            ):
                return _result(
                    case=case,
                    status_code=response.status_code,
                    ok=False,
                    error="invalid_response",
                    error_detail="choices_or_usage_missing",
                    started_at=started_at,
                    request_id=request_id,
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
            )

        saw_done = False
        saw_content = False
        usage: Any = None
        first_token_ms: int | None = None
        async with client.stream(
            "POST",
            f"{proxy_url}/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            request_id = response.headers.get("x-request-id")
            if response.status_code != 200:
                return _result(
                    case=case,
                    status_code=response.status_code,
                    ok=False,
                    error=f"http_{response.status_code}",
                    error_detail=None,
                    started_at=started_at,
                    request_id=request_id,
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
                delta = first_choice.get("delta")
                content = _content_text(delta.get("content") if isinstance(delta, dict) else None)
                if content:
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
            )
    except asyncio.CancelledError:
        raise
    except httpx.TimeoutException:
        return _result(
            case=case,
            status_code=None,
            ok=False,
            error="timeout",
            error_detail="request_timeout",
            started_at=started_at,
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
        )


async def _fetch_models(
    client: httpx.AsyncClient,
    proxy_url: str,
    credential: Credential,
) -> tuple[str, ...]:
    try:
        response = await client.get(
            f"{proxy_url}/models",
            headers={"Accept": "application/json", "Authorization": f"Bearer {credential.value}"},
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


def choose_models(available: Sequence[str], requested: Sequence[str]) -> tuple[str, ...]:
    if not requested:
        return tuple(available)
    selected: list[str] = []
    available_set = set(available)
    for model in requested:
        if model not in available_set:
            raise LoadConfigurationError(f"model is not available through public API: {model}")
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
) -> tuple[RequestResult, ...]:
    """Run cases with a bounded worker pool instead of creating one task/case."""
    results: list[RequestResult | None] = [None] * len(cases)
    queue: asyncio.Queue[int] = asyncio.Queue()
    for index in range(len(cases)):
        queue.put_nowait(index)

    async def worker() -> None:
        while True:
            try:
                case_index = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                results[case_index] = await _request_one(
                    client,
                    proxy_url,
                    cases[case_index],
                    max_tokens=max_tokens,
                    secrets=secrets,
                )
            finally:
                queue.task_done()

    worker_count = min(concurrency, len(cases))
    await asyncio.gather(*(worker() for _ in range(worker_count)))
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


def _latency_summary(results: Sequence[RequestResult], field: str) -> dict[str, float | None]:
    values = [getattr(result, field) for result in results]
    numeric_values = [value for value in values if isinstance(value, int)]
    return {
        "p50": percentile(numeric_values, 0.50),
        "p95": percentile(numeric_values, 0.95),
        "p99": percentile(numeric_values, 0.99),
    }


def summarize_results(results: Sequence[RequestResult]) -> dict[str, Any]:
    status_counts = Counter(
        str(result.status_code) if result.status_code is not None else "transport"
        for result in results
    )
    success_count = sum(result.ok for result in results)
    http_failure_count = sum(
        not result.ok and result.status_code is not None for result in results
    )
    error_count = len(results) - success_count - http_failure_count
    usage_total = sum(
        result.total_tokens or 0 for result in results if result.ok
    )

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
            }
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
        "total_tokens": usage_total,
        "scenarios": scenario_summaries,
    }


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
) -> dict[str, Any]:
    summary = summarize_results(results)
    return {
        "schema_version": 1,
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
            "stream_modes": list(stream_modes),
        },
        "summary": summary,
        "results": [asdict(result) for result in results],
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
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
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
        results = await run_cases(
            client,
            proxy_url,
            cases,
            concurrency=args.concurrency,
            max_tokens=args.max_tokens,
            secrets=secrets,
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
    )
    _atomic_write_json(args.report_file, report)

    summary = report["summary"]
    print(
        f"Totals: PASS={summary['success']} "
        f"FAIL={summary['http_failures']} ERROR={summary['client_errors']} "
        f"TOTAL={summary['total']}"
    )
    print(f"Report: {args.report_file}")
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
