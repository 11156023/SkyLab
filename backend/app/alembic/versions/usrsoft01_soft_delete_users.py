"""Retain deleted accounts for AI accounting and audit history.

Revision ID: usrsoft01_soft_delete_users
Revises: aiapi01_control_guards
Create Date: 2026-10-06
"""

import sqlalchemy as sa
from alembic import op

revision = "usrsoft01_soft_delete_users"
down_revision = "aiapi01_control_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.add_column(
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_check_constraint(
            "ck_user_deleted_inactive", "deleted_at IS NULL OR is_active = false"
        )


def downgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.drop_constraint("ck_user_deleted_inactive", type_="check")
        batch.drop_column("deleted_at")
