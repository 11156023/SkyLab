"""Spec change requests: user-driven apply flow.

Approval no longer applies the change to Proxmox. The requester applies it
later (the machine is shut down, reconfigured and started again), so the row
needs to track that background apply: ``apply_started_at`` / ``apply_error``.
Requests can now also be ``cancelled`` (by the requester, or automatically
when the machine is deleted).

Idempotent in both directions because shared dev databases drift from this
chain. The enum value cannot be removed on downgrade (PostgreSQL has no
DROP VALUE); it is harmless to leave in place.

Revision ID: sccl01_spec_change_apply_flow
Revises: tjatt01_msg_attachments
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sccl01_spec_change_apply_flow"
down_revision = "tjatt01_msg_attachments"
branch_labels = None
depends_on = None

_TABLE = "spec_change_requests"
_STATUS_ENUM = "specchangerequeststatus"


def _has_column(name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(col["name"] == name for col in inspector.get_columns(_TABLE))


def upgrade() -> None:
    ctx = op.get_context()
    with ctx.autocommit_block():
        op.execute(
            f"ALTER TYPE {_STATUS_ENUM} ADD VALUE IF NOT EXISTS 'cancelled'"
        )

    if not _has_column("apply_started_at"):
        op.add_column(
            _TABLE,
            sa.Column("apply_started_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column("apply_error"):
        op.add_column(_TABLE, sa.Column("apply_error", sa.String(), nullable=True))


def downgrade() -> None:
    if _has_column("apply_error"):
        op.drop_column(_TABLE, "apply_error")
    if _has_column("apply_started_at"):
        op.drop_column(_TABLE, "apply_started_at")
