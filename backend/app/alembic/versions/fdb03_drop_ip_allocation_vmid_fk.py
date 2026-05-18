"""drop ip allocation vmid foreign key

Revision ID: fdb03_drop_ip_allocation_vmid_fk
Revises: fdb02_resource_ip_cache
Create Date: 2026-05-18 00:00:00.000000

``ip_allocation`` reserves an address before the matching ``resources`` row is
created, so its ``vmid`` is an external Proxmox identifier rather than a strict
database relationship at allocation time.

"""

from alembic import op

revision = "fdb03_drop_ip_allocation_vmid_fk"
down_revision = "fdb02_resource_ip_cache"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE ip_allocation
        DROP CONSTRAINT IF EXISTS fk_ip_allocation_vmid_resources
        """
    )


def downgrade():
    op.execute(
        """
        ALTER TABLE ip_allocation
        ADD CONSTRAINT fk_ip_allocation_vmid_resources
        FOREIGN KEY (vmid) REFERENCES resources (vmid) ON DELETE SET NULL
        """
    )
