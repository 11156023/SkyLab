"""Recurrence schedule, batch review, auto-stop, and scheduled boot tuning.

This consolidates several schema changes:

- Recurrence columns on ``vm_requests`` and ``batch_provision_jobs``.
- Review columns on ``batch_provision_jobs``.
- New ``auto_stop_at`` / ``auto_stop_reason`` columns on ``resources``.
- New scheduled boot / auto-stop tuning fields on ``proxmox_config``.
- Missing enum labels: ``vmrequeststatus.scheduled`` and
  ``batchprovisionjobstatus.{approved,cancelled,pending_review,rejected}``.

The first set of columns (recurrence + review) is added **only when missing** —
the migration inspects ``information_schema`` first, so on shared DBs that
already have those columns (from a parallel branch that was applied directly)
the migration acquires no lock on the hot ``vm_requests`` table.

Revision ID: r7s8t9u0v1w2
Revises: gp01_gpu_mapping
Create Date: 2026-04-26 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "r7s8t9u0v1w2"
down_revision = "gp01_gpu_mapping"
branch_labels = None
depends_on = None


# Columns that may already exist on shared DBs (added by an unmerged branch).
# (table, column, sa.Column factory)
_OPTIONAL_COLUMNS: list[tuple[str, str, sa.Column]] = [
    ("vm_requests", "recurrence_rule", sa.Column("recurrence_rule", sa.String(), nullable=True)),
    ("vm_requests", "recurrence_duration_minutes", sa.Column("recurrence_duration_minutes", sa.Integer(), nullable=True)),
    ("vm_requests", "schedule_timezone", sa.Column("schedule_timezone", sa.String(), nullable=True)),
    ("vm_requests", "next_window_start", sa.Column("next_window_start", sa.DateTime(timezone=True), nullable=True)),
    ("vm_requests", "next_window_end", sa.Column("next_window_end", sa.DateTime(timezone=True), nullable=True)),
    ("vm_requests", "batch_job_id", sa.Column("batch_job_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True)),
    ("batch_provision_jobs", "reviewer_id", sa.Column("reviewer_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True)),
    ("batch_provision_jobs", "reviewed_at", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)),
    ("batch_provision_jobs", "review_comment", sa.Column("review_comment", sa.String(length=500), nullable=True)),
    ("batch_provision_jobs", "recurrence_rule", sa.Column("recurrence_rule", sa.String(), nullable=True)),
    ("batch_provision_jobs", "recurrence_duration_minutes", sa.Column("recurrence_duration_minutes", sa.Integer(), nullable=True)),
    ("batch_provision_jobs", "schedule_timezone", sa.Column("schedule_timezone", sa.String(), nullable=True)),
]

# Foreign keys to add idempotently after their columns exist.
# (constraint_name, table, column, referred_table, referred_column, ondelete)
_OPTIONAL_FKS: list[tuple[str, str, str, str, str, str]] = [
    (
        "fk_vm_requests_batch_job_id",
        "vm_requests", "batch_job_id",
        "batch_provision_jobs", "id", "SET NULL",
    ),
    (
        "fk_batch_provision_jobs_reviewer_id",
        "batch_provision_jobs", "reviewer_id",
        "user", "id", "SET NULL",
    ),
]


def _column_exists(bind: sa.engine.Connection, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    return any(c["name"] == column for c in inspector.get_columns(table))


def _constraint_exists(bind: sa.engine.Connection, table: str, name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(c["name"] == name for c in inspector.get_foreign_keys(table))


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Add optional columns one-by-one, skipping any that already exist.
    #    This avoids touching vm_requests on shared DBs where the columns are
    #    already present.
    for table, column, col_def in _OPTIONAL_COLUMNS:
        if not _column_exists(bind, table, column):
            op.add_column(table, col_def)

    # 2. Add FK constraints only if missing.
    for name, table, col, ref_table, ref_col, on_delete in _OPTIONAL_FKS:
        if _column_exists(bind, table, col) and not _constraint_exists(bind, table, name):
            op.create_foreign_key(
                name, table, ref_table, [col], [ref_col], ondelete=on_delete,
            )

    # 3. Genuinely new columns on cold tables.
    op.add_column(
        "resources",
        sa.Column("auto_stop_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "resources",
        sa.Column("auto_stop_reason", sa.String(length=32), nullable=True),
    )

    op.add_column(
        "proxmox_config",
        sa.Column(
            "scheduled_boot_batch_size",
            sa.Integer(), nullable=False, server_default="5",
        ),
    )
    op.add_column(
        "proxmox_config",
        sa.Column(
            "scheduled_boot_batch_interval_seconds",
            sa.Integer(), nullable=False, server_default="10",
        ),
    )
    op.add_column(
        "proxmox_config",
        sa.Column(
            "scheduled_boot_lead_time_minutes",
            sa.Integer(), nullable=False, server_default="5",
        ),
    )
    op.add_column(
        "proxmox_config",
        sa.Column(
            "window_grace_period_minutes",
            sa.Integer(), nullable=False, server_default="30",
        ),
    )
    op.add_column(
        "proxmox_config",
        sa.Column(
            "practice_session_hours",
            sa.Integer(), nullable=False, server_default="3",
        ),
    )
    op.add_column(
        "proxmox_config",
        sa.Column(
            "practice_warning_minutes",
            sa.Integer(), nullable=False, server_default="30",
        ),
    )

    # 4. Add missing enum labels. ``ALTER TYPE ... ADD VALUE`` must run outside
    #    a transaction; ``autocommit_block`` handles that on PostgreSQL.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE vmrequeststatus ADD VALUE IF NOT EXISTS 'scheduled'")
        op.execute(
            "ALTER TYPE batchprovisionjobstatus ADD VALUE IF NOT EXISTS 'approved'"
        )
        op.execute(
            "ALTER TYPE batchprovisionjobstatus ADD VALUE IF NOT EXISTS 'cancelled'"
        )
        op.execute(
            "ALTER TYPE batchprovisionjobstatus ADD VALUE IF NOT EXISTS 'pending_review'"
        )
        op.execute(
            "ALTER TYPE batchprovisionjobstatus ADD VALUE IF NOT EXISTS 'rejected'"
        )


def downgrade() -> None:
    op.drop_column("proxmox_config", "practice_warning_minutes")
    op.drop_column("proxmox_config", "practice_session_hours")
    op.drop_column("proxmox_config", "window_grace_period_minutes")
    op.drop_column("proxmox_config", "scheduled_boot_lead_time_minutes")
    op.drop_column("proxmox_config", "scheduled_boot_batch_interval_seconds")
    op.drop_column("proxmox_config", "scheduled_boot_batch_size")
    op.drop_column("resources", "auto_stop_reason")
    op.drop_column("resources", "auto_stop_at")

    bind = op.get_bind()
    for name, table, _col, _r, _c, _od in _OPTIONAL_FKS:
        if _constraint_exists(bind, table, name):
            op.drop_constraint(name, table, type_="foreignkey")

    for table, column, _col_def in reversed(_OPTIONAL_COLUMNS):
        if _column_exists(bind, table, column):
            op.drop_column(table, column)

    # Note: PostgreSQL does not support removing enum values; downgrade leaves
    # the enum labels in place. This is fine because no rows can use the new
    # labels by the time downgrade completes (the dependent code/columns are
    # dropped above).
