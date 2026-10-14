"""Terminal queue observation through the real relay with local transports."""

import asyncio

import httpx
import pytest

from app.services.llm_gateway import relay_service as relay
from tests import test_ai_latency_observation as observations
from tests import test_ai_proxy_queue as helpers
from tests.test_ai_latency_observation import sample

runtime = helpers.runtime
registry = observations.registry


@pytest.mark.parametrize(
    "terminal",
    [
        "wait_cancel",
        "queue_deadline",
        "shutdown",
        "stream_complete",
        "stream_truncated",
        "catalogue",
    ],
)
async def test_terminal_queue_sample_and_summary_once(
    registry, runtime, monkeypatch, caplog, terminal
):
    admission, records = runtime
    held = []
    clock = helpers.Clock()
    if terminal in {"wait_cancel", "queue_deadline", "shutdown"}:
        admission.clock = clock
        held = [await admission.acquire(), await admission.acquire()]
    if terminal == "queue_deadline":
        admission.wait_timeout_seconds = 0.01

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"OK"}}],"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\n'
            if terminal == "stream_complete":
                yield b"data: [DONE]\n\n"

    async def handle(request):
        return httpx.Response(200, request=request, stream=Body())

    async def catalogue():
        await asyncio.sleep(0.04)
        return {"A"}

    if terminal == "catalogue":
        monkeypatch.setattr(relay, "public_models", catalogue)
    with caplog.at_level("INFO"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            monkeypatch.setattr(relay, "_get_relay_http_client", lambda: client)
            request, messages = helpers.request()
            stream = terminal.startswith("stream_")
            task = asyncio.create_task(helpers.generation(request, stream=stream))
            if held:
                await helpers.settle(lambda: bool(admission._waiters))
                clock.value = 5
            if terminal in {"wait_cancel", "shutdown"}:
                if terminal == "shutdown":
                    await admission.close()
                else:
                    messages.put_nowait({"type": "http.disconnect"})
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
            else:
                response = await asyncio.wait_for(task, 1)
                if stream:
                    await helpers.consume(response, request)
    for lease in held:
        lease.release()
    logs = [
        r for r in caplog.records if getattr(r, "event", None) == "ai_relay_finished"
    ]
    assert len(logs) == len(records) == 1
    assert sample(registry, "skylab_ai_proxy_queue_wait_seconds_count", model="A") == 1
    queue_sum = sample(registry, "skylab_ai_proxy_queue_wait_seconds_sum", model="A")
    assert queue_sum == pytest.approx(logs[0].queue_ms / 1000, abs=0.000001)
    if terminal in {"wait_cancel", "queue_deadline", "shutdown"}:
        assert queue_sum > 0
    else:
        assert queue_sum == 0
    if terminal == "catalogue":
        assert logs[0].catalogue_ms > 0
    expected = (
        "cancelled"
        if terminal in {"wait_cancel", "shutdown"}
        else "error"
        if terminal in {"queue_deadline", "stream_truncated"}
        else "success"
    )
    assert logs[0].outcome == records[0]["record_status"] == expected
    assert admission.active == admission.waiting == 0
