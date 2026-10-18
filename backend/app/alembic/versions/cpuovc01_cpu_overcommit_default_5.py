"""raise the default cpu_overcommit_ratio to 5.0

只改資料庫層的預設值；已經存在的設定列維持管理員現有的值。

Revision ID: cpuovc01
Revises: memovc01
Create Date: 2026-10-18 13:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "cpuovc01"
down_revision = "memovc01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "proxmox_config",
        "cpu_overcommit_ratio",
        existing_type=sa.Float(),
        server_default=sa.text("5.0"),
    )


def downgrade() -> None:
    op.alter_column(
        "proxmox_config",
        "cpu_overcommit_ratio",
        existing_type=sa.Float(),
        server_default=sa.text("2.0"),
    )
