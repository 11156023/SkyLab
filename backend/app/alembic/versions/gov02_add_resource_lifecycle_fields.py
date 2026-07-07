"""add resource lifecycle fields

Revision ID: gov02_resource_lifecycle
Revises: gov01_governance_alerts
Create Date: 2026-07-04 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "gov02_resource_lifecycle"
down_revision = "gov01_governance_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "resources",
        sa.Column("expiry_notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "resources",
        sa.Column("idle_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "resources",
        sa.Column("idle_notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "resources",
        sa.Column("idle_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "resources",
        sa.Column(
            "scheduled_deletion_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("resources", "scheduled_deletion_at")
    op.drop_column("resources", "idle_checked_at")
    op.drop_column("resources", "idle_notified_at")
    op.drop_column("resources", "idle_since")
    op.drop_column("resources", "expiry_notified_at")
