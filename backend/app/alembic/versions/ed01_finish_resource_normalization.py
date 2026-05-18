"""finish resource relationship normalization

Revision ID: ed01_resource_norm
Revises: ec01_norm_resource_links
Create Date: 2026-05-17 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "ed01_resource_norm"
down_revision = "ec01_norm_resource_links"
branch_labels = None
depends_on = None


def _create_index_if_not_exists(name: str, table: str, columns: tuple[str, ...]) -> None:
    column_sql = ", ".join(columns)
    op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column_sql})")


def _drop_index_if_exists(name: str) -> None:
    op.execute(f"DROP INDEX IF EXISTS {name}")


def upgrade():
    op.add_column("audit_logs", sa.Column("resource_vmid", sa.Integer(), nullable=True))
    op.add_column("firewall_layout", sa.Column("resource_vmid", sa.Integer(), nullable=True))
    op.add_column("batch_provision_tasks", sa.Column("resource_vmid", sa.Integer(), nullable=True))
    op.add_column("vm_migration_jobs", sa.Column("resource_vmid", sa.Integer(), nullable=True))

    # Final sync before removing the legacy resource-level IP cache.
    op.execute(
        """
        WITH source_rows AS (
            SELECT DISTINCT ON (ip_address)
                vmid,
                ip_address,
                ip_address_cached_at,
                created_at
            FROM resources
            WHERE ip_address IS NOT NULL
              AND btrim(ip_address) <> ''
            ORDER BY
                ip_address,
                ip_address_cached_at DESC NULLS LAST,
                created_at DESC
        )
        INSERT INTO resource_networks (
            id,
            resource_vmid,
            ip_address,
            source,
            cached_at,
            created_at,
            updated_at
        )
        SELECT
            uuid_generate_v4(),
            vmid,
            ip_address,
            'resource_cache',
            ip_address_cached_at,
            COALESCE(ip_address_cached_at, created_at, now()),
            now()
        FROM source_rows
        ON CONFLICT (ip_address) DO UPDATE
        SET
            resource_vmid = EXCLUDED.resource_vmid,
            source = EXCLUDED.source,
            cached_at = EXCLUDED.cached_at,
            updated_at = now()
        """
    )

    for table_name in (
        "audit_logs",
        "firewall_layout",
        "batch_provision_tasks",
        "vm_migration_jobs",
    ):
        op.execute(
            f"""
            UPDATE {table_name} t
            SET resource_vmid = t.vmid
            WHERE t.resource_vmid IS NULL
              AND t.vmid IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM resources r WHERE r.vmid = t.vmid
              )
            """
        )

    _create_index_if_not_exists(
        "ix_audit_logs_resource_vmid", "audit_logs", ("resource_vmid",)
    )
    _create_index_if_not_exists(
        "ix_firewall_layout_resource_vmid", "firewall_layout", ("resource_vmid",)
    )
    _create_index_if_not_exists(
        "ix_batch_provision_tasks_resource_vmid",
        "batch_provision_tasks",
        ("resource_vmid",),
    )
    _create_index_if_not_exists(
        "ix_vm_migration_jobs_resource_vmid",
        "vm_migration_jobs",
        ("resource_vmid",),
    )

    op.create_foreign_key(
        "fk_audit_logs_resource_vmid",
        "audit_logs",
        "resources",
        ["resource_vmid"],
        ["vmid"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_firewall_layout_resource_vmid",
        "firewall_layout",
        "resources",
        ["resource_vmid"],
        ["vmid"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_batch_provision_tasks_resource_vmid",
        "batch_provision_tasks",
        "resources",
        ["resource_vmid"],
        ["vmid"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_vm_migration_jobs_resource_vmid",
        "vm_migration_jobs",
        "resources",
        ["resource_vmid"],
        ["vmid"],
        ondelete="SET NULL",
    )

    op.drop_column("resources", "ip_address_cached_at")
    op.drop_column("resources", "ip_address")


def downgrade():
    op.add_column("resources", sa.Column("ip_address", sa.String(length=64), nullable=True))
    op.add_column(
        "resources",
        sa.Column("ip_address_cached_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE resources r
        SET
            ip_address = rn.ip_address,
            ip_address_cached_at = rn.cached_at
        FROM resource_networks rn
        WHERE rn.resource_vmid = r.vmid
          AND rn.ip_address IS NOT NULL
        """
    )

    for constraint_name, table_name in (
        ("fk_vm_migration_jobs_resource_vmid", "vm_migration_jobs"),
        ("fk_batch_provision_tasks_resource_vmid", "batch_provision_tasks"),
        ("fk_firewall_layout_resource_vmid", "firewall_layout"),
        ("fk_audit_logs_resource_vmid", "audit_logs"),
    ):
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")

    for index_name in (
        "ix_vm_migration_jobs_resource_vmid",
        "ix_batch_provision_tasks_resource_vmid",
        "ix_firewall_layout_resource_vmid",
        "ix_audit_logs_resource_vmid",
    ):
        _drop_index_if_exists(index_name)

    op.drop_column("vm_migration_jobs", "resource_vmid")
    op.drop_column("batch_provision_tasks", "resource_vmid")
    op.drop_column("firewall_layout", "resource_vmid")
    op.drop_column("audit_logs", "resource_vmid")
