"""machine backup: per-connection backup storage + student backup limit

快照不能用的機器（磁碟在不支援快照的 storage）改以 vzdump 備份當還原點：
- ``proxmox_connections.backup_storage``：這個叢集的備份要放哪個 storage；
  NULL＝不開放備份功能（既有部署升級後預設就是 NULL，行為不變）。
- ``governance_config.student_backup_max_count``：非管理員每台機器可保留的備份數。

Revision ID: bkup01_machine_backup
Revises: pentry01_platform_entry
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "bkup01_machine_backup"
down_revision = "pentry01_platform_entry"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("proxmox_connections", "backup_storage"):
        op.add_column(
            "proxmox_connections",
            sa.Column("backup_storage", sa.String(length=255), nullable=True),
        )
    if not _has_column("governance_config", "student_backup_max_count"):
        # governance_config 為既有 singleton 表，NOT NULL 新欄位需 server_default
        op.add_column(
            "governance_config",
            sa.Column(
                "student_backup_max_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("2"),
            ),
        )


def downgrade() -> None:
    if _has_column("governance_config", "student_backup_max_count"):
        op.drop_column("governance_config", "student_backup_max_count")
    if _has_column("proxmox_connections", "backup_storage"):
        op.drop_column("proxmox_connections", "backup_storage")
