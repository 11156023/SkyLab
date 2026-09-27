"""補齊剩餘 user 外鍵的 ON DELETE，範本 pve_vmid 改為 partial unique。

H1/H2：usrfk01 沒涵蓋到的 user 外鍵仍是 NO ACTION。
- firewall_layout.user_id：刪帳號時 user_service 沒清這張表，gateway 節點
  那筆（vmid 為 NULL）也不會隨資源 CASCADE，存過佈局的帳號刪除會撞 FK。
- vm_requests / spec_change_requests：原本靠 user_service 手動刪除或清空，
  改由資料庫保證（申請者 CASCADE、審核者 SET NULL）。
- resources.user_id：明確宣告 RESTRICT，仍持有資源的帳號不可刪除。

H3：vm_templates.pve_vmid 原本全域 UNIQUE，搭配軟刪除只能「復用」舊列，
導致課程版本等歷史引用被新範本接手。改成只對 status <> 'deleted' 唯一，
同 VMID 重新註冊時另建新列。

Revision ID: dbh01_user_fk_template_uq
Revises: dead01_drop_dead_tables_cols
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "dbh01_user_fk_template_uq"
down_revision = "dead01_drop_dead_tables_cols"
branch_labels = None
depends_on = None


# (表, 欄位, ondelete)
_USER_FKS: tuple[tuple[str, str, str], ...] = (
    ("firewall_layout", "user_id", "CASCADE"),
    ("vm_requests", "user_id", "CASCADE"),
    ("vm_requests", "reviewer_id", "SET NULL"),
    ("spec_change_requests", "user_id", "CASCADE"),
    ("spec_change_requests", "reviewer_id", "SET NULL"),
    ("resources", "user_id", "RESTRICT"),
)


def _fk_name(table: str, column: str) -> str:
    return f"fk_{table}_{column}_user"


def _drop_user_fks(table: str, column: str) -> None:
    """丟掉 table.column → user.id 的既有外鍵（名稱以 DB 實際狀態為準）。"""
    inspector = sa.inspect(op.get_bind())
    for fk in inspector.get_foreign_keys(table):
        if fk.get("referred_table") != "user":
            continue
        if list(fk.get("constrained_columns") or []) != [column]:
            continue
        name = fk.get("name")
        if name:
            op.drop_constraint(name, table, type_="foreignkey")


def _index_names(table: str) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table)
        if index.get("name")
    }


def upgrade() -> None:
    for table, column, ondelete in _USER_FKS:
        _drop_user_fks(table, column)
        op.create_foreign_key(
            _fk_name(table, column),
            table,
            "user",
            [column],
            ["id"],
            ondelete=ondelete,
        )

    indexes = _index_names("vm_templates")
    if "ix_vm_templates_pve_vmid" in indexes:
        op.drop_index("ix_vm_templates_pve_vmid", table_name="vm_templates")
    if "uq_vm_templates_pve_vmid_active" not in indexes:
        op.create_index(
            "uq_vm_templates_pve_vmid_active",
            "vm_templates",
            ["pve_vmid"],
            unique=True,
            postgresql_where=sa.text("status <> 'deleted'"),
        )


def downgrade() -> None:
    # 全域 UNIQUE 還原前，同 VMID 只能留一列：刪掉已被新列取代的舊 deleted 列
    # 做不到（可能仍被課程版本 RESTRICT 引用），因此有重複時直接中止。
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT pve_vmid FROM vm_templates "
                "GROUP BY pve_vmid HAVING count(*) > 1"
            )
        )
        .fetchall()
    )
    if duplicates:
        vmids = ", ".join(str(row[0]) for row in duplicates)
        raise RuntimeError(
            f"vm_templates 有重複的 pve_vmid（{vmids}），無法還原全域 UNIQUE"
        )

    indexes = _index_names("vm_templates")
    if "uq_vm_templates_pve_vmid_active" in indexes:
        op.drop_index("uq_vm_templates_pve_vmid_active", table_name="vm_templates")
    if "ix_vm_templates_pve_vmid" not in indexes:
        op.create_index(
            "ix_vm_templates_pve_vmid", "vm_templates", ["pve_vmid"], unique=True
        )

    for table, column, _ondelete in _USER_FKS:
        _drop_user_fks(table, column)
        op.create_foreign_key(
            f"{table}_{column}_fkey",
            table,
            "user",
            [column],
            ["id"],
        )
