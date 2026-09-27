"""audit_logs.action 由 PostgreSQL enum 改為 VARCHAR（M4）。

auditaction enum 已造成三次正式環境故障：Python 新增 action 卻漏了
ALTER TYPE ... ADD VALUE（寫入 500），以及 Python 拿掉仍有紀錄的值
（讀取 LookupError）。PG enum 標籤又刪不掉，累積了十幾個已下線的值。

改為 VARCHAR(64)：寫入仍由 Python 的 AuditAction 驗證，新增 action 不再
需要 migration，讀取也不會因歷史值失敗。不加 CHECK，否則又回到「每加一個
action 都要 migration」。

注意：ALTER COLUMN TYPE 會重寫 audit_logs 並重建 ix_audit_logs_action_created
（ACCESS EXCLUSIVE 鎖），耗時與表大小成正比，請在維護時段執行。

Revision ID: dbm04b_audit_action_varchar
Revises: dbm04_status_checks
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "dbm04b_audit_action_varchar"
down_revision = "dbm04_status_checks"
branch_labels = None
depends_on = None

# downgrade 重建 enum 用的標籤（本 migration 撰寫時 AuditAction 的全部值）
_LABELS: tuple[str, ...] = (
    "spec_change_request",
    "spec_change_apply",
    "snapshot_create",
    "snapshot_delete",
    "snapshot_rollback",
    "config_update",
    "vm_create",
    "lxc_create",
    "resource_start",
    "resource_stop",
    "resource_reboot",
    "resource_shutdown",
    "resource_reset",
    "resource_delete",
    "resource_extend_session",
    "vm_request_submit",
    "vm_request_submit_auto_approved",
    "vm_request_review",
    "vm_request_expired",
    "ai_api_request_submit",
    "ai_api_request_review",
    "user_create",
    "user_update",
    "user_delete",
    "batch_provision_vm",
    "batch_provision_lxc",
    "script_deploy",
    "mining_detected",
    "mining_suspend",
    "mining_ban",
    "mining_dismiss",
    "mining_exempt_change",
    "login_success",
    "login_failed",
    "login_google_success",
    "login_google_failed",
    "login_ldap_success",
    "login_ldap_failed",
    "password_change",
    "password_recovery_request",
    "password_reset",
    "login_totp_failed",
    "totp_enable",
    "totp_disable",
    "totp_admin_reset",
    "auth_policy_update",
    "firewall_layout_update",
    "firewall_connection_create",
    "firewall_connection_delete",
    "firewall_rule_create",
    "firewall_rule_update",
    "firewall_rule_delete",
    "nat_rule_delete",
    "nat_rule_sync",
    "reverse_proxy_rule_delete",
    "reverse_proxy_rule_sync",
    "gateway_config_update",
    "gateway_keypair_generate",
    "gateway_config_write",
    "gateway_service_control",
    "cloudflare_config_update",
    "cloudflare_zone_create",
    "cloudflare_dns_record_create",
    "cloudflare_dns_record_update",
    "cloudflare_dns_record_delete",
    "proxmox_config_update",
    "proxmox_node_update",
    "proxmox_storage_update",
    "proxmox_sync_nodes",
    "proxmox_sync_now",
    "migration_job_retry",
    "migration_job_cancel",
    "group_create",
    "group_delete",
    "group_member_add",
    "group_member_remove",
    "cloudflare_zone_activation_check",
    "spec_direct_update",
    "credential_update",
    "resource_share_update",
    "resource_transfer",
    "ai_api_credential_rotate",
    "ai_api_credential_delete",
    "ai_api_credential_update",
    "ai_ssh_exec",
    "ai_ssh_exec_blocked",
    "course_lab_deploy",
    "course_answer_submit",
    "quick_practice_machine_create",
)


def _action_is_enum() -> bool:
    for column in sa.inspect(op.get_bind()).get_columns("audit_logs"):
        if column["name"] == "action":
            return isinstance(column["type"], sa.Enum)
    return False


def upgrade() -> None:
    if _action_is_enum():
        op.execute(
            "ALTER TABLE audit_logs ALTER COLUMN action TYPE varchar(64) "
            "USING action::text"
        )
    op.execute("DROP TYPE IF EXISTS auditaction")


def downgrade() -> None:
    unknown = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT DISTINCT action FROM audit_logs WHERE NOT (action = ANY(:labels))"
            ),
            {"labels": list(_LABELS)},
        )
        .fetchall()
    )
    if unknown:
        values = ", ".join(sorted(str(row[0]) for row in unknown))
        raise RuntimeError(
            f"audit_logs 有 enum 以外的 action（{values}），無法還原成 enum"
        )
    labels = ", ".join(f"'{label}'" for label in _LABELS)
    op.execute(f"CREATE TYPE auditaction AS ENUM ({labels})")
    op.execute(
        "ALTER TABLE audit_logs ALTER COLUMN action TYPE auditaction "
        "USING action::auditaction"
    )
