"""Gateway VM 管理 API（僅管理員）"""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from app.api.deps import AdminUser, SessionDep
from app.exceptions import BadRequestError, ProxmoxError
from app.models import AuditAction
from app.repositories import gateway_config as gw_repo
from app.schemas.common import Message
from app.schemas.gateway import (
    GatewayConfigPublic,
    GatewayConfigUpdate,
    GatewayConnectionTestResult,
    GatewayServiceVersionsResult,
    ServiceActionResult,
    ServiceConfigRead,
    ServiceConfigWrite,
    ServiceStatusResult,
)
from app.services.network import gateway_service
from app.services.user import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gateway", tags=["gateway"])

_VALID_SERVICES = {"haproxy", "traefik", "frps", "frpc"}


def _require_valid_service(service: str) -> None:
    if service not in _VALID_SERVICES:
        raise HTTPException(status_code=400, detail=f"未知服務：{service}")


# ─── 連線設定 ──────────────────────────────────────────────────────────────────


@router.get("/config", response_model=GatewayConfigPublic)
def get_config(session: SessionDep, _: AdminUser):
    """取得 Gateway VM 連線設定（含公鑰）"""
    config = gw_repo.get_gateway_config(session)
    if config is None:
        return GatewayConfigPublic(
            host="",
            ssh_port=22,
            ssh_user="root",
            public_key="",
            is_configured=False,
        )
    return GatewayConfigPublic(
        host=config.host,
        ssh_port=config.ssh_port,
        ssh_user=config.ssh_user,
        public_key=config.public_key,
        is_configured=bool(config.host and config.encrypted_private_key),
    )


@router.put("/config", response_model=GatewayConfigPublic)
def update_config(
    data: GatewayConfigUpdate,
    session: SessionDep,
    current_user: AdminUser,
):
    """更新 Gateway VM 連線設定（IP / SSH port / user）"""
    config = gw_repo.upsert_connection_settings(
        session=session,
        host=data.host,
        ssh_port=data.ssh_port,
        ssh_user=data.ssh_user,
    )
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.gateway_config_update,
        details=(
            f"Updated gateway config: host={data.host} "
            f"port={data.ssh_port} user={data.ssh_user}"
        ),
    )
    return GatewayConfigPublic(
        host=config.host,
        ssh_port=config.ssh_port,
        ssh_user=config.ssh_user,
        public_key=config.public_key,
        is_configured=bool(config.host and config.encrypted_private_key),
    )


@router.post("/generate-keypair", response_model=GatewayConfigPublic)
def generate_keypair(session: SessionDep, current_user: AdminUser):
    """生成新的 ED25519 SSH Keypair 並儲存（原有私鑰將被覆蓋）"""
    private_key_pem, public_key = gateway_service.generate_ed25519_keypair()
    config = gw_repo.save_keypair(
        session=session,
        private_key_pem=private_key_pem,
        public_key=public_key,
    )
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.gateway_keypair_generate,
        details="Generated new ED25519 SSH keypair for Gateway VM",
    )
    return GatewayConfigPublic(
        host=config.host,
        ssh_port=config.ssh_port,
        ssh_user=config.ssh_user,
        public_key=config.public_key,
        is_configured=bool(config.host and config.encrypted_private_key),
    )


@router.post("/test-connection", response_model=GatewayConnectionTestResult)
def test_connection(session: SessionDep, _: AdminUser):
    """測試 SSH 連線到 Gateway VM"""
    config = gw_repo.get_gateway_config(session)
    if config is None or not config.host or not config.encrypted_private_key:
        return GatewayConnectionTestResult(
            success=False, message="尚未設定 Gateway VM IP 或 SSH 金鑰"
        )
    private_key_pem = gw_repo.get_decrypted_private_key(config)
    success, message = gateway_service.test_connection(
        host=config.host,
        ssh_port=config.ssh_port,
        ssh_user=config.ssh_user,
        private_key_pem=private_key_pem,
    )
    return GatewayConnectionTestResult(success=success, message=message)


# ─── 安裝腳本下載 ──────────────────────────────────────────────────────────────


