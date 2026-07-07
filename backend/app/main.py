import asyncio
import sys
from contextlib import asynccontextmanager

# uvicorn 0.36+ 使用 loop_factory 參數直接建立 event loop，繞過 asyncio policy。
# 其 asyncio_loop_factory 在 Windows 單 worker 模式下固定回傳 ProactorEventLoop，
# 導致 WebSocket 空閒時觸發 WinError 121（IOCP 信號逾時）。
# 解法：在 uvicorn 載入 app 之前 patch 其 loop factory，強制回傳 SelectorEventLoop。
if sys.platform == "win32":
    import uvicorn.loops.asyncio as _uvicorn_asyncio_loop
    def _win_selector_factory(use_subprocess: bool = False) -> type[asyncio.SelectorEventLoop]:
        return asyncio.SelectorEventLoop
    _uvicorn_asyncio_loop.asyncio_loop_factory = _win_selector_factory
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import sentry_sdk
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.main import api_router
from app.api.websocket import vnc_proxy
from app.api.websocket.classroom import (
    classroom_presence_proxy,
    classroom_watch_proxy,
)
from app.api.websocket.course_progress import course_progress_proxy
from app.api.websocket.jobs import jobs_ws_proxy
from app.api.websocket.terminal import terminal_proxy
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.metrics import PrometheusMiddleware, metrics_endpoint
from app.core.request_context import RequestContextMiddleware
from app.exceptions import AppError
from app.infrastructure.ai import close_ai_clients
from app.infrastructure.queue import close_arq_pool, init_arq_pool
from app.infrastructure.redis import close_redis, init_redis
from app.infrastructure.worker import init_background_runner, shutdown_background_runner
from app.services.scheduling import vm_request_schedule_service

_SECURITY_HEADERS: list[tuple[str, str]] = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("X-XSS-Protection", "1; mode=block"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
    (
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "connect-src 'self' wss: https:; "
        "frame-ancestors 'none'",
    ),
]


class SecurityHeadersMiddleware:
    """Pure ASGI middleware — adds security headers to HTTP responses only.

    Unlike BaseHTTPMiddleware this does NOT interfere with WebSocket connections.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    # Paths that serve Swagger / ReDoc UI and need relaxed CSP
    _DOCS_PREFIXES = ("/docs", "/redoc")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only inject headers for HTTP; let WebSocket pass through untouched.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        is_docs = any(path.startswith(p) for p in self._DOCS_PREFIXES)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                for name, value in _SECURITY_HEADERS:
                    # Skip CSP and X-Frame-Options for docs pages so Swagger UI loads
                    if is_docs and name in (
                        "Content-Security-Policy",
                        "X-Frame-Options",
                    ):
                        continue
                    headers.append((name.lower().encode(), value.encode()))
                if settings.ENVIRONMENT == "production":
                    headers.append((
                        b"strict-transport-security",
                        b"max-age=31536000; includeSubDomains",
                    ))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _recover_orphan_running_deploys() -> None:
    """將上次重啟前未完成的部署任務標記為 failed，避免永久卡在「執行中」。"""
    import logging

    from sqlmodel import Session, select

    from app.core.db import engine
    from app.models.base import get_datetime_utc
    from app.models.script_deploy_log import ScriptDeployLog

    _log = logging.getLogger(__name__)
    try:
        with Session(engine) as session:
            orphans = session.exec(
                select(ScriptDeployLog).where(ScriptDeployLog.status == "running")
            ).all()
            if not orphans:
                return
            for log in orphans:
                log.status = "failed"
                log.error = (log.error or "") + "\n[系統] 伺服器重啟，部署中斷"
                log.progress = "已中斷（伺服器重啟）"
                log.updated_at = get_datetime_utc()
                log.completed_at = get_datetime_utc()
                session.add(log)
            session.commit()
            _log.warning(
                "啟動清理：將 %d 筆殘留 running 部署任務標記為 failed", len(orphans)
            )
    except Exception:
        _log.exception("啟動清理孤兒部署任務失敗（非致命）")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(
        level=settings.LOG_LEVEL,
        json_output=settings.LOG_JSON,
        log_dir=settings.LOG_DIR,
        file_enabled=settings.LOG_FILE_ENABLED,
    )
    await init_redis()
    await init_arq_pool()
    _recover_orphan_running_deploys()
    init_background_runner()
    stop_event = asyncio.Event()
    scheduler_task: asyncio.Task[None] | None = None
    if settings.SCHEDULER_ENABLED:
        scheduler_task = asyncio.create_task(
            vm_request_schedule_service.run_scheduler(stop_event)
        )
    try:
        yield
    finally:
        stop_event.set()
        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                # 排程器取消屬預期的關閉流程
                pass
        await shutdown_background_runner()
        await close_ai_clients()
        await close_arq_pool()
        await close_redis()


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=str(settings.SENTRY_DSN),
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
    )

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(PrometheusMiddleware)
app.add_middleware(RequestContextMiddleware)

if settings.all_cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.all_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
        expose_headers=["Content-Disposition"],
    )

app.include_router(api_router, prefix=settings.API_V1_STR)
app.add_route("/metrics", metrics_endpoint, methods=["GET"])


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message},
    )


@app.websocket("/ws/vnc/{vmid}")
async def websocket_vnc_proxy(
    websocket: WebSocket,
    vmid: int,
    token: str = "",
    vnc_ticket: str = "",
    vnc_port: str = "",
):
    await vnc_proxy(websocket, vmid, token=token, vnc_ticket=vnc_ticket, vnc_port=vnc_port)


@app.websocket("/ws/terminal/{vmid}")
async def websocket_terminal_proxy(websocket: WebSocket, vmid: int, token: str = ""):
    await terminal_proxy(websocket, vmid, token=token)


@app.websocket("/ws/jobs")
async def websocket_jobs_proxy(websocket: WebSocket, token: str = ""):
    await jobs_ws_proxy(websocket, token=token)


@app.websocket("/ws/classroom")
async def websocket_classroom_presence(websocket: WebSocket, token: str = ""):
    await classroom_presence_proxy(websocket, token=token)


@app.websocket("/ws/classroom/{session_id}/watch")
async def websocket_classroom_watch(
    websocket: WebSocket, session_id: str, token: str = ""
):
    await classroom_watch_proxy(websocket, session_id, token=token)


@app.websocket("/ws/courses/paths/{path_id}/progress")
async def websocket_course_progress(
    websocket: WebSocket, path_id: str, token: str = ""
):
    await course_progress_proxy(websocket, path_id, token=token)
