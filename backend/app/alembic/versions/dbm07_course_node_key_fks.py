"""課程環境的 node_key 字串參照補上複合外鍵（M7）。

edges.source/target_node_key、publications.node_key 原本只靠字串對應
course_environment_nodes (version_id, node_key)，讀取端遇到對不上的 key
會默默略過（例如防火牆規則就少一條）。改成複合外鍵，CASCADE 隨節點刪除。

外鍵設為 DEFERRABLE INITIALLY DEFERRED：_replace_nodes 在同一個 flush
新增節點與邊，沒有 relationship() 時 SQLAlchemy 不保證先 INSERT 節點。

先清掉既有的孤兒列；週次指向已不存在的班級機器節點時改回 NULL（全部機器），
週次這條不加外鍵（換課程版本會整批重建節點，改由程式在 select_course 清理）。

Revision ID: dbm07_course_node_key_fks
Revises: dbm01_single_resource_vmid
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "dbm07_course_node_key_fks"
down_revision = "dbm01_single_resource_vmid"
branch_labels = None
depends_on = None


# (約束名, 表, 參照欄位)
_FKS: tuple[tuple[str, str, str], ...] = (
    (
        "fk_course_environment_edges_source_node",
        "course_environment_edges",
        "source_node_key",
    ),
    (
        "fk_course_environment_edges_target_node",
        "course_environment_edges",
        "target_node_key",
    ),
    (
        "fk_course_environment_publications_node",
        "course_environment_publications",
        "node_key",
    ),
)


def _fk_names(table: str) -> set[str]:
    return {
        fk["name"]
        for fk in sa.inspect(op.get_bind()).get_foreign_keys(table)
        if fk.get("name")
    }


def upgrade() -> None:
    for _name, table, column in _FKS:
        op.execute(
            f"""
            DELETE FROM {table} t
            WHERE NOT EXISTS (
                SELECT 1 FROM course_environment_nodes n
                WHERE n.version_id = t.version_id AND n.node_key = t.{column}
            )
            """
        )
    op.execute(
        """
        UPDATE teaching_class_weeks w SET target_node_key = NULL
        WHERE target_node_key IS NOT NULL
          AND (
            btrim(target_node_key) = ''
            OR NOT EXISTS (
              SELECT 1 FROM teaching_class_machine_nodes m
              WHERE m.class_id = w.class_id AND m.node_key = w.target_node_key
            )
          )
        """
    )

    for name, table, column in _FKS:
        if name in _fk_names(table):
            continue
        op.create_foreign_key(
            name,
            table,
            "course_environment_nodes",
            ["version_id", column],
            ["version_id", "node_key"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
            postgresql_not_valid=True,
        )
        op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def downgrade() -> None:
    for name, table, _column in reversed(_FKS):
        if name in _fk_names(table):
            op.drop_constraint(name, table, type_="foreignkey")
