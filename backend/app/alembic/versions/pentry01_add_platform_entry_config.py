"""add platform_entry_config (主系統經 Gateway nginx 對外的平台入口)

Revision ID: pentry01_platform_entry
Revises: perf01_created_at_indexes
Create Date: 2026-10-01 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "pentry01_platform_entry"
down_revision = "perf01_created_at_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_entry_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("upstream_host", sa.String(length=255), nullable=False),
        sa.Column("upstream_port", sa.Integer(), nullable=False),
        sa.Column("enable_https", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("platform_entry_config")
