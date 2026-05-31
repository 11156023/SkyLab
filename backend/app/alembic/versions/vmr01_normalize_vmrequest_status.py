"""Normalize VMRequest status to review-only values.

Revision ID: vmr01_normalize_vmrequest_status
Revises: fdb05_ai_api_key_prefix_unique
Create Date: 2026-05-24 00:00:00.000000

"""

from alembic import op


revision = "vmr01_normalize_vmrequest_status"
down_revision = "fdb05_ai_api_key_prefix_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    op.execute(
        """
        UPDATE vm_requests
        SET status = 'approved'
        WHERE status IN ('scheduled', 'provisioning', 'running')
        """
    )

    if dialect != "postgresql":
        return

    op.execute("ALTER TABLE vm_requests ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE vmrequeststatus RENAME TO vmrequeststatus_old")
    op.execute(
        "CREATE TYPE vmrequeststatus AS ENUM "
        "('pending', 'approved', 'rejected', 'cancelled')"
    )
    op.execute(
        """
        ALTER TABLE vm_requests
        ALTER COLUMN status TYPE vmrequeststatus
        USING status::text::vmrequeststatus
        """
    )
    op.execute("ALTER TABLE vm_requests ALTER COLUMN status SET DEFAULT 'pending'")
    op.execute("DROP TYPE vmrequeststatus_old")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("ALTER TABLE vm_requests ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE vmrequeststatus RENAME TO vmrequeststatus_old")
    op.execute(
        "CREATE TYPE vmrequeststatus AS ENUM "
        "('pending', 'approved', 'provisioning', 'running', "
        "'rejected', 'cancelled', 'scheduled')"
    )
    op.execute(
        """
        ALTER TABLE vm_requests
        ALTER COLUMN status TYPE vmrequeststatus
        USING status::text::vmrequeststatus
        """
    )
    op.execute("ALTER TABLE vm_requests ALTER COLUMN status SET DEFAULT 'pending'")
    op.execute("DROP TYPE vmrequeststatus_old")

