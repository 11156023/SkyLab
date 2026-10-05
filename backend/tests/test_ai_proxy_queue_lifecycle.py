"""Lifecycle regression cases sharing the queue test helpers."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from app.services.llm_gateway import relay_service as relay
from app.services.monitoring import ai_metrics
from tests import test_ai_proxy_queue as helpers
from tests.test_ai_proxy_queue import (
    Clock,
    consume,
    generation,
    limited,
    queue,
    request,
    settle,
)

runtime = helpers.runtime


async def test_retry_capacity_reserved_before_new_admission():
    clock = Clock()
    admission = queue(active=2, waiting=3, clock=clock, timeout=100)
    tickets = [admission.ticket("A") for _ in range(3)]
    leases = [await admission.acquire(ticket) for ticket in tickets[:2]]
    waiting = asyncio.create_task(admission.acquire(tickets[2]))
    await settle(lambda: admission.waiting == 1)
    with pytest.raises(relay.AdmissionRejected, match="queue_full"):
        await admission.acquire(admission.ticket("B"))
    for lease in leases:
        admission.rate_limited(lease, "10")
    retries = [asyncio.create_task(admission.acquire(ticket)) for ticket in tickets[:2]]
    await settle(lambda: admission.waiting == 3)
    assert admission.active == 0 and len(admission._retained) == 3
    clock.value = 10
    admission.dispatch()
    probe = await retries[0]
    assert not waiting.done() and not retries[1].done()
    admission.accepted(probe)
    second = await retries[1]
    probe.release()
    third = await waiting
    second.release()
    third.release()
    assert admission.active == admission.waiting == len(admission._retained) == 0


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
async def test_non_429_errors_never_retry(monkeypatch, runtime, status):
    admission, records = runtime
    attempts = []

    async def handle(req):
        attempts.append(req)
        return httpx.Response(
            status, request=req, json={"error": {"message": "internal detail"}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, _ = request()
        response = await generation(req)
    assert response.status_code == status and len(attempts) == len(records) == 1
    assert admission.active == 0 and b"internal detail" not in response.body


async def test_retry_attempt_limit_bounds_zero_retry_after(monkeypatch, runtime):
    admission, records = runtime
    attempts = []

    async def handle(req):
        attempts.append(req)
        return limited(req)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, _ = request()
        response = await generation(req)
    assert response.status_code == 503 and len(attempts) == relay.AI_PROXY_MAX_ATTEMPTS
    assert json.loads(response.body)["error"]["code"] == "upstream_unavailable"
    assert len(records) == 1 and records[0]["error_message"] == "retry_exhausted"
    assert admission.active == admission.waiting == len(admission._retained) == 0


async def test_shutdown_cancels_nonstream_request_and_terminal_usage(
    monkeypatch, runtime
):
    admission, records = runtime
    started = asyncio.Event()

    async def handle(_req):
        started.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, _ = request()
        task = asyncio.create_task(generation(req))
        await asyncio.wait_for(started.wait(), 1)
        await admission.close()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert admission.active == admission.waiting == 0
    assert records[0]["error_message"] == "backend_shutdown" and len(records) == 1


async def test_post_headers_asgi_disconnect_closes_stream_without_retry(
    monkeypatch, runtime
):
    admission, records = runtime
    started = asyncio.Event()
    attempts = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: start\n\n"
            started.set()
            await asyncio.Event().wait()

    async def handle(req):
        attempts.append(req)
        return httpx.Response(200, request=req, stream=Body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, messages = request()
        req.scope["asgi"]["spec_version"] = "2.0"
        response = await generation(req, stream=True)
        task = asyncio.create_task(consume(response, req))
        await asyncio.wait_for(started.wait(), 1)
        messages.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(task, 1)
    assert len(attempts) == len(records) == 1 and admission.active == 0
    assert records[0]["record_status"] == "cancelled"


async def test_downstream_slow_send_obeys_total_deadline(monkeypatch, runtime):
    admission, records = runtime
    monkeypatch.setattr(relay, "AI_PROXY_REQUEST_TIMEOUT_SECONDS", 0.02)

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: output\n\n"

    async def handle(req):
        return httpx.Response(200, request=req, stream=Body())

    async def send(message):
        if message["type"] == "http.response.body":
            await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, _ = request()
        response = await generation(req, stream=True)
        await asyncio.wait_for(response(req.scope, req.receive, send), 1)
    assert admission.active == 0 and len(records) == 1
    assert records[0]["error_message"] == "request_timeout"
    assert records[0]["record_status"] == "error"


async def test_stream_probe_success_headers_release_other_model_slots_before_stream_finishes(
    monkeypatch, runtime
):
    admission, records = runtime
    attempts = []
    stream_started = asyncio.Event()
    end_stream = asyncio.Event()

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            stream_started.set()
            await end_stream.wait()
            yield b"data: [DONE]\n\n"

    async def handle(req):
        attempts.append(req)
        if len(attempts) == 1:
            return limited(req)
        if json.loads(req.content).get("stream"):
            return httpx.Response(200, request=req, stream=Body())
        return httpx.Response(200, request=req, json={"model": "A"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, _ = request()
        response = await generation(req, stream=True)
        stream_task = asyncio.create_task(consume(response, req))
        await asyncio.wait_for(stream_started.wait(), 1)
        other_req, _ = request()
        other = await generation(other_req)
        assert other.status_code == 200 and not stream_task.done()
        end_stream.set()
        await stream_task
    assert len(attempts) == 3 and len(records) == 2 and admission.active == 0


async def test_catalogue_uses_service_identity_and_bounds_model_state(monkeypatch):
    monkeypatch.setattr(ai_metrics, "_known_models", set())
    names = [f"trusted-{index}" for index in range(150)]
    calls = []

    async def handle(req):
        calls.append(req)
        return httpx.Response(
            200, request=req, json={"data": [{"id": name} for name in names]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        result = await relay.fetch_public_models()
    assert len(result) == ai_metrics.MAX_MODEL_LABELS
    assert (
        result <= set(names)
        and ai_metrics.model_label("arbitrary-client-input", served=False) == "other"
    )
    assert (
        calls[0].headers["authorization"]
        == f"Bearer {relay.ai_api_settings.ai_api_api_key}"
    )


async def test_catalogue_single_flight_survives_one_waiter_cancel(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    counts = []

    async def fetch():
        counts.append(1)
        started.set()
        await release.wait()
        return {"A"}

    monkeypatch.setattr(relay, "fetch_public_models", fetch)
    monkeypatch.setattr(relay, "_catalogue_loop", None)
    first = asyncio.create_task(relay.public_models())
    await started.wait()
    second = asyncio.create_task(relay.public_models())
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == {"A"}
    assert await relay.public_models() == {"A"} and len(counts) == 1
    assert relay._catalogue_waiters == 0


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("phase", ["headers", "body"])
async def test_generation_deadline_covers_prefill_and_stalled_body(
    monkeypatch, runtime, stream, phase
):
    admission, records = runtime
    monkeypatch.setattr(relay, "AI_PROXY_GENERATION_TIMEOUT_SECONDS", 0.03)
    attempts = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b"unused"

    async def handle(req):
        attempts.append(req)
        if phase == "headers":
            await asyncio.Event().wait()
        return httpx.Response(200, request=req, stream=Body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, _ = request()
        response = await generation(req, stream=stream)
        if stream and phase == "body":
            await consume(response, req)
        expected = 200 if stream and phase == "body" else 503
        assert response.status_code == expected
    assert len(attempts) == len(records) == 1
    assert records[0]["record_status"] == "error"
    assert records[0]["error_message"] == "generation_timeout"
    assert admission.active == admission.waiting == 0


async def test_catalogue_discovery_uses_same_bounded_reservation(monkeypatch, runtime):
    admission, records = runtime
    started = asyncio.Event()
    release = asyncio.Event()

    async def catalogue():
        started.set()
        await release.wait()
        return {"A"}

    async def handle(req):
        return httpx.Response(200, request=req, json={"model": "A"})

    monkeypatch.setattr(relay, "public_models", catalogue)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        tasks = []
        for _ in range(8):
            req, _ = request()
            tasks.append(asyncio.create_task(generation(req)))
        await settle(lambda: len(admission._retained) == 8)
        req, _ = request()
        rejected = await generation(req)
        assert rejected.status_code == 503
        assert json.loads(rejected.body)["error"]["code"] == "server_busy"
        assert records[-1]["error_message"] == "queue_full"
        release.set()
        assert all(
            response.status_code == 200 for response in await asyncio.gather(*tasks)
        )
    assert (
        len(records) == 9
        and admission.active == admission.waiting == len(admission._retained) == 0
    )


async def test_slow_usage_write_cannot_extend_request_deadline(monkeypatch, runtime):
    _, records = runtime
    release = asyncio.Event()
    monkeypatch.setattr(relay, "AI_PROXY_REQUEST_TIMEOUT_SECONDS", 0.05)

    async def write(**kwargs):
        await release.wait()
        records.append(kwargs)

    async def handle(req):
        return httpx.Response(200, request=req, json={"model": "A"})

    monkeypatch.setattr(relay, "record_usage_in_threadpool", write)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, _ = request()
        started = time.monotonic()
        response = await asyncio.wait_for(generation(req), 0.5)
        assert time.monotonic() - started < 0.4
    assert response.status_code == 200 and not records
    release.set()
    await settle(lambda: len(records) == 1)


@pytest.mark.parametrize("disconnect", [False, True])
async def test_json_delivery_timeout_and_disconnect_are_terminal_once(
    monkeypatch, runtime, disconnect
):
    admission, records = runtime
    monkeypatch.setattr(relay, "AI_PROXY_REQUEST_TIMEOUT_SECONDS", 0.03)

    async def handle(req):
        return httpx.Response(200, request=req, json={"model": "A"})

    async def send(message):
        if message["type"] == "http.response.body":
            if disconnect:
                raise OSError("disconnected")
            await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, _ = request()
        response = await relay.relay_generation(
            endpoint="chat/completions",
            request=req,
            payload={"model": "A"},
            model_name="A",
            user=SimpleNamespace(id="user"),
            credential=SimpleNamespace(id="key"),
        )
        if disconnect:
            with pytest.raises(OSError):
                await response(req.scope, req.receive, send)
        else:
            await asyncio.wait_for(response(req.scope, req.receive, send), 1)
    assert len(records) == 1 and admission.active == 0
    assert records[0]["record_status"] == ("cancelled" if disconnect else "error")
    assert records[0]["error_message"] == (
        "client_disconnected" if disconnect else "request_timeout"
    )


async def test_json_delivery_asgi_disconnect_is_terminal_once(monkeypatch, runtime):
    admission, records = runtime
    body_started = asyncio.Event()
    release_body = asyncio.Event()

    async def handle(req):
        return httpx.Response(200, request=req, json={"model": "A"})

    async def send(message):
        if message["type"] == "http.response.body":
            body_started.set()
            await release_body.wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
        req, messages = request()
        response = await relay.relay_generation(
            endpoint="chat/completions",
            request=req,
            payload={"model": "A"},
            model_name="A",
            user=SimpleNamespace(id="user"),
            credential=SimpleNamespace(id="key"),
        )
        delivery = asyncio.create_task(response(req.scope, req.receive, send))
        await asyncio.wait_for(body_started.wait(), 1)
        messages.put_nowait({"type": "http.disconnect"})
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(delivery, 1)
    assert len(records) == 1 and admission.active == 0
    assert records[0]["record_status"] == "cancelled"
    assert records[0]["error_message"] == "client_disconnected"


async def test_catalogue_reservation_is_counted_as_waiting_metric(monkeypatch):
    admission = queue(active=2, waiting=2)
    global_samples = []
    monkeypatch.setattr(
        ai_metrics,
        "update_proxy_admission",
        lambda **values: global_samples.append(values),
    )
    ticket = admission.ticket("A")
    admission.reserve(ticket)
    assert global_samples[-1] == {"active": 0, "waiting": 1}
    lease = await admission.acquire(ticket)
    assert global_samples[-1] == {"active": 1, "waiting": 0}
    lease.release()
    assert global_samples[-1] == {"active": 0, "waiting": 0}
