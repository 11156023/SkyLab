"""Real worker/ASGI regressions; no production DB, Redis or inference traffic."""

import asyncio
import threading
from types import SimpleNamespace

import anyio
import httpx
import pytest
from fastapi import Depends, FastAPI

from app.ai import monitoring
from app.api import ai_capacity
from app.api.deps import ai_api_key, auth
from app.core.db import run_db_in_threadpool
from app.services.llm_gateway import usage_writer


def test_unit_test_db_guard_blocks_application_engine():
    from app.core.db import engine

    with pytest.raises(RuntimeError, match="isolated engine"):
        engine.connect()


def test_seed_guard_skips_units_and_rejects_non_test_target(monkeypatch):
    from app.core.config import settings
    from tests import conftest

    touched = []
    monkeypatch.setattr(conftest, "Session", lambda *_: touched.append(True))
    seed = conftest._seed_first_superuser.__wrapped__
    seed(
        SimpleNamespace(
            session=SimpleNamespace(items=[SimpleNamespace(fixturenames=[])])
        )
    )
    monkeypatch.delenv("PYTEST_ALLOW_NON_TEST_DB", raising=False)
    monkeypatch.setattr(settings, "POSTGRES_SERVER", "example.invalid")
    monkeypatch.setattr(settings, "POSTGRES_DB", "app")

    def item_for(function):
        return SimpleNamespace(
            _fixtureinfo=SimpleNamespace(
                name2fixturedefs={"db": [SimpleNamespace(func=function)]}
            )
        )

    local_db_item = item_for(lambda: None)
    assert not conftest._uses_application_db(local_db_item)
    seed(SimpleNamespace(session=SimpleNamespace(items=[local_db_item])))
    canonical_db_item = item_for(conftest.db.__wrapped__)
    assert conftest._uses_application_db(canonical_db_item)
    with pytest.raises(RuntimeError, match="non-test database"):
        seed(SimpleNamespace(session=SimpleNamespace(items=[canonical_db_item])))
    assert not touched


