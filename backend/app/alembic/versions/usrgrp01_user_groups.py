"""Administrator-managed user groups that teachers can import into classes.

Revision ID: usrgrp01_user_groups
Revises: regpolicy02_google_signup

The tables are new names on purpose: tc03_retire_test_groups dropped the old
``group`` / ``group_member`` tables and its downgrade recreates them.
"""

import sqlalchemy as sa
from alembic import op

revision = "usrgrp01_user_groups"
down_revision = "regpolicy02_google_signup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "user_group_members",
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["user_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "user_id"),
    )
    op.create_index("ix_user_group_members_user_id", "user_group_members", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_group_members_user_id", table_name="user_group_members")
    op.drop_table("user_group_members")
    op.drop_table("user_groups")
