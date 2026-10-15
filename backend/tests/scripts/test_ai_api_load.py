from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "test_ai_api_load.py"
SPEC = importlib.util.spec_from_file_location("ai_api_load_script", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
ai_api_load = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ai_api_load
SPEC.loader.exec_module(ai_api_load)


def result_row(sequence=0, *, ok=True, duration=100, stream=False, ttft=None):
    return ai_api_load.RequestResult(
        sequence,
        "model-a",
        stream,
        1,
        200 if ok else 503,
        ok,
        None if ok else "http_503",
        None,
        duration,
        ttft,
        4,
        2,
        6,
        "request-id",
    )


@pytest.mark.parametrize("duration", [0.5, 12.0])
def test_fixed_arrivals_and_concurrency_delay_are_explicit(monkeypatch, duration):
    class Clock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = Clock()

    async def sleep(seconds):
        clock.value += seconds

    async def request(client, proxy_url, case, **kwargs):
        clock.value += duration
        return result_row(case.sequence, duration=round(duration * 1000))

    monkeypatch.setattr(ai_api_load.time, "perf_counter", clock)
    monkeypatch.setattr(ai_api_load.asyncio, "sleep", sleep)
    monkeypatch.setattr(ai_api_load, "_request_one", request)
    cases = ai_api_load.build_cases(
        ["model-a"],
        [ai_api_load.Credential(1, "ccai_test")],
        requests_per_scenario=2,
        stream_modes=[False],
    )
    rows = asyncio.run(
        ai_api_load.run_cases(
            None,
            "https://site.invalid/api/v1/ai-proxy",
            cases,
            concurrency=1,
            max_tokens=32,
            secrets=[],
            arrival_rpm=6,
        )
    )
    assert [r.scheduled_ms for r in rows] == [0, 10000]
    assert [r.sent_ms for r in rows] == [0, 10000 if duration < 10 else 12000]
    assert ai_api_load.summarize_results(rows)["send_delay_ms"]["max"] == (
        0 if duration < 10 else 2000
    )


def test_success_failure_missing_ttft_and_empty_samples_are_distinct():
    rows = [
        result_row(0, duration=100, stream=True, ttft=20),
        result_row(1, ok=False, duration=70000, stream=True),
    ]
    summary = ai_api_load.summarize_results(rows)
    assert summary["success_latency_ms"] == {
        "samples": 1,
        "p50": 100,
        "p95": 100,
        "p99": 100,
    }
    assert summary["failed_latency_ms"]["p95"] == 70000
    assert summary["ttft_ms"]["samples"] == 1
    assert summary["missing_ttft"] == 1
    assert summary["error_counts"] == {"http_503": 1}
    assert ai_api_load.summarize_results([])["latency_ms"]["p95"] is None


def test_stream_tool_output_and_partial_timeout_preserve_ttft():
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"function":{"name":"lookup"}}]}}]}\n\n'
            raise httpx.ReadTimeout("secret input")

    def handler(request):
        return httpx.Response(
            200, stream=BrokenStream(), headers={"x-request-id": "tool-id"}
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ai_api_load._request_one(
                client,
                "https://site.invalid",
                ai_api_load.LoadCase(
                    0, "model-a", True, ai_api_load.Credential(1, "ccai_secret")
                ),
                max_tokens=32,
                secrets=["ccai_secret"],
            )

    row = asyncio.run(run())
    assert row.error == "timeout" and row.ttft_ms is not None
    assert row.request_id == "tool-id" and "secret input" not in str(row)


def test_empty_nonstream_output_is_not_success():
    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": ""}}], "usage": {}}
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ai_api_load._request_one(
                client,
                "https://site.invalid",
                ai_api_load.LoadCase(
                    0, "model-a", False, ai_api_load.Credential(1, "ccai_test")
                ),
                max_tokens=32,
                secrets=[],
            )

    assert not asyncio.run(run()).ok


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1"])
def test_cli_rejects_nonfinite_or_nonpositive_arrival_rpm(value):
    with pytest.raises(SystemExit):
        ai_api_load.build_parser().parse_args(["--arrival-rpm", value])


def test_general_api_probes_are_read_only_separate_and_keep_failures():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET" and "authorization" not in request.headers
        return httpx.Response(503)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ai_api_load.probe_general_api(
                client,
                "https://site.invalid/api/v1/utils/health-check/",
                requests=2,
                arrival_rpm=600000,
            )

    rows = asyncio.run(run())
    assert len(rows) == len(calls) == 2
    assert all(r["status_code"] == 503 and r["duration_ms"] >= 0 for r in rows)


def test_custom_prompt_fingerprint_and_timeout_are_reported_without_text():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    report = ai_api_load.build_report(
        started_at=now,
        completed_at=now,
        proxy_url="https://site.invalid/api/v1/ai-proxy",
        available_models=[],
        selected_models=[],
        credentials=[],
        requests_per_scenario=0,
        concurrency=1,
        max_tokens=512,
        stream_modes=[False],
        results=[],
        prompt="private workload text",
        arrival_rpm=4,
        timeout_seconds=25,
    )
    assert len(report["config"]["prompt_sha256"]) == 64
    assert report["config"]["timeout_seconds"] == 25
    assert report["config"]["arrival_rpm"] == 4
    assert "private workload text" not in str(report)


@pytest.mark.parametrize("stream", [False, True])
def test_length_finish_is_preserved_without_becoming_transport_failure(stream):
    def handler(request):
        if not stream:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "length", "message": {"content": "partial"}}
                    ],
                    "usage": {"total_tokens": 8},
                },
            )
        return httpx.Response(
            200,
            content=(
                b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}\n\n'
                b'data: {"usage":{"total_tokens":8}}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ai_api_load._request_one(
                client,
                "https://site.invalid",
                ai_api_load.LoadCase(
                    0, "model-a", stream, ai_api_load.Credential(1, "ccai_test")
                ),
                max_tokens=32,
                secrets=[],
            )

    row = asyncio.run(run())
    assert row.ok and row.truncated and row.finish_reason == "length"
    summary = ai_api_load.summarize_results([row])
    assert summary["success"] == 1 and summary["complete_success"] == 0
    assert summary["truncated"] == 1 and summary["finish_reason_counts"] == {
        "length": 1
    }