async def settle(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.fixture
def writer(monkeypatch):
    instance = usage_writer.UsageWriter()
    monkeypatch.setattr(usage_writer, "_writer", instance)
    yield instance
    instance.executor.shutdown(wait=True, cancel_futures=True)


def user():
    return SimpleNamespace(
        id="u",
        email="u@example.com",
        is_active=True,
        token_version=0,
        totp_required=False,
        totp_enabled=False,
    )


class AuthSession:
    def get(self, *_):
        return user()

    def in_transaction(self):
        return False

    def close(self):
        pass


async def test_usage_full_does_not_block_real_http_and_ws_auth(writer, monkeypatch):
    gate = threading.Event()
    futures = [writer.submit(lambda: gate.wait(3), source="api_key") for _ in range(40)]
    try:
        await settle(lambda: writer.active == 4)
        assert len(writer.pending) == 40
        assert writer.submit(lambda: None, source="platform") is None

        async def validate(_):
            return SimpleNamespace(sub="u", ver=0)

        monkeypatch.setattr(auth, "_validate_access_token", validate)
        monkeypatch.setattr(auth, "Session", lambda _: AuthSession())
        http_user, (ws_user, session) = await asyncio.wait_for(
            asyncio.gather(
                auth.get_current_user(
                    AuthSession(),
                    "token",
                    SimpleNamespace(url=SimpleNamespace(path="/api/v1/users/me")),
                ),
                auth.get_ws_current_user(SimpleNamespace(), "token"),
            ),
            timeout=1,
        )
        assert http_user.id == ws_user.id == "u"
        assert not gate.is_set()
        assert anyio.to_thread.current_default_thread_limiter().borrowed_tokens == 0
        session.close()
    finally:
        gate.set()
        await asyncio.gather(*(asyncio.wrap_future(f) for f in futures))
    await settle(lambda: not writer.pending)


async def test_cancelled_usage_waiters_keep_actual_worker_capacity(writer):
    gate = threading.Event()
    waiters = [
        asyncio.create_task(
            usage_writer.write_usage(lambda: gate.wait(3), source="api_key")
        )
        for _ in range(4)
    ]
    try:
        await settle(lambda: writer.active == 4)
        for task in waiters:
            task.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        assert writer.active == len(writer.pending) == 4
        queued = [writer.submit(lambda: None, source="platform") for _ in range(36)]
        assert writer.submit(lambda: None, source="api_key") is None
        assert writer.active == 4
    finally:
        gate.set()
    await asyncio.gather(*(asyncio.wrap_future(f) for f in queued))
    await settle(lambda: not writer.pending)


async def test_shutdown_cancels_only_queued_usage(writer, monkeypatch):
    monkeypatch.setattr(usage_writer, "WAIT_SECONDS", 0.02)
    gate = threading.Event()
    futures = [
        writer.submit(lambda: gate.wait(3), source="platform") for _ in range(40)
    ]
    try:
        await settle(lambda: writer.active == 4)
        await writer.close()
        assert writer.closed and writer.active == len(writer.pending) == 4
        assert sum(f.cancelled() for f in futures) == 36
        assert writer.submit(lambda: None, source="api_key") is None
        with pytest.raises(RuntimeError, match="not drained"):
            usage_writer.start_usage_writer()
    finally:
        gate.set()
    await settle(lambda: not writer.pending)


@pytest.mark.parametrize("kind", ["usage", "auth"])
async def test_cancelled_shutdown_still_discards_queued_work(writer, monkeypatch, kind):
    gate = threading.Event()
    entered = threading.Event()
    queued_calls = []

    def blocked():
        entered.set()
        gate.wait(3)

    if kind == "usage":
        monkeypatch.setattr(usage_writer, "WAIT_SECONDS", 10)
        futures = [writer.submit(blocked, source="platform") for _ in range(4)]
        queued = writer.submit(lambda: queued_calls.append(True), source="platform")
        close = writer.close
        tasks = []
    else:
        monkeypatch.setattr(ai_capacity, "MAX_DB_ACTIVE", 1)
        tasks = [asyncio.create_task(ai_capacity.run_ai_db(blocked))]
        await settle(entered.is_set)
        tasks.append(
            asyncio.create_task(
                ai_capacity.run_ai_db(lambda: queued_calls.append(True))
            )
        )
        await settle(lambda: len(ai_capacity._pending) == 2)
        futures = []
        queued = next(f for f in ai_capacity._pending if not f.running())
        close = ai_capacity.close_ai_capacity
    closing = asyncio.create_task(close())
    try:
        await settle(lambda: writer.closed if kind == "usage" else ai_capacity._closed)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert queued.cancelled()
        assert queued_calls == []
    finally:
        gate.set()
        await asyncio.gather(
            *tasks, *(asyncio.wrap_future(f) for f in futures), return_exceptions=True
        )
        if kind == "usage":
            await settle(lambda: not writer.pending)
        else:
            await settle(lambda: not ai_capacity._pending)


async def test_relay_backlog_drop_is_counted_once(monkeypatch):
    import time
    from datetime import datetime, timezone

    from app.core import metrics
    from app.services.llm_gateway import relay_service as relay

    monkeypatch.setattr(relay, "AI_PROXY_MAX_WAITING", 0)
    counter = metrics.AI_USAGE_WRITES.labels(source="api_key", result="dropped")
    before = counter._value.get()
    observation = relay.RelayObservation(
        user=user(),
        credential=SimpleNamespace(id="key"),
        model_name="test",
        request_type="chat",
        request_id="drop-test",
        stream=False,
        started_at=time.monotonic(),
        started_at_utc=datetime.now(timezone.utc),
    )
    await observation.finish()
    await observation.finish()
    assert counter._value.get() == before + 1


async def test_platform_usage_never_uses_or_commits_request_session(
    writer, monkeypatch
):
    gate = threading.Event()
    observed = []
    caller_thread = threading.get_ident()

    class UsageSession:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    request_session = object()
    independent = UsageSession()
    monkeypatch.setattr(monitoring, "Session", lambda _: independent)

    def record(**kwargs):
        observed.append((threading.get_ident(), kwargs))
        gate.wait(3)

    monkeypatch.setattr(monitoring.ai_gateway_service, "record_template_call", record)
    values = {"request_id": "call-1", "prompt_tokens": 3}
    monitoring.record_ai_template_call(
        session=request_session,
        user_id="u",
        call_type="ai_help",
        model_name="test",
        metrics=values,
    )
    values["prompt_tokens"] = 999
    try:
        await settle(lambda: bool(observed))
        thread, kwargs = observed[0]
        assert thread != caller_thread
        assert kwargs["session"] is independent
        assert kwargs["input_tokens"] == 3
        assert not gate.is_set()
    finally:
        gate.set()
    await settle(lambda: not writer.pending)


async def test_session_cannot_close_while_cancelled_db_worker_is_running():
    gate = threading.Event()
    entered = threading.Event()
    closed = []

    def query():
        entered.set()
        gate.wait(3)

    async def request():
        try:
            await run_db_in_threadpool(query)
        finally:
            closed.append(True)

    task = asyncio.create_task(request())
    try:
        await settle(entered.is_set)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done() and not closed
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and not closed
    finally:
        gate.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]


