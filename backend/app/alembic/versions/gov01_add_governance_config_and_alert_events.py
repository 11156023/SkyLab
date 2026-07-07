"""add governance config and alert events

Revision ID: gov01_governance_alerts
Revises: vmt01_vm_templates
Create Date: 2026-07-04 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision = "gov01_governance_alerts"
down_revision = "vmt01_vm_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "governance_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("alerts_enabled", sa.Boolean(), nullable=False),
        sa.Column("alert_cpu_threshold", sa.Float(), nullable=False),
        sa.Column("alert_memory_threshold", sa.Float(), nullable=False),
        sa.Column("alert_disk_threshold", sa.Float(), nullable=False),
        sa.Column("alert_cooldown_minutes", sa.Integer(), nullable=False),
        sa.Column("alert_check_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("alert_email_enabled", sa.Boolean(), nullable=False),
        sa.Column("ttl_enabled", sa.Boolean(), nullable=False),
        sa.Column("expiry_warn_days", sa.Integer(), nullable=False),
        sa.Column("expiry_grace_delete_days", sa.Integer(), nullable=False),
        sa.Column("idle_detection_enabled", sa.Boolean(), nullable=False),
        sa.Column("idle_cpu_threshold_percent", sa.Float(), nullable=False),
        sa.Column("idle_window_hours", sa.Integer(), nullable=False),
        sa.Column("idle_grace_hours", sa.Integer(), nullable=False),
        sa.Column("idle_scan_batch_size", sa.Integer(), nullable=False),
        sa.Column("workload_advisor_enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    alert_scope = sa.Enum("cluster", "node", "vm", name="alertscope")
    alert_metric = sa.Enum("cpu", "memory", "disk", name="alertmetric")

    op.create_table(
        "alert_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", alert_scope, nullable=False),
        sa.Column(
            "target",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column("metric", alert_metric, nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("message", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.Uuid(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["acknowledged_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_alert_events_target_metric",
        "alert_events",
        ["target", "metric"],
    )
    op.create_index(
        "ix_alert_events_resolved_at",
        "alert_events",
        ["resolved_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_alert_events_resolved_at", table_name="alert_events")
    op.drop_index("ix_alert_events_target_metric", table_name="alert_events")
    op.drop_table("alert_events")
    op.drop_table("governance_config")
    sa.Enum(name="alertscope").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="alertmetric").drop(op.get_bind(), checkfirst=True)