def test_cancelled_stream_preserves_first_output_and_usage():
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}],"usage":{"total_tokens":3}}\n\n'
            raise asyncio.CancelledError()

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=Stream())
            )
        ) as client:
            with pytest.raises(ai_api_load.RequestCancelled) as caught:
                await ai_api_load._request_one(
                    client,
                    "https://site.invalid",
                    ai_api_load.LoadCase(
                        0, "model-a", True, ai_api_load.Credential(1, "ccai_test")
                    ),
                    max_tokens=32,
                    secrets=[],
                )
            return caught.value.result

    row = asyncio.run(run())
    assert row.error == "cancelled" and row.status_code == 200
    assert row.ttft_ms is not None and row.total_tokens == 3
    assert ai_api_load.summarize_results([row])["all_reported_tokens"]["total"] == 3


@pytest.mark.parametrize(
    "reason", [None, "other", "content_filter", "stop", "tool_calls", "function_call"]
)
def test_complete_success_requires_known_completion_reason(reason):
    from dataclasses import replace

    summary = ai_api_load.summarize_results(
        [replace(result_row(), finish_reason=reason)]
    )
    assert summary["success"] == 1
    assert summary["complete_success"] == (
        1 if reason in {"stop", "tool_calls", "function_call"} else 0
    )
    assert summary["unknown_finish_reason"] == (1 if reason in {None, "other"} else 0)
    assert summary["filtered_output"] == (1 if reason == "content_filter" else 0)


