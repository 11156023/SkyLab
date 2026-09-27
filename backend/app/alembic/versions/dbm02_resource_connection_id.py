"""resources 新增 connection_id，記錄資源建立在哪個 PVE 連線。

resources 以 PVE VMID 為主鍵，但多連線（多叢集）下 VMID 只有搭配連線
才唯一；原本完全沒記錄，只能每次問 PVE。新資源建立時由目標節點反查
連線寫入；刪除連線時改以 DB 判斷是否還有資源（RESTRICT 兜底）。

既有資料：只有一個連線時直接回填該連線；多連線無法離線判定，保持 NULL
（刪除連線的檢查仍會問 PVE 補足）。

Revision ID: dbm02_resource_connection_id
Revises: dbm05_missing_fk_indexes
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "dbm02_resource_connection_id"
down_revision = "dbm05_missing_fk_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "connection_id" not in {c["name"] for c in inspector.get_columns("resources")}:
        op.add_column(
            "resources", sa.Column("connection_id", sa.Integer(), nullable=True)
        )
        op.create_foreign_key(
            "fk_resources_connection_id",
            "resources",
            "proxmox_connections",
            ["connection_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index("ix_resources_connection_id", "resources", ["connection_id"])

    op.execute(
        """
        UPDATE resources
        SET connection_id = (SELECT id FROM proxmox_connections)
        WHERE connection_id IS NULL
          AND (SELECT count(*) FROM proxmox_connections) = 1
        """
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "connection_id" in {c["name"] for c in inspector.get_columns("resources")}:
        # 名稱以 DB 實際狀態為準（create_all 建出的庫會是 resources_connection_id_fkey）
        for fk in inspector.get_foreign_keys("resources"):
            if fk.get("constrained_columns") == ["connection_id"] and fk.get("name"):
                op.drop_constraint(fk["name"], "resources", type_="foreignkey")
        for index in inspector.get_indexes("resources"):
            if index.get("column_names") == ["connection_id"] and index.get("name"):
                op.drop_index(index["name"], table_name="resources")
        op.drop_column("resources", "connection_id")
