"""Drop dead tables and columns.

- ai_pve_templates: its service, repository and routes were removed in #310;
  nothing reads or writes the table any more.
- user.is_superuser / user.is_instructor: fully derived from ``role``
  (is_instructor was always written as False). ``User.is_superuser`` is now a
  read-only property. Rows where is_superuser was set but role was not admin
  were already treated as admin by the permission layer, so they are promoted
  to keep their effective permissions.
- resource_networks.mac_address / bridge_name: never written.
- governance_config.course_ttl_hours / course_max_active_per_user: Course Lab
  was removed in clrm01; nothing reads these settings.

Revision ID: dead01_drop_dead_tables_cols
Revises: aiobs01_ai_call_observability
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "dead01_drop_dead_tables_cols"
down_revision = "aiobs01_ai_call_observability"
branch_labels = None
depends_on = None


_DEAD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("user", "is_superuser"),
    ("user", "is_instructor"),
    ("resource_networks", "mac_address"),
    ("resource_networks", "bridge_name"),
    ("governance_config", "course_ttl_hours"),
    ("governance_config", "course_max_active_per_user"),
)


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if "is_superuser" in _columns(inspector, "user"):
        op.execute(
            "UPDATE \"user\" SET role = 'admin' WHERE is_superuser AND role <> 'admin'"
        )

    for table, column in _DEAD_COLUMNS:
        if column in _columns(inspector, table):
            op.drop_column(table, column)

    if "ai_pve_templates" in inspector.get_table_names():
        op.drop_table("ai_pve_templates")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    user_columns = _columns(inspector, "user")
    if "is_superuser" not in user_columns:
        op.add_column(
            "user",
            sa.Column(
                "is_superuser", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
        )
        op.execute("UPDATE \"user\" SET is_superuser = (role = 'admin')")
    if "is_instructor" not in user_columns:
        op.add_column(
            "user",
            sa.Column(
                "is_instructor", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
        )

    network_columns = _columns(inspector, "resource_networks")
    for column in ("mac_address", "bridge_name"):
        if column not in network_columns:
            op.add_column(
                "resource_networks",
                sa.Column(column, sa.String(length=64), nullable=True),
            )

    governance_columns = _columns(inspector, "governance_config")
    if "course_ttl_hours" not in governance_columns:
        op.add_column(
            "governance_config",
            sa.Column(
                "course_ttl_hours", sa.Integer(), nullable=False, server_default="3"
            ),
        )
    if "course_max_active_per_user" not in governance_columns:
        op.add_column(
            "governance_config",
            sa.Column(
                "course_max_active_per_user",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        )

    if "ai_pve_templates" not in inspector.get_table_names():
        # Recreated empty; the seed rows from aipve01 are not restored.
        op.create_table(
            "ai_pve_templates",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("template_key", sa.String(length=50), nullable=False),
            sa.Column("display_name", sa.String(length=100), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("system_prompt", sa.Text(), nullable=False),
            sa.Column(
                "enabled", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "template_key", name="uq_ai_pve_templates_template_key"
            ),
        )
        op.create_index(
            op.f("ix_ai_pve_templates_template_key"),
            "ai_pve_templates",
            ["template_key"],
        )
        op.create_index(
            op.f("ix_ai_pve_templates_enabled"), "ai_pve_templates", ["enabled"]
        )
