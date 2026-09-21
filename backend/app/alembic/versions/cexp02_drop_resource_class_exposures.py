"""Drop resource_class_exposures.

「開放給班級」（老師事先同意、學生自己拉線連到老師機器）整段移除：老師要把
服務給其他機器用，一律走連線對話框的「僅開放防火牆」——那條入站 ACCEPT
不限來源，同一個內網的機器都連得到，不需要逐班授權。

已經套在機器上的學生連線規則不在這張表裡（規則只存在 Proxmox），這支
migration 不碰；兩端各自在規則面板刪自己機器上的那條即可。

Revision ID: cexp02_drop_class_exposures
Revises: clrm01_drop_course_lab_deploy
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "cexp02_drop_class_exposures"
down_revision = "clrm01_drop_course_lab_deploy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "resource_class_exposures" in inspector.get_table_names():
        op.drop_table("resource_class_exposures")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "resource_class_exposures" in inspector.get_table_names():
        return
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
