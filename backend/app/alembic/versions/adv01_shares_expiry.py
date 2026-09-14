"""Resource advanced settings: sharing table + expiry extension requests.

- ``resource_shares``: owner grants another user control-level access
  (start/stop/console) to one VM.
- ``spec_change_requests``: ``expiry`` change type with
  ``current_expiry_date`` / ``requested_expiry_date`` so expiry extensions
  ride the existing review flow.

Idempotent in both directions because shared dev databases drift from this
chain. The enum value cannot be removed on downgrade (PostgreSQL has no
DROP VALUE); it is harmless to leave in place.

Revision ID: adv01_shares_expiry
Revises: sccl01_spec_change_apply_flow
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "adv01_shares_expiry"
down_revision = "sccl01_spec_change_apply_flow"
branch_labels = None
depends_on = None

_SHARES = "resource_shares"
_SPEC = "spec_change_requests"
_TYPE_ENUM = "specchangetype"
_AUDIT_ENUM = "auditaction"
_NEW_AUDIT_ACTIONS = ("credential_update", "resource_share_update", "resource_transfer")


def _has_table(name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return inspector.has_table(name)


def _has_column(table: str, name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(col["name"] == name for col in inspector.get_columns(table))


def upgrade() -> None:
    ctx = op.get_context()
    with ctx.autocommit_block():
        op.execute(f"ALTER TYPE {_TYPE_ENUM} ADD VALUE IF NOT EXISTS 'expiry'")
        for action in _NEW_AUDIT_ACTIONS:
            op.execute(f"ALTER TYPE {_AUDIT_ENUM} ADD VALUE IF NOT EXISTS '{action}'")

    if not _has_column(_SPEC, "current_expiry_date"):
        op.add_column(_SPEC, sa.Column("current_expiry_date", sa.Date(), nullable=True))
    if not _has_column(_SPEC, "requested_expiry_date"):
        op.add_column(
            _SPEC, sa.Column("requested_expiry_date", sa.Date(), nullable=True)
        )

    if not _has_table(_SHARES):
        op.create_table(
            _SHARES,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("resource_vmid", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("granted_by", sa.Uuid(), nullable=True),
            sa.Column("permission", sa.String(length=24), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["resource_vmid"], ["resources.vmid"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["granted_by"], ["user.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "resource_vmid", "user_id", name="uq_resource_share_user"
            ),
        )
        op.create_index("ix_resource_shares_resource_vmid", _SHARES, ["resource_vmid"])
        op.create_index("ix_resource_shares_user_id", _SHARES, ["user_id"])


def downgrade() -> None:
    if _has_table(_SHARES):
        op.drop_index("ix_resource_shares_user_id", table_name=_SHARES)
        op.drop_index("ix_resource_shares_resource_vmid", table_name=_SHARES)
        op.drop_table(_SHARES)
    if _has_column(_SPEC, "requested_expiry_date"):
        op.drop_column(_SPEC, "requested_expiry_date")
    if _has_column(_SPEC, "current_expiry_date"):
        op.drop_column(_SPEC, "current_expiry_date")
