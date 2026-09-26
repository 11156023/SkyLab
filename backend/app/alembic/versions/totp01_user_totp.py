"""兩步驟驗證（TOTP）：user 表新增金鑰／啟用／防重放欄位、auth_policy 政策表、auditaction 補標籤。

- ``totp_secret_encrypted``：Fernet 加密後的 Base32 金鑰（setup 後即寫入，confirm 前為待確認）
- ``totp_enabled``：確認綁定成功後才為 True，登入時據此要求驗證碼
- ``totp_last_used_step``：最後一次成功驗證的 time step，同一 step 不可重複使用
- ``auth_policy``（singleton）：``totp_required`` 管理員強制全站啟用 2FA

PostgreSQL 無法移除 enum 值，downgrade 只回收欄位與表。

Revision ID: totp01_user_totp
Revises: setup01_system_setup
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "totp01_user_totp"
down_revision = "setup01_system_setup"
branch_labels = None
depends_on = None

_ENUM = "auditaction"

AUDIT_VALUES = (
    "login_totp_failed",
    "totp_enable",
    "totp_disable",
    "totp_admin_reset",
    "auth_policy_update",
)


def upgrade() -> None:
    op.create_table(
        "auth_policy",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "totp_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO auth_policy (id, totp_required, updated_at) "
            "VALUES (1, false, NOW())"
        )
    )

    op.add_column(
        "user",
        sa.Column("totp_secret_encrypted", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "user",
        sa.Column(
            "totp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "user",
        sa.Column("totp_last_used_step", sa.Integer(), nullable=True),
    )

    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        for value in AUDIT_VALUES:
            op.execute(f"ALTER TYPE {_ENUM} ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    op.drop_column("user", "totp_last_used_step")
    op.drop_column("user", "totp_enabled")
    op.drop_column("user", "totp_secret_encrypted")
    op.drop_table("auth_policy")
    # PostgreSQL has no DROP VALUE; leaving the enum labels in place is harmless.
