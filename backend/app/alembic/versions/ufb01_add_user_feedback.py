"""Add user_feedback table.

帳號設定「關於」分頁的「使用者回饋」區塊：任何登入者送出問題回報或建議，
管理員在站內清單查看、標記已處理或刪除。送出者帳號刪除時保留內容
（user_id SET NULL），所以 user_id 可為空。

Revision ID: ufb01_user_feedback
Revises: usrgrp02_name_ci
Create Date: 2026-10-26
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "ufb01_user_feedback"
down_revision = "usrgrp02_name_ci"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "category",
            sa.Enum("bug", "suggestion", "other", name="userfeedbackcategory"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "app_version",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum("open", "resolved", name="userfeedbackstatus"),
            nullable=False,
        ),
        sa.Column("resolved_by_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_feedback_user_id"), "user_feedback", ["user_id"], unique=False
    )
    op.create_index(
        "ix_user_feedback_status_created",
        "user_feedback",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_user_feedback_created_at", "user_feedback", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_user_feedback_created_at", table_name="user_feedback")
    op.drop_index("ix_user_feedback_status_created", table_name="user_feedback")
    op.drop_index(op.f("ix_user_feedback_user_id"), table_name="user_feedback")
    op.drop_table("user_feedback")
    sa.Enum(name="userfeedbackstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="userfeedbackcategory").drop(op.get_bind(), checkfirst=True)
