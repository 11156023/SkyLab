"""add memory_overcommit_ratio to proxmox_config

Revision ID: memovc01
Revises: cepubpfx01
Create Date: 2026-10-18 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "memovc01"
down_revision = "cepubpfx01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "proxmox_config",
        sa.Column(
            "memory_overcommit_ratio",
            sa.Float(),
            nullable=False,
            server_default=sa.text("1.0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("proxmox_config", "memory_overcommit_ratio")
