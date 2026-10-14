"""app/services/llm_gateway/relay_service.py 的補強測試（排隊、串流與錯誤路徑；不連網路）。"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.responses import JSONResponse, Response
from starlette.requests import Request

from app.services.llm_gateway import relay_service as relay
from app.services.monitoring import ai_metrics

HTTP_SCOPE = {"type": "http", "asgi": {"spec_version": "2.4"}}


async def settle(predicate) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate()


def _queue(**overrides: Any) -> relay.AdmissionQueue:
    params: dict[str, Any] = {
        "max_active": 2,
        "max_waiting": 2,
        "model_max_active": 2,
        "wait_timeout_seconds": 5,
    }
    params.update(overrides)
    return relay.AdmissionQueue(**params)


def _request(receive=None) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/ai-proxy/chat/completions",
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
    }
    if receive is None:
        return Request(scope)
    return Request(scope, receive)


def _observation(**overrides: Any) -> relay.RelayObservation:
    observation = relay.RelayObservation(
        SimpleNamespace(id="user-1"),
        SimpleNamespace(id="credential-1"),
        "A",
        "chat_completion",
        "req-1",
        time.monotonic(),
        datetime.now(timezone.utc),
        True,
    )
    for name, value in overrides.items():
        setattr(observation, name, value)
    return observation


async def _never_receive() -> dict[str, Any]:
    await asyncio.Event().wait()
    return {"type": "http.disconnect"}


@pytest.fixture
def usage_records(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    async def record(**kwargs: Any) -> None:
        records.append(kwargs)

    monkeypatch.setattr(relay, "record_usage_in_threadpool", record)
    monkeypatch.setattr(relay, "_usage_tasks", set())
    return records


class _FakeObservation(SimpleNamespace):
    async def finish(self) -> None:
        self.finished = True


# ---- AdmissionQueue ----------------------------------------------------


async def test_reserve_rejects_when_closed_or_full() -> None:
    queue = _queue(max_active=1, max_waiting=1)
    first = queue.ticket("A")
    queue.reserve(first)
    queue.reserve(first)  # 已保留的 ticket 再保留不佔額度
    with pytest.raises(relay.AdmissionRejected) as full:
        queue.reserve(queue.ticket("A"))
    assert full.value.reason == "queue_full"

    queue.closed = True
    with pytest.raises(relay.AdmissionRejected) as shutdown:
        queue.reserve(queue.ticket("A"))
    assert shutdown.value.reason == "shutdown"


async def test_acquire_rejects_expired_deadline_and_spent_budget() -> None:
    queue = _queue()
    with pytest.raises(relay.AdmissionRejected) as expired:
        await queue.acquire(queue.ticket("A", deadline=0.0))
    assert expired.value.reason == "request_timeout"

    ticket = queue.ticket("A")
    ticket.wait_remaining = 0
    with pytest.raises(relay.AdmissionRejected) as spent:
        await queue.acquire(ticket)
    assert spent.value.reason == "timeout"


async def test_acquire_rejects_when_waiting_line_stays_full() -> None:
    queue = _queue(max_active=3, max_waiting=1, model_max_active=1)
    lease = await queue.acquire(queue.ticket("m"))
    waiter = asyncio.create_task(queue.acquire(queue.ticket("m")))
    await settle(lambda: queue.waiting == 1)

    with pytest.raises(relay.AdmissionRejected) as rejected:
        await queue.acquire(queue.ticket("m"))
    assert rejected.value.reason == "queue_full"

    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    lease.release()
    assert queue.active == 0


async def test_release_of_unknown_lease_is_ignored() -> None:
    queue = _queue()
    lease = await queue.acquire(queue.ticket("A"))
    lease.release()
    queue.release(lease)
    assert queue.active == 0


async def test_dispatch_drops_already_resolved_waiters() -> None:
    queue = _queue()
    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    future.set_result(None)
    queue._waiters.append((queue.ticket("A"), future))

    queue.dispatch()
    assert queue._waiters == []


async def test_close_cancels_pending_timer() -> None:
    queue = _queue()
    queue._timer = asyncio.get_running_loop().call_later(100, lambda: None)
    timer = queue._timer

    await queue.close()
    assert queue._timer is None
    assert timer.cancelled()


async def test_admission_queue_is_closed_while_runtime_is_stopping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(relay, "_admission_queue", relay._LoopBound())
    monkeypatch.setattr(relay, "_relay_stopping", True)

    assert relay._get_admission_queue().closed is True


async def test_close_relay_runtime_cancels_catalogue_and_stuck_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogue = relay._CatalogueState()
    catalogue.task = asyncio.create_task(asyncio.Event().wait())
    stuck = asyncio.create_task(asyncio.Event().wait())
    real_wait = asyncio.wait

    async def no_close() -> None:
        return None

    async def fast_wait(fs, *, timeout=None, **kwargs):
        if timeout == 2.0:
            return set(), set(fs)
        return await real_wait(fs, timeout=timeout, **kwargs)

    monkeypatch.setattr(relay, "_relay_stopping", False)
    monkeypatch.setattr(relay, "_admission_queue", relay._LoopBound())
    monkeypatch.setattr(relay, "_relay_http_client", relay._LoopBound())
    monkeypatch.setattr(relay, "_models_cache", relay._LoopBound())
    monkeypatch.setattr(relay, "_catalogue", catalogue)
    monkeypatch.setattr(relay, "_usage_tasks", {stuck})
    monkeypatch.setattr(relay, "close_usage_writer", no_close)
    monkeypatch.setattr(relay.asyncio, "wait", fast_wait)

    await relay.close_relay_runtime()

    assert stuck.cancelled()
    assert catalogue.task is None
    assert catalogue.models == set()


# ---- 純函式 ------------------------------------------------------------


def test_retry_after_http_date_without_timezone_is_ignored() -> None:
    assert relay.retry_after_seconds("Wed, 21 Oct 2015 07:28:00 -0000") is None


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"error": {"message": 5}}'])
def test_recoverable_model_limit_ignores_malformed_bodies(body: bytes) -> None:
    assert relay.recoverable_model_limit(body) is None


def test_usage_details_with_non_numeric_tokens() -> None:
    payload = {"usage": {"prompt_tokens": "many"}, "model": "m"}
    assert relay.usage_details(payload) == (0, 0, True, "m")


def test_update_stream_terminal_response_completed_event_without_data() -> None:
    state = relay.StreamTerminalState()
    relay.update_stream_terminal(
        "event: response.completed", state, request_type="response"
    )
    relay.update_stream_terminal("", state, request_type="response")
    assert state.completed is True


def test_update_stream_terminal_ignores_non_object_payload() -> None:
    state = relay.StreamTerminalState()
    relay.update_stream_terminal("data: [1, 2]", state, request_type="response")
    relay.update_stream_terminal("", state, request_type="response")
    assert state.completed is False and state.failed is False


def test_stream_event_has_output_variants() -> None:
    assert relay.stream_event_has_output(None) is False
    assert relay.stream_event_has_output(
        {"type": "response.output_text.delta", "delta": "hi"}
    )
    assert relay.stream_event_has_output({"choices": ["junk", {"text": "a"}]})


async def test_usage_task_done_logs_failed_task(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom() -> None:
        raise RuntimeError("boom")

    task = asyncio.create_task(boom())
    await asyncio.gather(task, return_exceptions=True)
    monkeypatch.setattr(relay, "_usage_tasks", {task})

    with caplog.at_level("ERROR"):
        relay.usage_task_done(task)
    assert "terminal usage task failed" in caplog.text
    assert relay._usage_tasks == set()


def test_stream_payload_leaves_responses_endpoint_untouched() -> None:
    payload = {"stream": True}
    assert relay.stream_payload(payload, "responses") is payload


# ---- until_disconnect --------------------------------------------------


async def test_until_disconnect_cancels_response_finished_at_same_time() -> None:
    async def disconnect() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    observation = _FakeObservation(
        record_status="success", error_message=None, final_status=200
    )
    response = relay.RelayResponse(
        Response(b"{}"), observation, _queue(), time.monotonic() + 30
    )

    async def operation() -> relay.RelayResponse:
        return response

    with pytest.raises(asyncio.CancelledError):
        await relay.until_disconnect(_request(disconnect), operation())
    assert observation.record_status == "cancelled"
    assert observation.error_message == "client_disconnected"
    assert observation.final_status == 499
    assert observation.finished is True


# ---- RelayObservation.finish ------------------------------------------


async def test_finish_survives_close_failure(
    usage_records: list[dict[str, Any]],
) -> None:
    class BrokenUpstream:
        async def aclose(self) -> None:
            raise RuntimeError("close failed")

    observation = _observation(upstream=BrokenUpstream())
    await observation.finish()
    await observation.finish()  # 第二次直接返回

    assert observation.finished is True
    assert observation.upstream is None
    assert len(usage_records) == 1


async def test_finish_ignores_logging_failure(
    monkeypatch: pytest.MonkeyPatch, usage_records: list[dict[str, Any]]
) -> None:
    def broken_info(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("log failed")

    monkeypatch.setattr(relay.logger, "info", broken_info)
    await _observation().finish()
    assert len(usage_records) == 1


async def test_finish_logs_when_usage_task_cannot_be_scheduled(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(relay, "_usage_tasks", set())
    monkeypatch.setattr(relay, "record_usage_in_threadpool", lambda **_: None)

    with caplog.at_level("ERROR"):
        await _observation().finish()
    assert "Failed to schedule AI relay usage recording" in caplog.text


# ---- RelayStreamingResponse / RelayResponse ---------------------------


async def test_streaming_response_send_failure_is_recorded(
    usage_records: list[dict[str, Any]],
) -> None:
    async def body():
        yield b"data: {}\n\n"

    async def send(message: dict[str, Any]) -> None:
        raise RuntimeError("socket broke")

    observation = _observation(record_status="streaming")
    response = relay.RelayStreamingResponse(
        body(),
        observation=observation,
        deadline=time.monotonic() + 30,
        deadline_reason="generation_timeout",
        media_type="text/event-stream",
    )
    with pytest.raises(RuntimeError):
        await response(HTTP_SCOPE, _never_receive, send)

    assert observation.record_status == "error"
    assert observation.error_message == "upstream_stream_error"
    assert observation.final_status == 502
    assert observation.finished is True


async def test_streaming_response_finished_while_still_streaming_is_cancelled(
    usage_records: list[dict[str, Any]],
) -> None:
    async def body():
        yield b"data: {}\n\n"

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    observation = _observation(record_status="streaming")
    response = relay.RelayStreamingResponse(
        body(),
        observation=observation,
        deadline=time.monotonic() + 30,
        deadline_reason="generation_timeout",
        media_type="text/event-stream",
    )
    await response(HTTP_SCOPE, _never_receive, send)

    assert sent[0]["type"] == "http.response.start"
    assert observation.record_status == "cancelled"
    assert observation.error_message == "client_disconnected"
    assert observation.final_status == 499


async def test_relay_response_downstream_failure_is_recorded() -> None:
    async def send(message: dict[str, Any]) -> None:
        raise RuntimeError("send failed")

    observation = _FakeObservation(
        record_status="success", error_message=None, final_status=200
    )
    queue = _queue()
    response = relay.RelayResponse(
        Response(b"{}"), observation, queue, time.monotonic() + 30
    )
    with pytest.raises(RuntimeError):
        await response(HTTP_SCOPE, _never_receive, send)

    assert observation.record_status == "error"
    assert observation.error_message == "downstream_response_error"
    assert observation.final_status == 500
    assert observation.finished is True
    assert queue.requests == set()


# ---- stream_upstream_response -----------------------------------------


class _FakeStream:
    status_code = 200

    def __init__(self, chunks: list[bytes], error: Exception | None = None) -> None:
        self.chunks = chunks
        self.error = error
        self.closed = False

    async def aiter_raw(self):
        for chunk in self.chunks:
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        self.closed = True


def _stream(upstream: _FakeStream):
    return relay.stream_upstream_response(
        upstream=upstream,  # type: ignore[arg-type]
        admission_lease=None,  # type: ignore[arg-type]
        user=SimpleNamespace(id="user-1"),
        credential=SimpleNamespace(id="credential-1"),
        model_name="A",
        request_type="chat_completion",
        request_id="req-1",
        upstream_request_id=None,
        started_at=time.monotonic(),
        started_at_utc=datetime.now(timezone.utc),
    )


async def test_stream_drops_oversized_unterminated_line(
    usage_records: list[dict[str, Any]],
) -> None:
    huge = b"a" * 1_048_577
    upstream = _FakeStream([huge, b"\n"])

    chunks = [chunk async for chunk in _stream(upstream)]

    assert chunks == [huge, b"\n"]
    assert upstream.closed is True
    [record] = usage_records
    assert record["record_status"] == "error"
    assert record["error_message"] == "incomplete_stream"


async def test_stream_upstream_error_is_recorded_and_raised(
    usage_records: list[dict[str, Any]],
) -> None:
    upstream = _FakeStream([b"data: {}\n"], error=RuntimeError("reset"))

    with pytest.raises(RuntimeError):
        async for _chunk in _stream(upstream):
            pass

    [record] = usage_records
    assert record["record_status"] == "error"
    assert record["error_message"] == "upstream_stream_error"


async def test_stream_deadline_ends_stream_without_raising(
    usage_records: list[dict[str, Any]],
) -> None:
    upstream = _FakeStream([b"data: {}\n"])
    stream = relay.stream_upstream_response(
        upstream=upstream,  # type: ignore[arg-type]
        admission_lease=None,  # type: ignore[arg-type]
        user=SimpleNamespace(id="user-1"),
        credential=SimpleNamespace(id="credential-1"),
        model_name="A",
        request_type="chat_completion",
        request_id="req-1",
        upstream_request_id=None,
        started_at=time.monotonic(),
        started_at_utc=datetime.now(timezone.utc),
        deadline=time.monotonic() - 1,
    )

    assert [chunk async for chunk in stream] == []
    [record] = usage_records
    assert record["record_status"] == "error"
    assert record["error_message"] == "generation_timeout"


# ---- 模型目錄 ----------------------------------------------------------


async def test_fetch_public_models_rejects_non_list_and_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        JSONResponse({"data": "not-a-list"}),
        Response(b"not json", status_code=200),
    ]

    async def fake_models_response(request: Request) -> Response:
        return responses.pop(0)

    monkeypatch.setattr(relay, "_models_response", fake_models_response)

    assert await relay.fetch_public_models() == set()
    assert await relay.fetch_public_models() == set()


async def test_public_models_rejects_when_too_many_waiters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = relay._CatalogueState(
        loop=asyncio.get_running_loop(), waiters=relay.AI_PROXY_MAX_WAITING
    )
    monkeypatch.setattr(relay, "_catalogue", state)

    with pytest.raises(relay.AdmissionRejected) as rejected:
        await relay.public_models()
    assert rejected.value.reason == "queue_full"


@pytest.mark.parametrize(
    ("content", "expected_status"),
    [(b"not json", 502), (b"[1, 2]", 502)],
)
async def test_fetch_models_response_rejects_invalid_payloads(
    monkeypatch: pytest.MonkeyPatch, content: bytes, expected_status: int
) -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        response = await relay._fetch_models_response(_request())

    assert response.status_code == expected_status
    assert json.loads(response.body)["error"]["code"] == "invalid_upstream_response"


async def test_fetch_models_response_skips_non_object_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [1, {"id": "m", "created": 7}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        response = await relay._fetch_models_response(_request())

    assert response.status_code == 200
    assert json.loads(response.body)["data"] == [{"id": "m", "created": 7}]


# ---- _relay_attempts ---------------------------------------------------


async def _attempts(queue: relay.AdmissionQueue, ticket: relay.AdmissionTicket):
    observation = _observation(stream=False)
    response = await relay._relay_attempts(
        endpoint="chat/completions",
        request=_request(),
        payload={"model": "A"},
        observation=observation,
        queue=queue,
        ticket=ticket,
    )
    return response, observation


async def test_relay_attempts_rejects_when_queue_closed() -> None:
    queue = _queue()
    queue.closed = True

    response, observation = await _attempts(queue, queue.ticket())

    assert response.status_code == 503
    assert json.loads(response.body)["error"]["code"] == "server_busy"
    assert observation.error_message == "queue_shutdown"


async def test_relay_attempts_catalogue_timeout_is_server_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def catalogue() -> set[str]:
        return {"A"}

    monkeypatch.setattr(relay, "public_models", catalogue)
    queue = _queue()
    ticket = queue.ticket()
    ticket.wait_remaining = 0

    response, observation = await _attempts(queue, ticket)

    assert response.status_code == 503
    assert response.headers["retry-after"] == str(relay.AI_PROXY_RETRY_AFTER_SECONDS)
    assert observation.error_message == "queue_timeout"


async def test_relay_attempts_generation_deadline_after_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTime:
        now = 1_000.0

        @classmethod
        def monotonic(cls) -> float:
            return cls.now

        @staticmethod
        def time() -> float:
            return time.time()

    class Limited:
        status_code = 429
        is_success = False
        headers = httpx.Headers({"retry-after": "0"})

        async def aread(self) -> bytes:
            # 讀完 429 時 generation 預算已用完
            FakeTime.now += 500
            return json.dumps(
                {
                    "error": {
                        "message": (
                            "Deployment has all max_parallel_requests slots in use."
                        )
                    }
                }
            ).encode()

        async def aclose(self) -> None:
            return None

    class Client:
        def build_request(self, *args: Any, **kwargs: Any) -> None:
            return None

        async def send(self, request: Any, *, stream: bool) -> Limited:
            return Limited()

    async def catalogue() -> set[str]:
        return {"A"}

    ai_metrics.remember_models(["A"])
    monkeypatch.setattr(relay, "public_models", catalogue)
    monkeypatch.setattr(relay, "_get_relay_http_client", Client)
    monkeypatch.setattr(relay, "time", FakeTime)
    queue = _queue()

    response, observation = await _attempts(queue, queue.ticket())

    assert response.status_code == 503
    assert json.loads(response.body)["error"]["code"] == "upstream_unavailable"
    assert observation.error_message == "generation_timeout"
    assert observation.attempts == 1
    assert queue.active == 0
