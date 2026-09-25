"""add system_setup (首次安裝初始化精靈狀態)

既有部署升級時不能被導去精靈：只要資料庫已經有 PVE 連線、子網設定，
或使用者不只 .env 建立的那一位，就直接把 completed 標成 True。

Revision ID: setup01_system_setup
Revises: mrg08_merge_aud03_usrfk01
Create Date: 2026-09-24 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "setup01_system_setup"
down_revision = "mrg08_merge_aud03_usrfk01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_setup",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("admin_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["admin_user_id"], ["user.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 既有環境視為已初始化；全新安裝（只有 .env 的預設管理員、沒有連線）才進精靈
    op.execute(
        sa.text(
            """
            INSERT INTO system_setup (id, completed, completed_at, admin_user_id, updated_at)
            SELECT
                1,
                (
                    EXISTS (SELECT 1 FROM proxmox_connections)
                    OR EXISTS (SELECT 1 FROM subnet_config)
                    OR (SELECT COUNT(*) FROM "user") > 1
                ),
                NULL,
                NULL,
                NOW()
            """
        )
    )


def downgrade() -> None:
    op.drop_table("system_setup")
