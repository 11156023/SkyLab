"""審計日誌模型"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, Relationship, SQLModel

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
    vm_request_expired = "vm_request_expired"
    ai_api_request_submit = "ai_api_request_submit"
    ai_api_request_review = "ai_api_request_review"

    # 用戶管理
    user_create = "user_create"
    user_update = "user_update"
    user_delete = "user_delete"

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
    # 兩步驟驗證（TOTP）
    login_totp_failed = "login_totp_failed"
    totp_enable = "totp_enable"
    totp_disable = "totp_disable"
    totp_admin_reset = "totp_admin_reset"

    # 防火牆
    firewall_layout_update = "firewall_layout_update"
    firewall_connection_create = "firewall_connection_create"
    firewall_connection_delete = "firewall_connection_delete"
    firewall_rule_create = "firewall_rule_create"
    firewall_rule_update = "firewall_rule_update"
    firewall_rule_delete = "firewall_rule_delete"
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

    # 已下線功能的 action（group_*、migration_job_*、script_deploy…）已移除：
    # audit_logs.action 是字串欄位，舊紀錄照樣能讀，只是不再出現在篩選選單。

    # 規格直改
    spec_direct_update = "spec_direct_update"

    # 資源進階設定（憑證重設 / 共享 / 轉移）
    credential_update = "credential_update"
    resource_share_update = "resource_share_update"
    resource_transfer = "resource_transfer"

    # AI API 憑證
    ai_api_credential_rotate = "ai_api_credential_rotate"
    ai_api_credential_delete = "ai_api_credential_delete"
    ai_api_credential_update = "ai_api_credential_update"

    # AI 助理的 SSH 遠端執行（模型代打的指令一定要留下誰、在哪台、跑了什麼）
    ai_ssh_exec = "ai_ssh_exec"
    ai_ssh_exec_blocked = "ai_ssh_exec_blocked"

    # 課程 / 快速練習（免審核自動開機與作答）
    course_answer_submit = "course_answer_submit"
    quick_practice_machine_create = "quick_practice_machine_create"


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
    # 以字串存放：寫入時由 AuditAction 驗證（repositories/audit_log），
    # 讀取不綁 enum，舊版遺留或已下線的 action 不會讓整批查詢 LookupError，
    # 新增 action 也不必再 ALTER TYPE
    action: str = Field(
        sa_column=Column(sa.String(64), nullable=False), description="操作類型"
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
