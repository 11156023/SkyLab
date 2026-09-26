"""Gateway 上 nginx 的執行期快照：給「網域管理」頁的管理員面板看。

nginx 沒有像 Traefik 那樣的 runtime API；這裡是 SSH 上去看版本、服務狀態、
``nginx -t`` 結果，並把 SkyLab 自己產生的兩份設定檔讀回來解析，再列出
Let's Encrypt 憑證與到期日。
"""

from __future__ import annotations

from app.exceptions import BadRequestError
from app.schemas.reverse_proxy import (
    ReverseProxyCertificate,
    ReverseProxyHttpServer,
    ReverseProxyRuntimeSnapshot,
    ReverseProxyStreamServer,
)


def get_runtime_snapshot(*, session: object) -> ReverseProxyRuntimeSnapshot:
    from app.infrastructure.ssh import create_key_client
    from app.repositories import gateway_config as gw_repo
    from app.repositories.gateway_config import get_decrypted_private_key
    from app.services.network import nginx_gateway_service as nginx

    config = gw_repo.get_gateway_config(session)  # type: ignore[arg-type]
    if config is None or not config.host or not config.encrypted_private_key:
        raise BadRequestError("Gateway 尚未設定，無法讀取 nginx 狀態")

    private_key_pem = get_decrypted_private_key(config)
    client = create_key_client(
        config.host,
        config.ssh_port,
        config.ssh_user,
        private_key_pem,
        timeout=10,
    )
    try:
        runtime = nginx.collect_runtime(client)
    finally:
        client.close()

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
