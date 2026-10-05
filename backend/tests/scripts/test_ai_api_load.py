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
