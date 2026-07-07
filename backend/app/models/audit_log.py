"""審計日誌模型"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .user import User


class AuditAction(str, enum.Enum):
    """審計操作類型"""

    # 規格調整
    spec_change_request = "spec_change_request"
    spec_change_apply = "spec_change_apply"

    # 快照管理
    snapshot_create = "snapshot_create"
    snapshot_delete = "snapshot_delete"
    snapshot_rollback = "snapshot_rollback"

    # 配置更新
    config_update = "config_update"

    # 資源創建
    vm_create = "vm_create"
    lxc_create = "lxc_create"

    # 資源控制
    resource_start = "resource_start"
    resource_stop = "resource_stop"
    resource_reboot = "resource_reboot"
    resource_shutdown = "resource_shutdown"
    resource_reset = "resource_reset"
    resource_delete = "resource_delete"
    resource_extend_session = "resource_extend_session"

    # VM 申請
    vm_request_submit = "vm_request_submit"
    vm_request_submit_auto_approved = "vm_request_submit_auto_approved"
    vm_request_review = "vm_request_review"
    ai_api_request_submit = "ai_api_request_submit"
    ai_api_request_review = "ai_api_request_review"

    # 用戶管理
    user_create = "user_create"
    user_update = "user_update"
    user_delete = "user_delete"

    # 群組管理
    group_create = "group_create"
    group_delete = "group_delete"
    group_member_add = "group_member_add"
    group_member_remove = "group_member_remove"
    batch_provision_vm = "batch_provision_vm"
    batch_provision_lxc = "batch_provision_lxc"

    # 腳本部署
    script_deploy = "script_deploy"

    # 反挖礦（模組D）
    mining_detected = "mining_detected"
    mining_suspend = "mining_suspend"
    mining_ban = "mining_ban"
    mining_dismiss = "mining_dismiss"
    mining_exempt_change = "mining_exempt_change"

    # 認證 / Login
    login_success = "login_success"
    login_failed = "login_failed"
    login_google_success = "login_google_success"
    login_google_failed = "login_google_failed"
    login_ldap_success = "login_ldap_success"
    login_ldap_failed = "login_ldap_failed"
    password_change = "password_change"
    password_recovery_request = "password_recovery_request"
    password_reset = "password_reset"

    # 防火牆
    firewall_layout_update = "firewall_layout_update"
    firewall_connection_create = "firewall_connection_create"
    firewall_connection_delete = "firewall_connection_delete"
    firewall_rule_create = "firewall_rule_create"
    firewall_rule_update = "firewall_rule_update"
    firewall_rule_delete = "firewall_rule_delete"
    nat_rule_delete = "nat_rule_delete"
    nat_rule_sync = "nat_rule_sync"
    reverse_proxy_rule_delete = "reverse_proxy_rule_delete"
    reverse_proxy_rule_sync = "reverse_proxy_rule_sync"

    # Gateway
    gateway_config_update = "gateway_config_update"
    gateway_keypair_generate = "gateway_keypair_generate"
    gateway_config_write = "gateway_config_write"
    gateway_service_control = "gateway_service_control"

    # Cloudflare
    cloudflare_config_update = "cloudflare_config_update"
    cloudflare_zone_create = "cloudflare_zone_create"
    cloudflare_dns_record_create = "cloudflare_dns_record_create"
    cloudflare_dns_record_update = "cloudflare_dns_record_update"
    cloudflare_dns_record_delete = "cloudflare_dns_record_delete"

    # Proxmox 設定
    proxmox_config_update = "proxmox_config_update"
    proxmox_node_update = "proxmox_node_update"
    proxmox_storage_update = "proxmox_storage_update"
    proxmox_sync_nodes = "proxmox_sync_nodes"
    proxmox_sync_now = "proxmox_sync_now"

    # Migration jobs
    migration_job_retry = "migration_job_retry"
    migration_job_cancel = "migration_job_cancel"

    # 規格直改
    spec_direct_update = "spec_direct_update"

    # AI API 憑證
    ai_api_credential_rotate = "ai_api_credential_rotate"
    ai_api_credential_delete = "ai_api_credential_delete"
    ai_api_credential_update = "ai_api_credential_update"


class AuditLog(SQLModel, table=True):
    """審計日誌表"""

    __tablename__ = "audit_logs"
    __table_args__ = (
        sa.Index("ix_audit_logs_created_at", "created_at"),
        sa.Index("ix_audit_logs_user_created", "user_id", "created_at"),
        sa.Index("ix_audit_logs_action_created", "action", "created_at"),
        sa.Index("ix_audit_logs_vmid_created", "vmid", "created_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        description="操作者ID",
    )
    vmid: int | None = Field(default=None, description="操作的VM/CT ID")
    resource_vmid: int | None = Field(
        default=None,
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        description="Linked resource VMID; vmid remains as audit snapshot",
    )
    action: AuditAction = Field(
        sa_column=Column(Enum(AuditAction), nullable=False), description="操作類型"
    )
    details: str = Field(description="操作詳情")
    ip_address: str | None = Field(default=None, description="操作來源IP")
    user_agent: str | None = Field(default=None, description="User Agent")
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        description="操作時間",
    )

    # Relationship
    user: Optional["User"] = Relationship(back_populates="audit_logs")


__all__ = [
    "AuditAction",
    "AuditLog",
]