@pytest.mark.parametrize("concurrency", [1, 3])
def test_interrupted_main_writes_completed_cancelled_and_not_sent_rows(
    monkeypatch, tmp_path, concurrency
):
    import json

    sent = []
    original_client = httpx.AsyncClient

    async def run():
        blocked = asyncio.Event()

        async def handler(request):
            if request.url.path.endswith("health-check/"):
                return httpx.Response(200)
            sent.append(request)
            if len(sent) == 1:
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"content": "OK"}, "finish_reason": "stop"}
                        ],
                        "usage": {"total_tokens": 6},
                    },
                )
            if len(sent) == concurrency + 1:
                blocked.set()
            await asyncio.Event().wait()
            raise AssertionError("blocked request must stay pending until cancelled")

        async def preflight(*args):
            return ("model-a",)

        monkeypatch.setattr(
            ai_api_load,
            "load_local_values",
            lambda path: {
                "AI_API_PUBLIC_BASE_URL": "https://site.invalid/api/v1",
                "CCAI_API_KEY_01": "ccai_one",
                "CCAI_API_KEY_02": "ccai_two",
            },
        )
        monkeypatch.setattr(ai_api_load, "preflight_models", preflight)
        monkeypatch.setattr(
            ai_api_load.httpx,
            "AsyncClient",
            lambda **kwargs: original_client(
                transport=httpx.MockTransport(handler), **kwargs
            ),
        )
        args = ai_api_load.build_parser().parse_args(
            [
                "--requests",
                str(concurrency + 3),
                "--concurrency",
                str(concurrency),
                "--stream",
                "false",
                "--arrival-rpm",
                "600000",
                "--report-file",
                str(tmp_path / "latest.json"),
            ]
        )
        task = asyncio.create_task(ai_api_load.async_main(args))
        await asyncio.wait_for(blocked.wait(), 2)
        task.cancel()
        exit_code = await task
        assert not [
            t
            for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.done()
        ]
        return exit_code

    assert asyncio.run(run()) == 130
    report = json.loads((tmp_path / "latest.json").read_text())
    assert report["interrupted"] and len(sent) == concurrency + 1
    assert [row["error"] for row in report["results"]] == [None] + [
        "cancelled"
    ] * concurrency + ["not_sent"] * 2
    assert report["results"][-1]["duration_ms"] is None
    assert report["summary"]["latency_ms"]["samples"] == concurrency + 1
    assert "ccai_one" not in str(report) and "ccai_two" not in str(report)


@pytest.mark.parametrize("count", [20, 100])
def test_percentile_recomputes_raw_samples_with_explicit_population(count):
    assert ai_api_load.percentile(list(range(1, count + 1)), 0.95) == round(
        1 + (count - 1) * 0.95, 2
    )


def test_probe_coverage_does_not_hide_unobserved_ai_tail():
    from dataclasses import replace

    now = ai_api_load.datetime.now(ai_api_load.timezone.utc)
    row = replace(result_row(duration=70000), sent_ms=0, scheduled_ms=0)
    report = ai_api_load.build_report(
        started_at=now,
        completed_at=now,
        proxy_url="https://site.invalid/api/v1/ai-proxy",
        available_models=[],
        selected_models=[],
        credentials=[],
        requests_per_scenario=1,
        concurrency=1,
        max_tokens=32,
        stream_modes=[False],
        results=[row],
        general_api_results=[{"sent_ms": 0, "duration_ms": 10, "status_code": 200}],
    )
    assert report["general_api"]["samples"] == 1
    assert report["general_api"]["covers_ai_completion"] is False
    assert report["general_api"]["tail_unobserved_ms"] == 69990


