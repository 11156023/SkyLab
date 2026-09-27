"""Gateway 上 nginx 的執行期快照：給「網域管理」頁的管理員面板看。

nginx 沒有像 Traefik 那樣的 runtime API；這裡是 SSH 上去看版本、服務狀態、
``nginx -t`` 結果，並把 SkyLab 自己產生的兩份設定檔讀回來解析，再列出
Let's Encrypt 憑證與到期日。
"""

from __future__ import annotations

from app.schemas.reverse_proxy import (
    ReverseProxyCertificate,
    ReverseProxyHttpServer,
    ReverseProxyRuntimeSnapshot,
    ReverseProxyStreamServer,
)


def get_runtime_snapshot(*, session: object) -> ReverseProxyRuntimeSnapshot:
    """SSH 到 Gateway 收集 nginx 執行期資訊；Gateway 未設定時 raise BadRequestError。"""
    from app.services.network import gateway_service
    from app.services.network import nginx_gateway_service as nginx

    with gateway_service.gateway_client(session) as client:
        runtime = nginx.collect_runtime(client)

    return ReverseProxyRuntimeSnapshot(
        version=runtime["version"],
        active=runtime["active"],
        config_valid=runtime["config_valid"],
        http_servers=[ReverseProxyHttpServer(**item) for item in runtime["http_servers"]],
        stream_servers=[
            ReverseProxyStreamServer(**item) for item in runtime["stream_servers"]
        ],
        certificates=[
            ReverseProxyCertificate(**item) for item in runtime["certificates"]
        ],
    )
