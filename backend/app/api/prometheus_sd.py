"""``/metrics/gateway-targets``：Prometheus ``http_sd_configs`` 用的 Gateway exporter 清單。

跟 ``/metrics`` 一樣掛在 app 層、不在 ``/api`` 底下：nginx 只把 ``/api``、``/ws``
轉給 backend，所以這個端點不會從外網打得到（回應裡有 Gateway 的內網位址），
驗證沿用 METRICS_TOKEN。
"""

from __future__ import annotations

import asyncio
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from app.core.metrics import is_authorized
from app.services.monitoring import prometheus_sd_service

logger = logging.getLogger(__name__)


async def gateway_targets_endpoint(request: Request) -> Response:
    if not is_authorized(request):
        return PlainTextResponse(
            "unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"}
        )
    exporter = request.query_params.get("exporter", "")
    if exporter not in prometheus_sd_service.EXPORTER_PORTS:
        return PlainTextResponse(
            f"exporter must be one of: {', '.join(sorted(prometheus_sd_service.EXPORTER_PORTS))}",
            status_code=400,
        )
    try:
        groups = await asyncio.to_thread(prometheus_sd_service.gateway_targets, exporter)
    except Exception:
        # Prometheus 拿到非 200 會沿用上一次的目標清單，比回空陣列（整個消失）好
        logger.exception("Building gateway Prometheus targets failed")
        return PlainTextResponse("failed to load gateway config", status_code=503)
    return JSONResponse(groups)
