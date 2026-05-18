"""repair resource IP cache columns

Revision ID: fdb02_resource_ip_cache
Revises: fdb01_resource_integrity
Create Date: 2026-05-18 00:00:00.000000

Some development databases were stamped past the historical migration that added
``resources.ip_address`` while the physical column was missing.  Keep this
repair idempotent so it is safe for both drifted and already-correct databases.

"""

from alembic import op

revision = "fdb02_resource_ip_cache"
down_revision = "fdb01_resource_integrity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE resources
        ADD COLUMN IF NOT EXISTS ip_address VARCHAR(64)
        """
    )
    op.execute(
        """
        ALTER TABLE resources
        ADD COLUMN IF NOT EXISTS ip_address_cached_at TIMESTAMP WITH TIME ZONE
        """
    )


def downgrade():
    op.execute("ALTER TABLE resources DROP COLUMN IF EXISTS ip_address_cached_at")
    op.execute("ALTER TABLE resources DROP COLUMN IF EXISTS ip_address")
