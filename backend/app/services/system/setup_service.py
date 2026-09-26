"""首次安裝初始化精靈的業務邏輯。

精靈在 `system_setup.completed` 為 False 時對外開放（免登入），
每個寫入動作都先經過 `ensure_setup_open`；完成後所有精靈端點一律 403，
後續調整改走各系統管理頁面。
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from sqlmodel import Session

from app.core.config import settings
from app.core.i18n import t
from app.exceptions import BadRequestError, ConflictError, PermissionDeniedError
from app.infrastructure.proxmox import (
    fetch_cluster_nodes,
    invalidate_proxmox_client,
    resolve_verify,
)
from app.models import (
    AuditAction,
    ProxmoxNode,
    SubnetConfig,
    SystemSetup,
    User,
    UserRole,
)
from app.repositories import proxmox_connection as proxmox_connection_repo
from app.repositories import system_setup as system_setup_repo
from app.repositories import user as user_repo
from app.schemas import UserCreate, UserUpdate
from app.schemas.ip_management import SubnetConfigCreate
from app.schemas.proxmox_config import ProxmoxNodePublic
from app.schemas.setup import (
    SetupAdminCreate,
    SetupAdminResult,
    SetupProxmoxCreate,
    SetupProxmoxResult,
    SetupProxmoxTestRequest,
    SetupProxmoxTestResult,
    SetupStatusPublic,
    SetupStepsPublic,
    SetupStoragePublic,
    SetupSubnetResult,
)
from app.services.network import ip_management_service
from app.services.proxmox import connection_sync_service
from app.services.user import audit_service

logger = logging.getLogger(__name__)


# ── 狀態 ────────────────────────────────────────────────────────────────────


def _steps(session: Session, state: SystemSetup) -> SetupStepsPublic:
    return SetupStepsPublic(
        admin=state.admin_user_id is not None,
        proxmox=bool(proxmox_connection_repo.get_all_connections(session)),
        subnet=ip_management_service.get_subnet_config(session) is not None,
    )


def get_status(*, session: Session) -> SetupStatusPublic:
    state = system_setup_repo.get_system_setup(session=session)
    return SetupStatusPublic(
        completed=state.completed,
        completed_at=state.completed_at,
        steps=_steps(session, state),
    )


def ensure_setup_open(*, session: Session) -> SystemSetup:
    """精靈只在尚未完成時可用；完成後一律 403。"""
    state = system_setup_repo.get_system_setup(session=session)
    if state.completed:
        raise PermissionDeniedError(t("setup.alreadyCompleted"))
    return state


# ── 步驟一：管理員 ────────────────────────────────────────────────────────────


def configure_admin(*, session: Session, data: SetupAdminCreate) -> SetupAdminResult:
    """建立系統管理員；信箱已是超級使用者（例如 .env 預設帳號）時改為接管。

    信箱屬於一般帳號時拒絕：精靈免登入，不能拿來把既有學生／老師帳號升成管理員。
    """
    ensure_setup_open(session=session)

    existing = user_repo.get_user_by_email(session=session, email=data.email)
    if existing is not None and not existing.is_superuser:
        raise ConflictError(t("setup.emailInUse"))

    if existing is None:
        user = user_repo.create_user(
            session=session,
            user_create=UserCreate(
                email=data.email,
                password=data.password,
                full_name=data.full_name,
                role=UserRole.admin,
                is_superuser=True,
                is_active=True,
            ),
        )
        created = True
    else:
        # 接管既有超級使用者：沒填姓名就保留原本的，不要清空
        user_in = UserUpdate(
            password=data.password,
            role=UserRole.admin,
            is_superuser=True,
            is_active=True,
        )
        if data.full_name is not None:
            user_in.full_name = data.full_name
        user = user_repo.update_user(
            session=session, db_user=existing, user_in=user_in
        )
        created = False
    # 精靈裡已經選過語言與主題，登入後不再跑一次首次登入引導
    user.onboarding_completed = True
    session.add(user)
    session.commit()
    session.refresh(user)

    default_admin_disabled = False
    if data.disable_default_admin and user.email != settings.FIRST_SUPERUSER:
        default_admin_disabled = _disable_default_admin(session)

    system_setup_repo.set_admin_user(session=session, user_id=user.id)
    audit_service.log_action(
        session=session,
        user_id=user.id,
        action=AuditAction.user_create if created else AuditAction.config_update,
        details=(
            f"Setup wizard: {'created' if created else 'took over'} "
            f"superuser {user.email}"
            + (" and disabled the default admin" if default_admin_disabled else "")
        ),
    )
    return SetupAdminResult(
        email=user.email,
        full_name=user.full_name,
        created=created,
        default_admin_disabled=default_admin_disabled,
    )


def _disable_default_admin(session: Session) -> bool:
    """停用 .env 的 FIRST_SUPERUSER（密碼寫在 .env，安裝後不該再能登入）。"""
    default = user_repo.get_user_by_email(
        session=session, email=settings.FIRST_SUPERUSER
    )
    if default is None or not default.is_active:
        return False
    user_repo.update_user(
        session=session, db_user=default, user_in=UserUpdate(is_active=False)
    )
    session.commit()
    return True


# ── 步驟二：PVE 連線 ──────────────────────────────────────────────────────────


def _validate_ca_cert(ca_cert: str | None) -> None:
    if not ca_cert:
        return
    try:
        x509.load_pem_x509_certificate(ca_cert.encode(), default_backend())
    except Exception:
        raise BadRequestError(t("proxmoxConfig.invalidCaCert"))


def _collect_storages(client: Any, node_names: list[str]) -> list[SetupStoragePublic]:
    """逐節點抓 storage，同名共享 storage 合併成一筆（列出所有節點）。"""
    merged: OrderedDict[str, SetupStoragePublic] = OrderedDict()
    for node_name in node_names:
        try:
            raw_storages = client.nodes(node_name).storage.get()
        except Exception as e:
            logger.warning(f"Setup: failed to fetch storage for node {node_name}: {e}")
            continue
        for st in raw_storages:
            if not connection_sync_service.is_usable_storage(st):
                continue
            row = connection_sync_service.storage_row_from_pve(node_name, st)
            key = row["storage"] if row["is_shared"] else f"{node_name}/{row['storage']}"
            if key in merged:
                merged[key].nodes.append(node_name)
                continue
            merged[key] = SetupStoragePublic(
                storage=row["storage"],
                storage_type=row["storage_type"],
                nodes=[node_name],
                can_iso=row["can_iso"],
                can_vm=row["can_vm"],
                can_lxc=row["can_lxc"],
                is_shared=row["is_shared"],
                avail_gb=row["avail_gb"],
            )
    return list(merged.values())


def test_proxmox(*, data: SetupProxmoxTestRequest) -> SetupProxmoxTestResult:
    """用表單內容臨時連線：回節點與 storage 清單，讓下一步能用選的而不是用打的。"""
    _validate_ca_cert(data.ca_cert)
    try:
        from proxmoxer import ProxmoxAPI  # type: ignore[import-untyped]

        verify_ssl = resolve_verify(data.host, data.verify_ssl, data.ca_cert)
        raw_nodes = fetch_cluster_nodes(
            host=data.host,
            user=data.user,
            password=data.password,
            verify_ssl=verify_ssl,
            timeout=data.api_timeout,
        )
        nodes = [
            ProxmoxNodePublic(
                name=n["name"],
                host=n["host"],
                port=n.get("port", 8006),
                is_primary=n.get("is_primary", False),
                is_online=True,
            )
            for n in raw_nodes
        ]
        client = ProxmoxAPI(
            data.host,
            port=data.port,
            user=data.user,
            password=data.password,
            verify_ssl=verify_ssl,
            timeout=data.api_timeout,
        )
        storages = _collect_storages(client, [n.name for n in nodes])
    except Exception as e:
        logger.warning(f"Setup: Proxmox connection test failed for {data.host}: {e}")
        return SetupProxmoxTestResult(
            success=False, error=t("setup.proxmoxTestFailed")
        )
    return SetupProxmoxTestResult(
        success=True,
        is_cluster=len(nodes) > 1,
        nodes=nodes,
        storages=storages,
    )


def configure_proxmox(
    *, session: Session, data: SetupProxmoxCreate
) -> SetupProxmoxResult:
    """建立第一組 PVE 連線並立刻同步節點／Storage。

    同步失敗不擋精靈：連線已存好，錯誤帶回給前端顯示，管理員登入後可再同步。
    """
    state = ensure_setup_open(session=session)
    _validate_ca_cert(data.ca_cert)

    is_default = data.is_default or not proxmox_connection_repo.get_all_connections(
        session
    )
    conn = proxmox_connection_repo.create_connection(
        session,
        name=data.name,
        host=data.host,
        port=data.port,
        user=data.user,
        password=data.password,
        verify_ssl=data.verify_ssl,
        ca_cert=data.ca_cert,
        api_timeout=data.api_timeout,
        pool_name=data.pool_name,
        iso_storage=data.iso_storage,
        data_storage=data.data_storage,
        task_check_interval=data.task_check_interval,
        gateway_ip=data.gateway_ip,
        local_subnet=data.local_subnet,
        default_node=data.default_node,
        enabled=data.enabled,
        is_default=is_default,
    )
    invalidate_proxmox_client()

    nodes: list[ProxmoxNode] = []
    storage_count = 0
    sync_error: str | None = None
    try:
        nodes, storage_count = connection_sync_service.sync_connection_inventory(
            session, conn
        )
    except ValueError as e:
        sync_error = str(e)
    except Exception as e:
        logger.warning(f"Setup: connection sync failed for {conn.name}: {e}")
        sync_error = t("proxmoxConfig.syncFailed")
    if nodes:
        invalidate_proxmox_client()

    audit_service.log_action(
        session=session,
        user_id=state.admin_user_id,
        action=AuditAction.proxmox_config_update,
        details=(
            f"Setup wizard: created Proxmox connection {conn.name} ({conn.host}), "
            f"synced {len(nodes)} nodes / {storage_count} storages"
            + (f"; sync error: {sync_error}" if sync_error else "")
        ),
    )
    assert conn.id is not None
    return SetupProxmoxResult(
        connection_id=conn.id,
        name=conn.name,
        host=conn.host,
        nodes=[
            ProxmoxNodePublic(
                id=n.id,
                name=n.name,
                host=n.host,
                port=n.port,
                is_primary=n.is_primary,
                is_online=n.is_online,
                last_checked=n.last_checked,
                priority=n.priority,
                enabled=getattr(n, "enabled", True),
            )
            for n in nodes
        ],
        storage_count=storage_count,
        sync_error=sync_error,
    )


# ── 步驟三：IP 網段 ───────────────────────────────────────────────────────────


def configure_subnet(*, session: Session, data: SubnetConfigCreate) -> SetupSubnetResult:
    """寫入實驗室子網設定（與 PUT /ip-management/subnet 同一套驗證）。

    全新安裝底下還沒有任何機器，封鎖網段規則沒有套用對象，這裡不呼叫
    firewall 同步；管理員之後在 IP 管理頁再存一次就會套到各機器。
    """
    state = ensure_setup_open(session=session)
    config: SubnetConfig = ip_management_service.upsert_subnet_config(
        session,
        cidr=data.cidr,
        gateway=data.gateway,
        bridge_name=data.bridge_name,
        gateway_vm_ip=data.gateway_vm_ip,
        dns_servers=data.dns_servers,
        extra_blocked_subnets=data.extra_blocked_subnets,
        forward_port_start=data.forward_port_start,
        forward_port_end=data.forward_port_end,
        forward_public_host=data.forward_public_host,
    )
    stats = ip_management_service.get_ip_stats(session)
    audit_service.log_action(
        session=session,
        user_id=state.admin_user_id,
        action=AuditAction.config_update,
        details=f"Setup wizard: configured subnet {config.cidr} on {config.bridge_name}",
    )
    return SetupSubnetResult(
        cidr=config.cidr,
        gateway=config.gateway,
        bridge_name=config.bridge_name,
        gateway_vm_ip=config.gateway_vm_ip,
        dns_servers=config.dns_servers,
        total_ips=stats["total"],
        available_ips=stats["available"],
    )


# ── 完成 ────────────────────────────────────────────────────────────────────


def complete(*, session: Session) -> SystemSetup:
    """標記初始化完成；至少要先建立管理員，PVE 與子網可以之後再補。"""
    state = ensure_setup_open(session=session)
    if state.admin_user_id is None:
        raise BadRequestError(t("setup.adminRequired"))
    admin: User | None = session.get(User, state.admin_user_id)
    if admin is None or not admin.is_active or not admin.is_superuser:
        raise BadRequestError(t("setup.adminRequired"))

    state = system_setup_repo.mark_completed(session=session)
    audit_service.log_action(
        session=session,
        user_id=state.admin_user_id,
        action=AuditAction.config_update,
        details="Setup wizard completed",
    )
    return state
