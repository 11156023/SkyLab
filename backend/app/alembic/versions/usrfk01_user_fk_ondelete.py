"""使用者相關外鍵補上 ON DELETE 行為。

這些表建立時外鍵都沒寫 ondelete（等同 NO ACTION），只要使用者留下過
AI 金鑰申請、配額覆寫、刪除請求或挖礦事件，刪帳號就會被 FK 擋下來，
必須先人工清一輪關聯資料。這裡把「屬於使用者的紀錄」改成 CASCADE，
「只是記錄誰審核／誰確認」的欄位改成 SET NULL（保留事件本體）。

``mining_incidents.user_id`` 是 NOT NULL，無法 SET NULL，因此比照其他
使用者自身資料走 CASCADE：帳號都刪了，該事件的處置對象也不存在。

Revision ID: usrfk01_user_fk_ondelete
Revises: vmreq02_provisioning_started_at
Create Date: 2026-09-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "usrfk01_user_fk_ondelete"
down_revision = "vmreq02_provisioning_started_at"
branch_labels = None
depends_on = None


# (表, 欄位) → 刪除使用者時連帶刪除該筆紀錄
_CASCADE: tuple[tuple[str, str], ...] = (
    ("ai_api_credentials", "user_id"),
    ("ai_api_usage", "user_id"),
    ("ai_template_call_logs", "user_id"),
    ("resource_quotas", "user_id"),
    ("deletion_requests", "user_id"),
    ("ai_api_requests", "user_id"),
    ("mining_incidents", "user_id"),
)

# (表, 欄位) → 刪除使用者時只清掉這個欄位，事件本身保留
_SET_NULL: tuple[tuple[str, str], ...] = (
    ("ai_api_requests", "reviewer_id"),
    ("alert_events", "acknowledged_by"),
    ("mining_incidents", "reviewed_by"),
)


def _fk_name(table: str, column: str) -> str:
    """本次改寫後統一使用的約束名稱。"""
    return f"fk_{table}_{column}_user"


def _drop_user_fks(table: str, column: str) -> None:
    """丟掉 table.column → user.id 的既有外鍵（名稱以 DB 實際狀態為準）。

    這些表建立時沒有指定約束名，PostgreSQL 會給 ``<table>_<col>_fkey``；
    但共用開發庫歷經多次 autogenerate，不保證都是那個名字，所以一律從
    inspector 讀回實際名稱再 drop。
    """
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return
    for fk in inspector.get_foreign_keys(table):
        if fk.get("referred_table") != "user":
            continue
        if list(fk.get("constrained_columns") or []) != [column]:
            continue
        name = fk.get("name")
        if name:
            op.drop_constraint(name, table, type_="foreignkey")


def _table_exists(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    for table, column in _CASCADE:
        if not _table_exists(table):
            continue
        _drop_user_fks(table, column)
        op.create_foreign_key(
            _fk_name(table, column),
            table,
            "user",
            [column],
            ["id"],
            ondelete="CASCADE",
        )

    for table, column in _SET_NULL:
        if not _table_exists(table):
            continue
        _drop_user_fks(table, column)
        op.create_foreign_key(
            _fk_name(table, column),
            table,
            "user",
            [column],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # 還原成沒有 ondelete 的預設命名外鍵
    for table, column in _CASCADE + _SET_NULL:
        if not _table_exists(table):
            continue
        _drop_user_fks(table, column)
        op.create_foreign_key(
            f"{table}_{column}_fkey",
            table,
            "user",
            [column],
            ["id"],
        )
