"""ai_template_call_logs 併入 ai_api_usage，以 source 區分來源。

兩張表欄位幾乎相同（aiobs01 還對兩張表加同一組觀測欄位），監控查詢本來就
把兩邊合併計算。合併後：
- source：api_key（使用者金鑰經 Proxy 呼叫）｜platform（平台功能自己的 LLM 呼叫）
- credential_id 改為可為 NULL，並以 CHECK 約束「api_key 必有、platform 必無」
- request_type 更名為 call_type（兩邊都是「呼叫類型」），並新增 preset

搬移前把平台紀錄的舊狀態（'ok'／'200' 等）正規化，否則 ck_ai_api_usage_status
會擋下 INSERT。

Revision ID: dbw03_merge_ai_call_logs
Revises: dbw02_drop_pve_config_conn_cols
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "dbw03_merge_ai_call_logs"
down_revision = "dbw02_drop_pve_config_conn_cols"
branch_labels = None
depends_on = None


# 兩張表共有的欄位（call_type 在 ai_api_usage 端是由 request_type 更名而來）
_SHARED = (
    "id",
    "user_id",
    "model_name",
    "call_type",
    "preset",
    "request_id",
    "upstream_request_id",
    "input_tokens",
    "output_tokens",
    "request_duration_ms",
    "first_token_ms",
    "stream",
    "usage_reported",
    "response_model",
    "status",
    "error_message",
    "started_at",
    "completed_at",
    "created_at",
)

_NEW_INDEXES = (
    ("ix_ai_usage_user_source_created", ("user_id", "source", "created_at")),
    ("ix_ai_usage_source_created", ("source", "created_at")),
    ("ix_ai_usage_call_type_created", ("call_type", "created_at")),
)


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _index_names(table: str) -> set[str]:
    return {i["name"] for i in _inspector().get_indexes(table) if i.get("name")}


def _count(sql: str) -> int:
    return int(op.get_bind().execute(sa.text(sql)).scalar() or 0)


def upgrade() -> None:
    columns = {c["name"] for c in _inspector().get_columns("ai_api_usage")}
    if "source" not in columns:
        op.add_column(
            "ai_api_usage",
            sa.Column(
                "source", sa.String(20), nullable=False, server_default="api_key"
            ),
        )
        op.alter_column("ai_api_usage", "source", server_default=None)
    if "preset" not in columns:
        op.add_column("ai_api_usage", sa.Column("preset", sa.String(50), nullable=True))
    if "request_type" in columns:
        op.alter_column("ai_api_usage", "request_type", new_column_name="call_type")
    op.alter_column("ai_api_usage", "credential_id", nullable=True)

    if "ai_template_call_logs" in _inspector().get_table_names():
        op.execute(
            """
            UPDATE ai_template_call_logs
            SET status = CASE WHEN status IN ('ok', '200') THEN 'success' ELSE 'error' END
            WHERE status NOT IN ('success', 'error', 'cancelled')
            """
        )
        expected = _count("SELECT count(*) FROM ai_template_call_logs")
        before = _count("SELECT count(*) FROM ai_api_usage WHERE source = 'platform'")
        shared = ", ".join(_SHARED)
        op.execute(
            f"""
            INSERT INTO ai_api_usage (source, credential_id, {shared})
            SELECT 'platform', NULL, {shared} FROM ai_template_call_logs
            ON CONFLICT (id) DO NOTHING
            """
        )
        moved = (
            _count("SELECT count(*) FROM ai_api_usage WHERE source = 'platform'")
            - before
        )
        if moved != expected:
            raise RuntimeError(
                f"ai_template_call_logs 應搬移 {expected} 筆，實際 {moved} 筆（id 衝突？）"
            )
        op.drop_table("ai_template_call_logs")

    op.create_check_constraint(
        "ck_ai_api_usage_source", "ai_api_usage", "source IN ('api_key', 'platform')"
    )
    op.create_check_constraint(
        "ck_ai_api_usage_source_credential",
        "ai_api_usage",
        "(source = 'api_key') = (credential_id IS NOT NULL)",
    )
    indexes = _index_names("ai_api_usage")
    for name, cols in _NEW_INDEXES:
        if name not in indexes:
            op.create_index(name, "ai_api_usage", list(cols))
    # (user_id, source, created_at) 已涵蓋原本的 (user_id, created_at)
    if "ix_ai_usage_user_created" in indexes:
        op.drop_index("ix_ai_usage_user_created", table_name="ai_api_usage")


def downgrade() -> None:
    op.create_table(
        "ai_template_call_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey(
                "user.id",
                name="fk_ai_template_call_logs_user_id_user",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("call_type", sa.String(30), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("preset", sa.String(50), nullable=True),
        sa.Column("request_id", sa.String(255), nullable=True),
        sa.Column("upstream_request_id", sa.String(255), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_duration_ms", sa.Integer(), nullable=True),
        sa.Column("first_token_ms", sa.Integer(), nullable=True),
        sa.Column("stream", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "usage_reported", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("response_model", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('success', 'error')", name="ck_ai_template_call_logs_status"
        ),
    )
    for name, cols in (
        ("ix_ai_template_call_logs_user_id", ["user_id"]),
        ("ix_ai_template_call_logs_created_at", ["created_at"]),
        ("ix_ai_template_call_logs_request_id", ["request_id"]),
        ("ix_ai_template_call_logs_user_created", ["user_id", "created_at"]),
        ("ix_ai_template_call_logs_status_created", ["status", "created_at"]),
        ("ix_ai_template_call_logs_call_type_created", ["call_type", "created_at"]),
    ):
        op.create_index(name, "ai_template_call_logs", cols)

    shared = [c for c in _SHARED if c not in {"call_type", "status"}]
    cols = ", ".join(shared)
    op.execute(
        f"""
        INSERT INTO ai_template_call_logs (call_type, status, {cols})
        SELECT left(call_type, 30),
               CASE WHEN status = 'cancelled' THEN 'error' ELSE status END,
               {cols}
        FROM ai_api_usage WHERE source = 'platform'
        """
    )
    op.execute("DELETE FROM ai_api_usage WHERE source = 'platform'")

    for name, _cols in _NEW_INDEXES:
        op.drop_index(name, table_name="ai_api_usage")
    op.create_index(
        "ix_ai_usage_user_created", "ai_api_usage", ["user_id", "created_at"]
    )
    op.drop_constraint(
        "ck_ai_api_usage_source_credential", "ai_api_usage", type_="check"
    )
    op.drop_constraint("ck_ai_api_usage_source", "ai_api_usage", type_="check")
    op.alter_column("ai_api_usage", "credential_id", nullable=False)
    op.alter_column("ai_api_usage", "call_type", new_column_name="request_type")
    op.drop_column("ai_api_usage", "preset")
    op.drop_column("ai_api_usage", "source")
