"""add resource_class_exposures

Revision ID: cexp01_resource_class_exposures
Revises: usrc01_add_user_auth_source
Create Date: 2026-09-15 00:00:00.000000

老師把自己機器的指定埠開放給整個班級。學生建立「自己的機器 → 老師的機器」
連線時，後端憑這筆事先同意替學生在老師機器上寫入站規則；沒有這筆紀錄，
連線兩端仍然都要有管理權（防火牆師生關係的非對稱授權）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "cexp01_resource_class_exposures"
down_revision = "usrc01_add_user_auth_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resource_class_exposures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resource_vmid", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("ports", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["resource_vmid"], ["resources.vmid"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["class_id"], ["teaching_classes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_vmid", "class_id", name="uq_resource_class_exposure"
        ),
    )
    op.create_index(
        "ix_resource_class_exposures_resource_vmid",
        "resource_class_exposures",
        ["resource_vmid"],
    )
    op.create_index(
        "ix_resource_class_exposures_class_id",
        "resource_class_exposures",
        ["class_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_resource_class_exposures_class_id", table_name="resource_class_exposures"
    )
    op.drop_index(
        "ix_resource_class_exposures_resource_vmid",
        table_name="resource_class_exposures",
    )
    op.drop_table("resource_class_exposures")
