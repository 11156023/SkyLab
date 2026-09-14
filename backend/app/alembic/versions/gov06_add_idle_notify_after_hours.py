"""add idle_notify_after_hours to governance_config

Revision ID: gov06_idle_notify_after
Revises: adv01_shares_expiry
Create Date: 2026-09-07 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "gov06_idle_notify_after"
down_revision = "adv01_shares_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # governance_config 為既有 singleton 表，NOT NULL 新欄位需 server_default
    op.add_column(
        "governance_config",
        sa.Column(
            "idle_notify_after_hours",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("12"),
        ),
    )


def downgrade() -> None:
    op.drop_column("governance_config", "idle_notify_after_hours")
