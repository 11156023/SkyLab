"""JSON 文件欄位統一改為 jsonb（M6）。

- 以 Text 存 JSON 字串的欄位（程式自己 json.dumps / json.loads）改為 jsonb：
  task_records.payload/result、batch_provision_jobs.template_params、
  class_capacity_reservations.placement_plan/student_placements、
  course_environment_versions.draft_data。
- 已是 sa.JSON（PostgreSQL json）的欄位一併改為 jsonb：可建索引、支援路徑
  查詢、去除重複鍵；Python 端讀寫不變。
- audit_logs.details 是自由文字，不是 JSON，不動。

Text → jsonb 前先檢查既有資料是否都是合法 JSON（PG16+ 用 pg_input_is_valid，
舊版直接轉型，失敗即中止）。轉型會重寫整張表，請在維護時段執行。

Revision ID: dbm06_json_columns_to_jsonb
Revises: dbm04b_audit_action_varchar
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "dbm06_json_columns_to_jsonb"
down_revision = "dbm04b_audit_action_varchar"
branch_labels = None
depends_on = None


# 原本是 Text 的欄位：(表, 欄位, 轉型後的 server default)
_TEXT_COLUMNS: tuple[tuple[str, str, str | None], ...] = (
    ("task_records", "payload", None),
    ("task_records", "result", None),
    ("batch_provision_jobs", "template_params", None),
    ("class_capacity_reservations", "placement_plan", None),
    ("class_capacity_reservations", "student_placements", "'{}'::jsonb"),
    ("course_environment_versions", "draft_data", None),
)

# 原本就是 json 的欄位
_JSON_COLUMNS: tuple[tuple[str, str], ...] = (
    ("resources", "guest_os"),
    ("teacher_judge_files", "environment_keys"),
    ("teacher_judge_files", "analysis_json"),
    ("teacher_judge_script_artifacts", "rubric_snapshot_json"),
    ("teacher_judge_script_artifacts", "source_file_snapshot_json"),
    ("teacher_judge_script_artifacts", "policy_check_result_json"),
    ("teacher_judge_script_artifacts", "ai_review_result_json"),
    ("teacher_judge_script_runs", "target_snapshot_json"),
    ("teacher_judge_script_runs", "progress_json"),
    ("teacher_judge_script_runs", "result_summary_json"),
    ("teacher_judge_script_runs", "target_results_json"),
    ("teacher_judge_session_messages", "metadata_json"),
    ("teacher_judge_student_submissions", "completed_item_ids"),
    ("wireguard_peers", "allowed_endpoints"),
)


def _column_type(table: str, column: str) -> str | None:
    for col in sa.inspect(op.get_bind()).get_columns(table):
        if col["name"] == column:
            return type(col["type"]).__name__.upper()
    return None


def _column_default(table: str, column: str) -> str | None:
    return (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        )
        .scalar()
    )


def _retype_json(table: str, column: str, target: str) -> None:
    """json ⇄ jsonb 轉型；既有 server default（例如 '{}'::json）先拿掉再換型別。"""
    default = _column_default(table, column)
    if default is not None:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT")
    op.execute(
        f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {target} "
        f"USING {column}::{target}"
    )
    if default is not None:
        literal = default.split("::", 1)[0]
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {literal}::{target}"
        )


def _assert_valid_json(table: str, column: str) -> None:
    bind = op.get_bind()
    version = int(bind.execute(sa.text("SHOW server_version_num")).scalar() or 0)
    if version < 160000:
        return  # 沒有 pg_input_is_valid；轉型失敗時 migration 會直接中止
    bad = bind.execute(
        sa.text(
            f"SELECT count(*) FROM {table} "
            f"WHERE {column} IS NOT NULL AND NOT pg_input_is_valid({column}, 'jsonb')"
        )
    ).scalar()
    if bad:
        raise RuntimeError(
            f"{table}.{column} 有 {bad} 筆不是合法 JSON，請先清理再執行 migration"
        )


def upgrade() -> None:
    for table, column, default in _TEXT_COLUMNS:
        if _column_type(table, column) == "JSONB":
            continue
        _assert_valid_json(table, column)
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT")
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE jsonb "
            f"USING {column}::jsonb"
        )
        if default is not None:
            op.execute(
                f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {default}"
            )

    for table, column in _JSON_COLUMNS:
        if _column_type(table, column) == "JSONB":
            continue
        _retype_json(table, column, "jsonb")


def downgrade() -> None:
    for table, column in reversed(_JSON_COLUMNS):
        _retype_json(table, column, "json")

    for table, column, default in reversed(_TEXT_COLUMNS):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT")
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE text USING {column}::text"
        )
        if default is not None:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT '{{}}'")