@router.get("/install-script")
def download_install_script(_: AdminUser):
    """下載 Gateway VM 安裝腳本"""
    import os  # noqa: PLC0415

    script_path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..", "..", "gateway", "install.sh",
    )
    script_path = os.path.abspath(script_path)
    if not os.path.exists(script_path):
        raise HTTPException(status_code=404, detail="安裝腳本不存在")
    return FileResponse(
        path=script_path,
        media_type="text/x-sh",
        filename="install-gateway.sh",
    )


# ─── 服務設定檔管理 ────────────────────────────────────────────────────────────


@router.get("/services/{service}/config", response_model=ServiceConfigRead)
def read_config(service: str, session: SessionDep, _: AdminUser):
    """讀取 Gateway VM 上指定服務的設定檔"""
    _require_valid_service(service)
    try:
        content = gateway_service.read_service_config(session=session, service=service)
        return ServiceConfigRead(service=service, content=content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/services/{service}/config", response_model=Message)
def write_config(
    service: str,
    body: ServiceConfigWrite,
    session: SessionDep,
    current_user: AdminUser,
):
    """寫入設定檔到 Gateway VM"""
    _require_valid_service(service)
    try:
        gateway_service.write_service_config(
            session=session, service=service, content=body.content
        )
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            action=AuditAction.gateway_config_write,
            details=f"Wrote {service} config to Gateway VM ({len(body.content)} bytes)",
        )
        return Message(message=f"{service} 設定已儲存")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/traefik/dns-challenge/sync", response_model=Message)
def sync_traefik_dns_challenge(session: SessionDep, current_user: AdminUser):
    """套用 Cloudflare DNS Challenge 設定到 Gateway VM 的 Traefik"""
    try:
        gateway_service.sync_traefik_dns_challenge(session=session)
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            action=AuditAction.gateway_config_write,
            details="Synced Traefik dnsChallenge config from Cloudflare settings",
        )
        return Message(message="Traefik 已套用 Cloudflare DNS Challenge 設定")
    except BadRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ProxmoxError as exc:
        logger.error("Failed to sync Traefik dnsChallenge config: %s", exc)
        raise HTTPException(status_code=502, detail="Proxmox 操作失敗")
    except Exception:
        logger.exception("Unexpected error syncing Traefik dnsChallenge config")
        raise HTTPException(status_code=500, detail="同步 Traefik 憑證設定失敗")


# ─── 服務控制 ──────────────────────────────────────────────────────────────────


@router.get("/services/{service}/status", response_model=ServiceStatusResult)
def service_status(service: str, session: SessionDep, _: AdminUser):
    """取得服務運行狀態"""
    _require_valid_service(service)
    try:
        active, status_text = gateway_service.get_service_status(
            session=session, service=service
        )
        return ServiceStatusResult(
            service=service, active=active, status_text=status_text
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/services/versions", response_model=GatewayServiceVersionsResult)
def get_service_versions(session: SessionDep, _: AdminUser):
    """取得 Gateway VM 上受管理服務的版本資訊與更新建議"""
    try:
        return gateway_service.get_service_versions(session=session)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/services/{service}/logs")
def service_logs(service: str, session: SessionDep, _: AdminUser, lines: int = 50):
    """查看服務最近的 journalctl 日誌"""
    _require_valid_service(service)
    try:
        _ok, output = gateway_service.get_service_logs(
            session=session, service=service, lines=lines
        )
    except BadRequestError:
        raise
    except Exception:
        # 例外細節只記在伺服器端，避免將內部資訊洩漏給呼叫端
        logger.exception("讀取 %s 服務日誌失敗", service)
        raise HTTPException(
            status_code=502, detail=f"無法讀取 {service} 服務日誌，請稍後再試"
        )
    return PlainTextResponse(content=output)


@router.post("/services/{service}/{action}", response_model=ServiceActionResult)
def control_service(
    service: str,
    action: str,
    session: SessionDep,
    current_user: AdminUser,
):
    """控制服務（start / stop / restart / reload）"""
    _require_valid_service(service)
    valid_actions = {"start", "stop", "restart", "reload"}
    if action not in valid_actions:
        raise HTTPException(status_code=400, detail=f"無效操作：{action}")
    try:
        success, output = gateway_service.control_service(
            session=session, service=service, action=action
        )
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            action=AuditAction.gateway_service_control,
            details=f"Gateway service {action}: {service} (success={success})",
        )
        return ServiceActionResult(
            service=service, action=action, success=success, output=output
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
