"""drop legacy nat unique constraint

Revision ID: fdb04_drop_legacy_nat_unique
Revises: fdb03_drop_ip_allocation_vmid_fk
Create Date: 2026-05-18 00:00:00.000000

``ec01`` introduced the NAT uniqueness constraint with a shorter legacy name,
while the model and later repair migration use the explicit
``uq_nat_rule_host_external_port_protocol`` name.  Keep only the model name so
Alembic autogenerate has no extra database constraint to remove.

"""

from alembic import op

revision = "fdb04_drop_legacy_nat_unique"
down_revision = "fdb03_drop_ip_allocation_vmid_fk"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE nat_rule
        DROP CONSTRAINT IF EXISTS uq_nat_rule_host_port_protocol
        """
    )


def downgrade():
    op.execute(
        """
        ALTER TABLE nat_rule
        ADD CONSTRAINT uq_nat_rule_host_port_protocol
        UNIQUE (ssh_host, external_port, protocol)
        """
    )