def test_parse_dotenv_supports_comments_quotes_and_export(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.ai-api-load.local"
    env_file.write_text(
        "# ignored\n"
        "export AI_API_PUBLIC_BASE_URL='https://campus.example/api/v1'\n"
        "CCAI_API_KEY_01=ccai_first\n"
        'CCAI_API_KEY_02="ccai_second" # inline comment\n',
        encoding="utf-8",
    )

    values = ai_api_load.parse_dotenv(env_file)

    assert values == {
        "AI_API_PUBLIC_BASE_URL": "https://campus.example/api/v1",
        "CCAI_API_KEY_01": "ccai_first",
        "CCAI_API_KEY_02": "ccai_second",
    }


def test_load_credentials_sorts_indexes_without_exposing_values() -> None:
    credentials = ai_api_load.load_credentials(
        {
            "CCAI_API_KEY_10": "ccai_tenth",
            "CCAI_API_KEY_02": "ccai_second",
            "CCAI_API_KEY_01": "ccai_first",
        }
    )

    assert [item.index for item in credentials] == [1, 2, 10]
    assert [item.value for item in credentials] == [
        "ccai_first",
        "ccai_second",
        "ccai_tenth",
    ]


def test_load_credentials_rejects_duplicates_and_single_key() -> None:
    with pytest.raises(ai_api_load.LoadConfigurationError, match="duplicate"):
        ai_api_load.load_credentials(
            {
                "CCAI_API_KEY_01": "ccai_same",
                "CCAI_API_KEY_02": "ccai_same",
            }
        )

    with pytest.raises(ai_api_load.LoadConfigurationError, match="at least two"):
        ai_api_load.load_credentials({"CCAI_API_KEY_01": "ccai_only"})


def test_public_proxy_url_rejects_unsafe_shape() -> None:
    assert (
        ai_api_load.public_proxy_url("https://campus.example/api/v1")
        == "https://campus.example/api/v1/ai-proxy"
    )
    assert (
        ai_api_load.public_proxy_url("https://campus.example/api/v1/ai-proxy")
        == "https://campus.example/api/v1/ai-proxy"
    )
    with pytest.raises(ai_api_load.LoadConfigurationError, match="end with /api/v1"):
        ai_api_load.public_proxy_url("https://campus.example")


def test_build_cases_round_robins_credentials() -> None:
    credentials = (
        ai_api_load.Credential(index=1, value="ccai_first"),
        ai_api_load.Credential(index=2, value="ccai_second"),
    )
    cases = ai_api_load.build_cases(
        ["model-a"],
        credentials,
        requests_per_scenario=4,
        stream_modes=(False,),
    )

    assert [case.credential.index for case in cases] == [1, 2, 1, 2]
    assert [case.sequence for case in cases] == [0, 1, 2, 3]


def test_summary_reports_percentiles_without_secrets() -> None:
    credential = ai_api_load.Credential(index=1, value="ccai_secret")
    cases = ai_api_load.build_cases(
        ["model-a"],
        (credential, ai_api_load.Credential(index=2, value="ccai_other")),
        requests_per_scenario=2,
        stream_modes=(False,),
    )
    results = tuple(
        ai_api_load.RequestResult(
            sequence=case.sequence,
            model=case.model,
            stream=case.stream,
            key_index=case.credential.index,
            status_code=200,
            ok=True,
            error=None,
            error_detail=None,
            duration_ms=100 + case.sequence * 100,
            ttft_ms=None,
            prompt_tokens=4,
            completion_tokens=2,
            total_tokens=6,
            request_id=f"request-{case.sequence}",
        )
        for case in cases
    )

    report = ai_api_load.build_report(
        started_at=ai_api_load.datetime.now(ai_api_load.timezone.utc),
        completed_at=ai_api_load.datetime.now(ai_api_load.timezone.utc),
        proxy_url="https://campus.example/api/v1/ai-proxy",
        available_models=("model-a",),
        selected_models=("model-a",),
        credentials=(credential,),
        requests_per_scenario=2,
        concurrency=2,
        max_tokens=32,
        stream_modes=(False,),
        results=results,
    )

    assert report["summary"]["success"] == 2
    assert report["summary"]["latency_ms"]["p95"] == 195.0
    assert "ccai_secret" not in str(report)


def test_non_stream_request_uses_key_and_extracts_usage() -> None:
    credential = ai_api_load.Credential(index=1, value="ccai_secret")
    case = ai_api_load.LoadCase(
        sequence=0,
        model="model-a",
        stream=False,
        credential=credential,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer ccai_secret"
        return httpx.Response(
            200,
            request=request,
            headers={"x-request-id": "request-1"},
            json={
                "choices": [{"message": {"content": "OK"}}],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                },
            },
        )

    async def run() -> ai_api_load.RequestResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ai_api_load._request_one(
                client,
                "https://campus.example/api/v1/ai-proxy",
                case,
                max_tokens=32,
                secrets=(credential.value,),
            )

    result = asyncio.run(run())

    assert result.ok is True
    assert result.status_code == 200
    assert result.total_tokens == 6
    assert result.request_id == "request-1"


def test_stream_request_requires_done_and_extracts_ttft_and_usage() -> None:
    credential = ai_api_load.Credential(index=1, value="ccai_stream")
    case = ai_api_load.LoadCase(
        sequence=0,
        model="model-a",
        stream=True,
        credential=credential,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        body = (
            'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )

    async def run() -> ai_api_load.RequestResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ai_api_load._request_one(
                client,
                "https://campus.example/api/v1/ai-proxy",
                case,
                max_tokens=32,
                secrets=(credential.value,),
            )

    result = asyncio.run(run())

    assert result.ok is True
    assert result.ttft_ms is not None
    assert result.total_tokens == 6
