"""Backend scheduling, retry and real ASGI disconnect tests; no DB/live model."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import ClientDisconnect, Request

from app.api.routes import ai_proxy
from app.services.llm_gateway import relay_service as relay
from app.services.monitoring import ai_metrics


async def settle(predicate) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate()


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


def queue(
    *,
    active=2,
    waiting=8,
    model_active=2,
    clock=time.monotonic,
    timeout=1,
):
    return relay.AdmissionQueue(
        max_active=active,
        max_waiting=waiting,
        model_max_active=model_active,
        wait_timeout_seconds=timeout,
        clock=clock,
    )


@pytest.fixture
def runtime(monkeypatch):
    records = []
    admission = queue()

    async def catalogue():
        return {"A", "B"}

    async def record(**kwargs):
        records.append(kwargs)

    monkeypatch.setattr(relay, "public_models", catalogue)
    monkeypatch.setattr(relay, "record_usage_in_threadpool", record)
    monkeypatch.setattr(relay, "_get_admission_queue", lambda: admission)
    ai_metrics.remember_models(["A", "B"])
    return admission, records


def request(*, payload=None):
    messages = asyncio.Queue()
    messages.put_nowait(
        {
            "type": "http.request",
            "body": json.dumps(payload or {}).encode(),
            "more_body": False,
        }
    )
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/ai-proxy/chat/completions",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"x-request-id", b"campus-id"),
        ],
        "server": ("testserver", 80),
        "client": ("testclient", 5000),
        "asgi": {"version": "3.0", "spec_version": "2.4"},
    }
    return Request(scope, messages.get), messages


async def generation(req, *, model="A", stream=False):
    response = await relay.relay_generation(
        endpoint="chat/completions",
        request=req,
        payload={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": stream,
        },
        model_name=model,
        user=SimpleNamespace(id="user"),
        credential=SimpleNamespace(id="key"),
    )
    if not isinstance(response, relay.RelayStreamingResponse):
        await consume(response, req)
    return response


async def consume(response, req):
    sent = []

    async def send(message):
        sent.append(message)

    await response(req.scope, req.receive, send)
    return b"".join(message.get("body", b"") for message in sent)


def limited(
    req,
    *,
    after="0",
    message="Model rate limit exceeded. RPM limit=10, current usage=11",
):
    return httpx.Response(
        429,
        request=req,
        headers={"retry-after": after},
        json={"error": {"message": message}},
    )


async def test_short_completion_immediately_replaces_slot_without_waiting_for_long_request():
    admission = queue(active=2)
    long = await admission.acquire(admission.ticket("A"))
    short = await admission.acquire(admission.ticket("A"))
    third = asyncio.create_task(admission.acquire(admission.ticket("A")))
    await settle(lambda: admission.waiting == 1)
    short.release()
    replacement = await third
    assert not long.released
    assert admission.active == 2 and admission.waiting == 0
    replacement.release()
    long.release()
    assert admission.active == 0


async def test_model_cap_and_fifo_allow_other_models_to_bypass_blocked_head():
    admission = queue(active=3, model_active=1)
    first = await admission.acquire(admission.ticket("A"))
    second = asyncio.create_task(admission.acquire(admission.ticket("A")))
    await settle(lambda: admission.waiting == 1)
    third = asyncio.create_task(admission.acquire(admission.ticket("A")))
    await settle(lambda: admission.waiting == 2)
    other = await admission.acquire(admission.ticket("B"))
    assert admission.active == 2 and admission._models["A"].active == 1
    first.release()
    second_lease = await second
    assert not third.done()
    second_lease.release()
    third_lease = await third
    other.release()
    third_lease.release()
    assert admission.active == admission.waiting == 0


async def test_shared_cooldown_single_probe_stale_success_and_probe_failure():
    clock = Clock()
    admission = queue(active=3, clock=clock, timeout=100)
    stale = await admission.acquire(admission.ticket("A"))
    ticket = admission.ticket("A")
    rejected = await admission.acquire(ticket)
    admission.rate_limited(rejected, "5")
    retry = asyncio.create_task(admission.acquire(ticket))
    fresh = asyncio.create_task(admission.acquire(admission.ticket("A")))
    await settle(lambda: admission.waiting == 2)
    other = await admission.acquire(admission.ticket("B"))
    admission.accepted(stale)
    assert admission._models["A"].recovering
    clock.value = 5
    admission.dispatch()
    probe = await retry
    assert probe.probe and not fresh.done()
    admission.rate_limited(probe, "10")
    retried = asyncio.create_task(admission.acquire(ticket))
    await settle(lambda: admission.waiting == 2)
    assert admission._models["A"].cooldown_until == 15
    clock.value = 14
    admission.dispatch()
    assert not retried.done() and not fresh.done()
    clock.value = 15
    admission.dispatch()
    next_probe = await retried
    admission.accepted(next_probe)
    assert not admission._models["A"].recovering
    stale.release()
    following = await fresh
    next_probe.release()
    following.release()
    other.release()
    assert admission.active == admission.waiting == 0


async def test_probe_cancel_returns_probe_right_and_handed_slot():
    clock = Clock()
    admission = queue(clock=clock)
    ticket = admission.ticket("A")
    initial = await admission.acquire(ticket)
    admission.rate_limited(initial, "1")
    pending = asyncio.create_task(admission.acquire(ticket))
    await settle(lambda: admission.waiting == 1)
    clock.value = 1
    admission.dispatch()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert admission.active == 0 and admission._models["A"].probe is None
    replacement = await admission.acquire(admission.ticket("A"))
    assert replacement.probe
    replacement.release()


async def test_queue_budget_not_reset_on_requeue_and_deadline_removes_waiter():
    clock = Clock()
    admission = queue(clock=clock, timeout=10)
    ticket = admission.ticket("A", deadline=12)
    first = await admission.acquire(ticket)
    admission.rate_limited(first, "4")
    retry = asyncio.create_task(admission.acquire(ticket))
    await settle(lambda: admission.waiting == 1)
    clock.value = 4
    admission.dispatch()
    probe = await retry
    assert ticket.wait_remaining == 6
    admission.rate_limited(probe, "20")
    again = asyncio.create_task(admission.acquire(ticket))
    await settle(lambda: admission.waiting == 1)
    clock.value = 13
    admission.dispatch()
    with pytest.raises(relay.AdmissionRejected):
        await again
    assert admission.waiting == admission.active == 0


async def test_shutdown_wakes_waiters_and_refuses_new_admission():
    admission = queue(active=1)
    first = await admission.acquire()
    pending = asyncio.create_task(admission.acquire())
    await settle(lambda: admission.waiting == 1)
    await admission.close()
    with pytest.raises(relay.AdmissionRejected, match="shutdown"):
        await pending
    with pytest.raises(relay.AdmissionRejected, match="shutdown"):
        await admission.acquire()
    first.release()
    assert admission.waiting == admission.active == 0


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2", 2),
        ("0", 0),
        ("-1", None),
        ("nan", None),
        ("inf", None),
        ("invalid", None),
        (None, None),
    ],
)
def test_retry_after_seconds(value, expected):
    assert relay.retry_after_seconds(value) == expected


def test_retry_after_http_date(monkeypatch):
    monkeypatch.setattr(relay.time, "time", lambda: 1000)
    date = format_datetime(datetime.fromtimestamp(1012, timezone.utc), usegmt=True)
    assert relay.retry_after_seconds(date) == 12


async def test_bounded_backoff_and_cooldown_never_shortened(monkeypatch):
    clock = Clock()
    admission = queue(clock=clock)
    monkeypatch.setattr(relay.random, "uniform", lambda *_: 1.0)
    delays = []
    for _ in range(5):
        lease = await admission.acquire(admission.ticket("A"))
        delays.append(admission.rate_limited(lease, "invalid"))
        clock.value += delays[-1]
    assert delays == [5, 10, 20, 30, 30]


@pytest.mark.parametrize(
    "message",
    [
        "Rate limit exceeded for key",
        "Team budget exceeded",
        "provider quota exceeded",
        "Model rate limit exceeded. TPM limit=10, current usage=11",
        "untrusted: Model rate limit exceeded. RPM limit=10, current usage=11",
    ],
)
def test_unconfirmed_or_non_rpm_limits_are_not_retried(message):
    assert (
        relay.recoverable_model_limit(
            json.dumps({"error": {"message": message}}).encode()
        )
        is None
    )


@pytest.mark.parametrize(
    "message,reason",
    [
        (
            "litellm.RateLimitError: Model rate limit exceeded. RPM limit=10, current usage=11",
            "model_rpm",
        ),
        (
            "Deployment over user-defined ratelimit. rpm limit=10. current usage=11. id=x, model_group=A",
            "model_rpm",
        ),
        (
            "Deployment has all max_parallel_requests slots in use. Deployment model_group=A",
            "model_parallel",
        ),
    ],
)
def test_known_pre_call_limit_signatures(message, reason):
    assert (
        relay.recoverable_model_limit(
            json.dumps({"error": {"message": message}}).encode()
        )
        == reason
    )


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("model", ["A", "other"])
async def test_429_retry_preserves_payload_model_request_id_and_records_once(
    monkeypatch, runtime, stream, model
):
    admission, records = runtime
    attempts = []
    responses = []

    async def catalogue():
        return {"A", "B", "other"}

    monkeypatch.setattr(relay, "public_models", catalogue)

    class SSE(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\ndata: [DONE]\n\n'

    async def handle(req):
        attempts.append(req)
        if len(attempts) < 3:
            result = limited(req)
        elif stream:
            result = httpx.Response(
                200,
                request=req,
                headers={"content-type": "text/event-stream"},
                stream=SSE(),
            )
        else:
            result = httpx.Response(
                200,
                request=req,
                json={
                    "model": "A",
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                },
            )
        responses.append(result)
        return result

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, _ = request()
        response = await generation(req, stream=stream, model=model)
        assert response.status_code == 200
        if stream:
            assert not records
            assert b"[DONE]" in await consume(response, req)
        assert len(attempts) == 3 and all(r.is_closed for r in responses)
    assert len({r.content for r in attempts}) == 1
    assert all(r.headers["x-request-id"] == "campus-id" for r in attempts)
    assert all(json.loads(r.content)["model"] == model for r in attempts)
    assert len(records) == 1 and records[0]["record_status"] == "success"
    assert records[0]["input_tokens"] == 2
    assert records[0]["stream"] is stream
    assert admission.active == admission.waiting == 0


async def test_credential_limiter_called_once_for_internal_retries(
    monkeypatch, runtime
):
    _, records = runtime
    counts = {"limiter": 0, "upstream": 0}

    async def limiter(**_kwargs):
        counts["limiter"] += 1

    async def handle(req):
        counts["upstream"] += 1
        return (
            limited(req)
            if counts["upstream"] == 1
            else httpx.Response(200, request=req, json={"model": "A"})
        )

    monkeypatch.setattr(ai_proxy, "_enforce_rate_limit", limiter)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, _ = request(payload={"model": "A", "messages": []})
        response = await ai_proxy._relay_generation(
            endpoint="chat/completions",
            request=req,
            user_and_credential=(SimpleNamespace(id="user"), SimpleNamespace(id="key")),
        )
        await consume(response, req)
    assert response.status_code == 200 and counts == {"limiter": 1, "upstream": 2}
    assert len(records) == 1


@pytest.mark.parametrize(
    "model,message",
    [
        ("A", "Rate limit exceeded for service key"),
        ("unlisted", "Model rate limit exceeded. RPM limit=10, current usage=11"),
    ],
)
async def test_unknown_limit_or_model_keeps_sanitized_upstream_error(
    monkeypatch, runtime, model, message, caplog
):
    admission, records = runtime
    attempts = []

    async def handle(req):
        attempts.append(req)
        return limited(req, message=message + " secret-marker http://internal")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, _ = request()
        response = await generation(req, model=model)
    assert response.status_code == 429 and len(attempts) == len(records) == 1
    assert b"secret-marker" not in response.body and "secret-marker" not in caplog.text
    assert "unlisted" not in admission._models


@pytest.mark.parametrize(
    "phase", ["waiting", "nonstream_headers", "stream_headers", "nonstream_body"]
)
async def test_asgi_disconnect_cancels_waiter_or_upstream_and_records_once(
    monkeypatch, runtime, phase
):
    admission, records = runtime
    started = asyncio.Event()
    stopped = asyncio.Event()
    held = []
    if phase == "waiting":
        held = [await admission.acquire(), await admission.acquire()]

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            try:
                await asyncio.Event().wait()
                yield b"unused"
            finally:
                stopped.set()

    async def handle(req):
        if phase == "nonstream_body":
            return httpx.Response(200, request=req, stream=Body())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, messages = request()
        await req.body()
        task = asyncio.create_task(generation(req, stream=phase == "stream_headers"))
        if phase == "waiting":
            await settle(lambda: admission.waiting == 1)
        else:
            await asyncio.wait_for(started.wait(), 1)
        messages.put_nowait({"type": "http.disconnect"})
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    for lease in held:
        lease.release()
    assert admission.active == admission.waiting == 0
    assert len(records) == 1 and records[0]["record_status"] == "cancelled"
    if phase != "waiting":
        assert stopped.is_set()


async def test_stream_headers_send_disconnect_before_generator_starts_cleans_up(
    monkeypatch, runtime
):
    admission, records = runtime

    async def handle(req):
        return httpx.Response(200, request=req, content=b"data: [DONE]\n\n")

    async def broken_send(_message):
        raise OSError("client disconnected")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, _ = request()
        response = await generation(req, stream=True)
        with pytest.raises(ClientDisconnect):
            await response(req.scope, req.receive, broken_send)
    assert admission.active == 0
    assert len(records) == 1 and records[0]["record_status"] == "cancelled"


@pytest.mark.parametrize("boundary", ["queue", "generation", "request"])
async def test_deadlines_are_distinct_and_never_reset_across_retry(
    monkeypatch, runtime, boundary
):
    admission, records = runtime
    monkeypatch.setattr(
        relay, "AI_PROXY_REQUEST_TIMEOUT_SECONDS", 0.04 if boundary == "request" else 1
    )
    monkeypatch.setattr(
        relay,
        "AI_PROXY_GENERATION_TIMEOUT_SECONDS",
        0.02 if boundary == "generation" else 1,
    )
    admission.wait_timeout_seconds = 0.02 if boundary == "queue" else 1
    attempts = []

    async def handle(req):
        attempts.append(req)
        return limited(req, after="10")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, _ = request()
        response = await generation(req)
    assert response.status_code == 503 and len(attempts) == 1
    assert len(records) == 1 and admission.waiting == admission.active == 0
    expected = "server_busy" if boundary == "queue" else "upstream_unavailable"
    assert json.loads(response.body)["error"]["code"] == expected


async def test_continuous_sse_cannot_evade_generation_deadline(monkeypatch, runtime):
    admission, records = runtime
    monkeypatch.setattr(relay, "AI_PROXY_GENERATION_TIMEOUT_SECONDS", 0.02)
    starts = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.001)
                yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'

    async def handle(req):
        starts.append(req)
        return httpx.Response(200, request=req, stream=Body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, _ = request()
        response = await generation(req, stream=True)
        content = await consume(response, req)
    assert content and len(starts) == 1 and response.status_code == 200
    assert records[0]["error_message"] == "generation_timeout"
    assert records[0]["record_status"] != "success"
    assert admission.active == 0
