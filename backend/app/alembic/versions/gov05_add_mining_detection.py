"""add mining detection config, resource fields and incidents table

Revision ID: gov05_mining
Revises: gov04_ldap_config
Create Date: 2026-07-04 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision = "gov05_mining"
down_revision = "gov04_ldap_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # governance_config 為既有 singleton 表，NOT NULL 新欄位需 server_default
    op.add_column(
        "governance_config",
        sa.Column(
            "mining_detection_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "governance_config",
        sa.Column(
            "mining_cpu_threshold_percent",
            sa.Float(),
            nullable=False,
            server_default=sa.text("90.0"),
        ),
    )
    op.add_column(
        "governance_config",
        sa.Column(
            "mining_window_hours",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("6"),
        ),
    )
    op.add_column(
        "governance_config",
        sa.Column(
            "mining_scan_batch_size",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("20"),
        ),
    )
    op.add_column(
        "governance_config",
        sa.Column(
            "mining_auto_suspend",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "governance_config",
        sa.Column(
            "provision_max_concurrency",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("4"),
        ),
    )

    op.add_column(
        "resources",
        sa.Column(
            "mining_exempt",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "resources",
        sa.Column("mining_checked_at", sa.DateTime(timezone=True), nullable=True),
    )

    incident_status = sa.Enum(
        "detected", "suspended", "banned", "dismissed",
        name="miningincidentstatus",
    )

    op.create_table(
        "mining_incidents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("vmid", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "node",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "resource_type",
            sqlmodel.sql.sqltypes.AutoString(length=8),
            nullable=False,
        ),
        sa.Column("avg_cpu", sa.Float(), nullable=False),
        sa.Column("window_hours", sa.Integer(), nullable=False),
        sa.Column(
            "snapshot_name",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=True,
        ),
        sa.Column("status", incident_status, nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "review_note",
            sqlmodel.sql.sqltypes.AutoString(length=1024),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mining_incidents_vmid", "mining_incidents", ["vmid"])
    op.create_index("ix_mining_incidents_user_id", "mining_incidents", ["user_id"])
    op.create_index("ix_mining_incidents_status", "mining_incidents", ["status"])
    op.create_index(
        "ix_mining_incidents_vmid_status",
        "mining_incidents",
        ["vmid", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_mining_incidents_vmid_status", table_name="mining_incidents")
    op.drop_index("ix_mining_incidents_status", table_name="mining_incidents")
    op.drop_index("ix_mining_incidents_user_id", table_name="mining_incidents")
    op.drop_index("ix_mining_incidents_vmid", table_name="mining_incidents")
    op.drop_table("mining_incidents")
    sa.Enum(name="miningincidentstatus").drop(op.get_bind(), checkfirst=True)
    op.drop_column("resources", "mining_checked_at")
    op.drop_column("resources", "mining_exempt")
    op.drop_column("governance_config", "provision_max_concurrency")
    op.drop_column("governance_config", "mining_auto_suspend")
    op.drop_column("governance_config", "mining_scan_batch_size")
    op.drop_column("governance_config", "mining_window_hours")
    op.drop_column("governance_config", "mining_cpu_threshold_percent")
    op.drop_column("governance_config", "mining_detection_enabled")
