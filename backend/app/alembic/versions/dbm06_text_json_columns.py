"""以 Text 存 JSON 字串的欄位改為 json 型別（M6）。

task_records.payload/result、batch_provision_jobs.template_params、
class_capacity_reservations.placement_plan/student_placements、
course_environment_versions.draft_data 原本由程式自己 json.dumps / json.loads，
資料庫不驗證內容。改為 json 型別，與其他 sa.JSON 欄位一致。

刻意用 json 而非 jsonb：資料庫若以 SQL_ASCII 編碼建立，jsonb 無法把
unicode 跳脫序列表示的中文轉成伺服器編碼（SQLAlchemy 預設以 ensure_ascii 序列化），
既有資料轉型與之後的寫入都會失敗；json 只檢查語法、原文保存，不受影響。
audit_logs.details 是自由文字，不動。

轉型前先檢查既有資料皆為合法 JSON（PG16+ 用 pg_input_is_valid），
轉型會重寫整張表，請在維護時段執行。

Revision ID: dbm06_text_json_columns
Revises: dbm04b_audit_action_varchar
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "dbm06_text_json_columns"
down_revision = "dbm04b_audit_action_varchar"
branch_labels = None
depends_on = None


# (表, 欄位, 轉型後的 server default)
_COLUMNS: tuple[tuple[str, str, str | None], ...] = (
    ("task_records", "payload", None),
    ("task_records", "result", None),
    ("batch_provision_jobs", "template_params", None),
    ("class_capacity_reservations", "placement_plan", None),
    ("class_capacity_reservations", "student_placements", "'{}'::json"),
    ("course_environment_versions", "draft_data", None),
)


def _is_text(table: str, column: str) -> bool:
    for col in sa.inspect(op.get_bind()).get_columns(table):
        if col["name"] == column:
            return isinstance(col["type"], sa.Text | sa.String)
    return False


def _assert_valid_json(table: str, column: str) -> None:
    bind = op.get_bind()
    version = int(bind.execute(sa.text("SHOW server_version_num")).scalar() or 0)
    if version < 160000:
        return  # 沒有 pg_input_is_valid；轉型失敗時 migration 會直接中止
    bad = bind.execute(
        sa.text(
            f"SELECT count(*) FROM {table} "
            f"WHERE {column} IS NOT NULL AND NOT pg_input_is_valid({column}, 'json')"
        )
    ).scalar()
    if bad:
        raise RuntimeError(
            f"{table}.{column} 有 {bad} 筆不是合法 JSON，請先清理再執行 migration"
        )


def upgrade() -> None:
    for table, column, default in _COLUMNS:
        if not _is_text(table, column):
            continue
        _assert_valid_json(table, column)
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT")
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE json USING {column}::json"
        )
        if default is not None:
            op.execute(
                f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {default}"
            )


def downgrade() -> None:
    for table, column, default in reversed(_COLUMNS):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT")
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE text USING {column}::text"
        )
        if default is not None:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT '{{}}'")