@pytest.mark.parametrize("scope_kind", ["move_on_after", "fail_after"])
async def test_db_worker_drains_under_level_cancellation_without_busy_loop(
    monkeypatch, scope_kind
):
    import time

    completed = []
    original_shield = asyncio.shield
    waits = []

    def shield(task):
        waits.append(True)
        return original_shield(task)

    def query():
        time.sleep(0.06)
        completed.append(True)

    monkeypatch.setattr(asyncio, "shield", shield)
    if scope_kind == "fail_after":
        with pytest.raises(TimeoutError):
            with anyio.fail_after(0.01):
                await run_db_in_threadpool(query)
    else:
        with anyio.move_on_after(0.01) as scope:
            await run_db_in_threadpool(query)
        assert scope.cancelled_caught
    assert completed == [True]
    assert len(waits) == 2


async def test_full_asgi_ingress_rejects_before_auth_and_body(monkeypatch):
    ai_capacity.start_ai_capacity()
    gate = threading.Event()
    entered = []

    def authenticate(_):
        entered.append(True)
        gate.wait(3)
        return user(), SimpleNamespace()

    monkeypatch.setattr(ai_api_key, "_authenticate", authenticate)
    app = FastAPI()
    app.add_middleware(ai_capacity.AIIngressMiddleware)

    @app.post("/api/v1/ai-proxy/chat/completions")
    async def generate(identity=Depends(ai_api_key.authenticate_ai_api_key)):
        return {"ok": True}

    @app.get("/ordinary")
    async def ordinary():
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        tasks = [
            asyncio.create_task(
                client.post(
                    "/api/v1/ai-proxy/chat/completions",
                    headers={"Authorization": "Bearer fake"},
                )
            )
            for _ in range(64)
        ]
        try:
            await settle(lambda: len(ai_capacity._pending) == 64)
            await settle(lambda: len(entered) == 8)
            rejected = await client.post("/api/v1/ai-proxy/chat/completions")
            assert rejected.status_code == 503
            assert rejected.json()["error"]["code"] == "server_busy"
            assert rejected.headers["retry-after"] == "1"
            assert (await client.get("/ordinary")).status_code == 200
            assert len(entered) == 8
            assert anyio.to_thread.current_default_thread_limiter().borrowed_tokens == 0
            # Cancelled queued auth must not become an unbounded orphan queue.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            assert len(ai_capacity._pending) == 64
            assert len(entered) == 8
            for _ in range(3):
                rejected = await client.post(
                    "/api/v1/ai-proxy/chat/completions",
                    headers={"Authorization": "Bearer fake"},
                )
                assert rejected.status_code == 503
                assert len(ai_capacity._pending) == 64
                assert ai_capacity._executor._work_queue.qsize() == 56
        finally:
            gate.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await settle(lambda: not ai_capacity._pending)
            assert len(entered) == 8
            await ai_capacity.close_ai_capacity()
            ai_capacity.start_ai_capacity()


