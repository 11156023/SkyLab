"""add vm request auto decision fields

Revision ID: gov03_vm_request_auto
Revises: gov02_resource_lifecycle
Create Date: 2026-07-04 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision = "gov03_vm_request_auto"
down_revision = "gov02_resource_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vm_requests",
        sa.Column(
            "requested_mode",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "vm_requests",
        sa.Column(
            "auto_decision_reason",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("vm_requests", "auto_decision_reason")
    op.drop_column("vm_requests", "requested_mode")
