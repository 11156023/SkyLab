"""add optional vlan_tag to subnet_config

Revision ID: vlan01_subnet_vlan_tag
Revises: dbw03_merge_ai_call_logs
Create Date: 2026-09-27 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "vlan01_subnet_vlan_tag"
down_revision = "dbw03_merge_ai_call_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subnet_config",
        sa.Column("vlan_tag", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subnet_config", "vlan_tag")
