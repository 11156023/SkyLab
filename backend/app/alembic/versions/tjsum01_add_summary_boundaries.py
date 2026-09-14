"""Track the message boundary covered by Teacher Judge summaries.

Revision ID: tjsum01_summary_boundaries
Revises: sccl01_spec_change_apply_flow
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "tjsum01_summary_boundaries"
down_revision = "sccl01_spec_change_apply_flow"
branch_labels = None
depends_on = None

_TABLE = "teacher_judge_sessions"


def _has_column(name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(column["name"] == name for column in inspector.get_columns(_TABLE))


def upgrade() -> None:
    if not _has_column("summary_through_message_id"):
        op.add_column(
            _TABLE,
            sa.Column("summary_through_message_id", sa.Uuid(), nullable=True),
        )
    if not _has_column("summary_through_assistant_count"):
        op.add_column(
            _TABLE,
            sa.Column(
                "summary_through_assistant_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    if _has_column("summary_through_assistant_count"):
        op.drop_column(_TABLE, "summary_through_assistant_count")
    if _has_column("summary_through_message_id"):
        op.drop_column(_TABLE, "summary_through_message_id")
