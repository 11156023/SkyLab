"""Prometheus HTTP service discovery：告訴 Prometheus 去哪裡抓 Gateway 的 exporter。

Gateway 的位址存在 DB（閘道頁的連線設定），寫死在 prometheus.yml 的話每個部署
都要手動改、Gateway 換 IP 也要記得跟著改。改成 Prometheus 用 ``http_sd_configs``
問後端，回傳格式照 Prometheus 規格：

    [{"targets": ["<host>:<port>"], "labels": {...}}]

Gateway 還沒設定時回空陣列，Prometheus 就不抓（不會一直報 down）。
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine

EXPORTER_PORTS: dict[str, int] = {
    "node": settings.GATEWAY_NODE_EXPORTER_PORT,
    "nginx": settings.GATEWAY_NGINX_EXPORTER_PORT,
}


def _format_address(host: str, port: int) -> str:
    clean = host.strip()
    # IPv6 位址要加中括號，否則 host:port 會切錯
    if ":" in clean and not clean.startswith("["):
        clean = f"[{clean}]"
    return f"{clean}:{port}"


def build_gateway_targets(host: str | None, exporter: str) -> list[dict[str, Any]]:
    """純函式：Gateway host + exporter 種類 → Prometheus target group 清單。"""
    port = EXPORTER_PORTS.get(exporter)
    if port is None or not host or not host.strip():
        return []
    return [
        {
            "targets": [_format_address(host, port)],
            "labels": {"skylab_role": "gateway", "exporter": exporter},
        }
    ]


def gateway_targets(exporter: str) -> list[dict[str, Any]]:
    from app.repositories import gateway_config as gw_repo

    with Session(engine) as session:
        config = gw_repo.get_gateway_config(session)
        host = config.host if config is not None else None
    return build_gateway_targets(host, exporter)


__all__ = ["EXPORTER_PORTS", "build_gateway_targets", "gateway_targets"]
