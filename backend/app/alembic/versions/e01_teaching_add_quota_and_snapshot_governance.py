"""add resource_quotas table and snapshot governance fields

Revision ID: e01_teaching
Revises: gov05_mining
Create Date: 2026-07-04 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e01_teaching"
down_revision = "gov05_mining"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "governance_config",
        sa.Column(
            "snapshot_cleanup_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "governance_config",
        sa.Column(
            "snapshot_retention_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("7"),
        ),
    )
    op.add_column(
        "governance_config",
        sa.Column(
            "student_snapshot_max_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
    )

    quota_scope = sa.Enum("group", "user", name="quotascope")
    op.create_table(
        "resource_quotas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", quota_scope, nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("max_cpu_cores", sa.Integer(), nullable=False),
        sa.Column("max_memory_mb", sa.Integer(), nullable=False),
        sa.Column("max_disk_gb", sa.Integer(), nullable=False),
        sa.Column("max_instances", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["group.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", name="uq_resource_quotas_group_id"),
        sa.UniqueConstraint("user_id", name="uq_resource_quotas_user_id"),
    )


def downgrade() -> None:
    op.drop_table("resource_quotas")
    sa.Enum(name="quotascope").drop(op.get_bind(), checkfirst=True)
    op.drop_column("governance_config", "student_snapshot_max_count")
    op.drop_column("governance_config", "snapshot_retention_days")
    op.drop_column("governance_config", "snapshot_cleanup_enabled")
