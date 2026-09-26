"""vmid / resource_vmid 雙欄收斂（H4、M1）。

ec01/ed01 為 8 張表補了 resource_vmid 外鍵，舊 vmid 欄位留著雙寫。依各表
語意分兩類處理：

- 列隨資源存亡、vmid 本來就指向現存資源 → 只留 vmid 當外鍵，刪 resource_vmid：
  - nat_rule / reverse_proxy_rule：fdb01 已替 vmid 加了 CASCADE 外鍵，
    resource_vmid 是第二條重複外鍵，且從來沒人讀。
  - firewall_layout：vmid 改為 CASCADE 外鍵（NULL 代表 gateway 節點）。
    先清掉指向已不存在資源的 VM 節點、每位使用者重複的 gateway 列，
    再加 partial unique 與 gateway/vmid 一致性檢查。
  - batch_provision_tasks：vmid 改為 SET NULL 外鍵（資源刪除時本來就由
    clear_task_vmid_references 清成 NULL，並非快照）。
- vmid 是申請／事件當時的快照、需在資源刪除後保留 → 維持雙欄不動
  （audit_logs、spec_change_requests、deletion_requests、ip_allocation）。
  resource_vmid 標記「當時那台機器」，PVE 回收 VMID 後不會誤指新機器。
  只補 spec_change_requests.vmid 的查詢索引。

Revision ID: dbm01_single_resource_vmid
Revises: dbm02_resource_connection_id
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "dbm01_single_resource_vmid"
down_revision = "dbm02_resource_connection_id"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in _inspector().get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in _inspector().get_indexes(table) if i.get("name")}


def _drop_fks_on(table: str, column: str) -> None:
    for fk in _inspector().get_foreign_keys(table):
        if list(fk.get("constrained_columns") or []) == [column] and fk.get("name"):
            op.drop_constraint(fk["name"], table, type_="foreignkey")


def _drop_column(table: str, column: str) -> None:
    """連同該欄的外鍵與單欄索引一起刪。"""
    if column not in _columns(table):
        return
    _drop_fks_on(table, column)
    for index in _inspector().get_indexes(table):
        if index.get("column_names") == [column] and index.get("name"):
            op.drop_index(index["name"], table_name=table)
    op.drop_column(table, column)


def _ensure_vmid_fk(table: str, ondelete: str) -> None:
    _drop_fks_on(table, "vmid")
    op.create_foreign_key(
        f"fk_{table}_vmid_resources",
        table,
        "resources",
        ["vmid"],
        ["vmid"],
        ondelete=ondelete,
    )
    if f"ix_{table}_vmid" not in _indexes(table):
        op.create_index(f"ix_{table}_vmid", table, ["vmid"])


def upgrade() -> None:
    # nat_rule / reverse_proxy_rule：vmid 已有 CASCADE 外鍵，刪重複的那條
    _drop_column("nat_rule", "resource_vmid")
    _drop_column("reverse_proxy_rule", "resource_vmid")

    # firewall_layout
    op.execute(
        """
        DELETE FROM firewall_layout
        WHERE node_type <> 'gateway'
          AND (vmid IS NULL OR vmid NOT IN (SELECT vmid FROM resources))
        """
    )
    op.execute(
        "UPDATE firewall_layout SET vmid = NULL "
        "WHERE node_type = 'gateway' AND vmid IS NOT NULL"
    )
    op.execute(
        """
        DELETE FROM firewall_layout f
        USING firewall_layout newer
        WHERE f.node_type = 'gateway' AND newer.node_type = 'gateway'
          AND f.user_id = newer.user_id
          AND (f.updated_at, f.id::text) < (newer.updated_at, newer.id::text)
        """
    )
    _ensure_vmid_fk("firewall_layout", "CASCADE")
    _drop_column("firewall_layout", "resource_vmid")
    if "uq_firewall_layout_user_gateway" not in _indexes("firewall_layout"):
        op.create_index(
            "uq_firewall_layout_user_gateway",
            "firewall_layout",
            ["user_id"],
            unique=True,
            postgresql_where=sa.text("vmid IS NULL"),
        )
    op.create_check_constraint(
        "ck_firewall_layout_gateway_has_no_vmid",
        "firewall_layout",
        "(node_type = 'gateway') = (vmid IS NULL)",
    )

    # batch_provision_tasks
    op.execute(
        """
        UPDATE batch_provision_tasks SET vmid = NULL
        WHERE vmid IS NOT NULL AND vmid NOT IN (SELECT vmid FROM resources)
        """
    )
    _ensure_vmid_fk("batch_provision_tasks", "SET NULL")
    _drop_column("batch_provision_tasks", "resource_vmid")

    # spec_change_requests.vmid：申請人開單／取消時以 vmid 查詢
    if "ix_spec_change_requests_vmid" not in _indexes("spec_change_requests"):
        op.create_index(
            "ix_spec_change_requests_vmid", "spec_change_requests", ["vmid"]
        )


def _restore_resource_vmid(table: str, ondelete: str) -> None:
    if "resource_vmid" in _columns(table):
        return
    op.add_column(table, sa.Column("resource_vmid", sa.Integer(), nullable=True))
    op.execute(
        f"UPDATE {table} SET resource_vmid = vmid "
        "WHERE vmid IN (SELECT vmid FROM resources)"
    )
    op.create_foreign_key(
        f"fk_{table}_resource_vmid",
        table,
        "resources",
        ["resource_vmid"],
        ["vmid"],
        ondelete=ondelete,
    )
    op.create_index(f"ix_{table}_resource_vmid", table, ["resource_vmid"])


def downgrade() -> None:
    if "ix_spec_change_requests_vmid" in _indexes("spec_change_requests"):
        op.drop_index("ix_spec_change_requests_vmid", table_name="spec_change_requests")

    _restore_resource_vmid("batch_provision_tasks", "SET NULL")
    _drop_fks_on("batch_provision_tasks", "vmid")
    if "ix_batch_provision_tasks_vmid" in _indexes("batch_provision_tasks"):
        op.drop_index(
            "ix_batch_provision_tasks_vmid", table_name="batch_provision_tasks"
        )

    op.drop_constraint(
        "ck_firewall_layout_gateway_has_no_vmid", "firewall_layout", type_="check"
    )
    if "uq_firewall_layout_user_gateway" in _indexes("firewall_layout"):
        op.drop_index("uq_firewall_layout_user_gateway", table_name="firewall_layout")
    _restore_resource_vmid("firewall_layout", "CASCADE")
    _drop_fks_on("firewall_layout", "vmid")
    if "ix_firewall_layout_vmid" in _indexes("firewall_layout"):
        op.drop_index("ix_firewall_layout_vmid", table_name="firewall_layout")

    _restore_resource_vmid("reverse_proxy_rule", "CASCADE")
    _restore_resource_vmid("nat_rule", "CASCADE")
