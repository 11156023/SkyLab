"""Add request-level observability fields to AI usage records.

Revision ID: aiobs01_ai_call_observability
Revises: onb01_user_onboarding
Create Date: 2026-09-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "aiobs01_ai_call_observability"
down_revision = "onb01_user_onboarding"
branch_labels = None
depends_on = None


_TABLES = ("ai_api_usage", "ai_template_call_logs")


def upgrade() -> None:
    for table_name in _TABLES:
        op.add_column(
            table_name, sa.Column("request_id", sa.String(255), nullable=True)
        )
        op.add_column(
            table_name,
            sa.Column("upstream_request_id", sa.String(255), nullable=True),
        )
        op.add_column(
            table_name, sa.Column("first_token_ms", sa.Integer(), nullable=True)
        )
        op.add_column(
            table_name,
            sa.Column(
                "stream", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
        )
        op.add_column(
            table_name,
            sa.Column(
                "usage_reported",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        op.add_column(
            table_name,
            sa.Column("response_model", sa.String(255), nullable=True),
        )
        op.add_column(
            table_name,
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            table_name,
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            f"ix_{table_name}_request_id",
            table_name,
            ["request_id"],
            unique=False,
        )
        op.execute(
            sa.text(
                f"UPDATE {table_name} SET usage_reported = true "
                "WHERE input_tokens <> 0 OR output_tokens <> 0"
            )
        )


def downgrade() -> None:
    for table_name in reversed(_TABLES):
        op.drop_index(f"ix_{table_name}_request_id", table_name=table_name)
        op.drop_column(table_name, "completed_at")
        op.drop_column(table_name, "started_at")
        op.drop_column(table_name, "response_model")
        op.drop_column(table_name, "usage_reported")
        op.drop_column(table_name, "stream")
        op.drop_column(table_name, "first_token_ms")
        op.drop_column(table_name, "upstream_request_id")
        op.drop_column(table_name, "request_id")
