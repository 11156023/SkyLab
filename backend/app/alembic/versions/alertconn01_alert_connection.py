"""alert_events 新增 connection_id，告警以 (連線, 目標, 指標) 為鍵。

多連線（多叢集）下 vmid 只有搭配連線才唯一；原本只以 (target, metric)
開啟／解除告警，不同叢集的同號 VM 會互相解除或擋掉彼此的告警。

既有資料：只回填仍開啟中的告警 —— 節點告警依 proxmox_nodes 的節點名稱、
VM 告警依 resources.vmid 查出連線。對不到的維持 NULL，評估時仍會與
同目標的樣本比對，所以照樣會解除。已解除的歷史告警保持 NULL。

Revision ID: alertconn01_alert_connection
Revises: pwhash01_login_password_hash
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "alertconn01_alert_connection"
down_revision = "pwhash01_login_password_hash"
branch_labels = None
depends_on = None


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    return "connection_id" in {
        c["name"] for c in inspector.get_columns("alert_events")
    }


def upgrade() -> None:
    if not _has_column():
        op.add_column(
            "alert_events", sa.Column("connection_id", sa.Integer(), nullable=True)
        )
        op.create_foreign_key(
            "fk_alert_events_connection_id",
            "alert_events",
            "proxmox_connections",
            ["connection_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            "ix_alert_events_connection_id", "alert_events", ["connection_id"]
        )

    op.execute(
        """
        UPDATE alert_events AS a
        SET connection_id = n.connection_id
        FROM proxmox_nodes AS n
        WHERE a.resolved_at IS NULL
          AND a.connection_id IS NULL
          AND a.scope = 'node'
          AND a.target = n.name
          AND n.connection_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE alert_events AS a
        SET connection_id = r.connection_id
        FROM resources AS r
        WHERE a.resolved_at IS NULL
          AND a.connection_id IS NULL
          AND a.scope = 'vm'
          AND a.target = CAST(r.vmid AS VARCHAR)
          AND r.connection_id IS NOT NULL
        """
    )


def downgrade() -> None:
    if not _has_column():
        return
    inspector = sa.inspect(op.get_bind())
    for fk in inspector.get_foreign_keys("alert_events"):
        if fk.get("constrained_columns") == ["connection_id"] and fk.get("name"):
            op.drop_constraint(fk["name"], "alert_events", type_="foreignkey")
    for index in inspector.get_indexes("alert_events"):
        if index.get("column_names") == ["connection_id"] and index.get("name"):
            op.drop_index(index["name"], table_name="alert_events")
    op.drop_column("alert_events", "connection_id")
