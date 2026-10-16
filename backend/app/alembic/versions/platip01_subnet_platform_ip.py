"""add optional platform_ip (SkyLab host inside the lab subnet) to subnet_config

Revision ID: platip01
Revises: tjreply01
Create Date: 2026-10-16 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "platip01"
down_revision = "tjreply01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subnet_config",
        sa.Column("platform_ip", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subnet_config", "platform_ip")