async def test_body_ingress_lifetime_and_prefix_bypass(monkeypatch):
    monkeypatch.setattr(ai_capacity, "MAX_INGRESS", 2)
    gate = asyncio.Event()
    entered = []

    async def app(scope, receive, send):
        entered.append(scope["path"])
        if scope["path"] == "/api/v1/ai-proxy/responses":
            await receive()

    middleware = ai_capacity.AIIngressMiddleware(app)

    async def receive():
        await gate.wait()
        return {"type": "http.disconnect"}

    async def send(_):
        pass

    scope = {"type": "http", "method": "POST", "path": "/api/v1/ai-proxy/responses"}
    tasks = [asyncio.create_task(middleware(scope, receive, send)) for _ in range(2)]
    try:
        await settle(lambda: middleware.active == 2)
        sent = []

        async def capture(message):
            sent.append(message)

        await middleware(scope, receive, capture)
        assert sent[0]["status"] == 503 and len(entered) == 2
        for path in ("/api/v1/ai-api/me", "/api/v1/ai-proxy-other", "/ws/jobs"):
            await middleware({**scope, "path": path}, receive, send)
        assert len(entered) == 5
        await middleware(
            {**scope, "method": "OPTIONS", "path": "/api/v1/ai-proxy/models"},
            receive,
            send,
        )
        assert len(entered) == 6 and middleware.active == 2
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert middleware.active == 0
    finally:
        gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("kind", ["classroom", "course"])
async def test_actual_asgi_ws_db_lookup_keeps_http_loop_available(monkeypatch, kind):
    import uuid

    from fastapi import WebSocket

    from app.api.websocket import classroom, course_progress

    gate = threading.Event()
    entered = threading.Event()
    threads = []
    actor = user()
    actor.id = uuid.uuid4()
    actor.role = "teacher"
    path_id = uuid.uuid4()

    class Session:
        def lookup(self, *_):
            threads.append(threading.get_ident())
            entered.set()
            gate.wait(3)
            return SimpleNamespace(created_by=actor.id)

        get = lookup

        def close(self):
            threads.append(threading.get_ident())

    db = Session()

    async def authenticate(*_, **__):
        return actor, db

    async def register(**kwargs):
        await kwargs["websocket"].send_json({"ready": True})
        await kwargs["websocket"].close()

    module = classroom if kind == "classroom" else course_progress
    monkeypatch.setattr(module, "get_ws_current_user", authenticate)
    if kind == "classroom":
        monkeypatch.setattr(
            classroom.classroom_service,
            "get_class_ids_of_user",
            lambda *_: [db.lookup()],
        )
        monkeypatch.setattr(classroom.classroom_presence_hub, "register", register)
    else:
        monkeypatch.setattr(course_progress.course_progress_hub, "register", register)

    app = FastAPI()

    @app.websocket("/ws")
    async def endpoint(websocket: WebSocket):
        if kind == "classroom":
            await classroom.classroom_presence_proxy(websocket, "token")
        else:
            await course_progress.course_progress_proxy(
                websocket, str(path_id), "token"
            )

    @app.get("/health")
    async def health():
        return True

    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/ws",
        "scheme": "ws",
        "query_string": b"",
        "headers": [],
        "server": ("test", 80),
        "client": ("test", 1234),
        "root_path": "",
        "subprotocols": [],
    }
    task = asyncio.create_task(app(scope, receive, send))
    try:
        await settle(entered.is_set)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await asyncio.wait_for(client.get("/health"), 1)).status_code == 200
        assert not task.done() and not gate.is_set()
    finally:
        gate.set()
    await task
    assert any(message["type"] == "websocket.accept" for message in sent)
    assert threads and threading.get_ident() not in threads
