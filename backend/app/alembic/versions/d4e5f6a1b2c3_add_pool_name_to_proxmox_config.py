"""add pool_name to proxmox_config

Revision ID: d4e5f6a1b2c3
Revises: c3d4e5f6a1b2
Create Date: 2026-03-21 02:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d4e5f6a1b2c3"
down_revision = "c3d4e5f6a1b2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "proxmox_config",
        sa.Column(
            "pool_name",
            sa.String(length=255),
            nullable=False,
            server_default="SkyLab",
        ),
    )


def downgrade():
    op.drop_column("proxmox_config", "pool_name")
