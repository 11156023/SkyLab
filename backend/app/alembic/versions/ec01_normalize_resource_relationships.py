"""normalize resource relationships and add database constraints

Revision ID: ec01_norm_resource_links
Revises: eb01_add_expiry_warning_hours
Create Date: 2026-05-17 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "ec01_norm_resource_links"
down_revision = "eb01_add_expiry_warning_hours"
branch_labels = None
depends_on = None


def _create_index_if_not_exists(
    name: str,
    table: str,
    columns: tuple[str, ...],
    *,
    unique: bool = False,
) -> None:
    unique_sql = "UNIQUE " if unique else ""
    column_sql = ", ".join(columns)
    op.execute(f"CREATE {unique_sql}INDEX IF NOT EXISTS {name} ON {table} ({column_sql})")


def _drop_index_if_exists(name: str) -> None:
    op.execute(f"DROP INDEX IF EXISTS {name}")


def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    op.add_column("resources", sa.Column("request_id", sa.Uuid(), nullable=True))
    op.add_column("spec_change_requests", sa.Column("resource_vmid", sa.Integer(), nullable=True))
    op.add_column("deletion_requests", sa.Column("resource_vmid", sa.Integer(), nullable=True))
    op.add_column("nat_rule", sa.Column("resource_vmid", sa.Integer(), nullable=True))
    op.add_column("reverse_proxy_rule", sa.Column("resource_vmid", sa.Integer(), nullable=True))
    op.add_column("tunnel_proxies", sa.Column("resource_vmid", sa.Integer(), nullable=True))
    op.add_column("ip_allocation", sa.Column("resource_vmid", sa.Integer(), nullable=True))
    op.add_column("script_deploy_logs", sa.Column("resource_vmid", sa.Integer(), nullable=True))

    op.create_table(
        "resource_networks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resource_vmid", sa.Integer(), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("mac_address", sa.String(length=64), nullable=True),
        sa.Column("bridge_name", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("cached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["resource_vmid"],
            ["resources.vmid"],
            name="fk_resource_networks_resource_vmid",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Backfill request/resource links from existing denormalized vmid columns.
    op.execute(
        """
        WITH ranked AS (
            SELECT
                r.vmid,
                v.id AS request_id,
                row_number() OVER (
                    PARTITION BY r.vmid
                    ORDER BY
                        CASE v.status::text
                            WHEN 'running' THEN 0
                            WHEN 'scheduled' THEN 1
                            WHEN 'provisioning' THEN 2
                            WHEN 'approved' THEN 3
                            ELSE 4
                        END,
                        v.created_at DESC
                ) AS rn
            FROM resources r
            JOIN vm_requests v ON v.vmid = r.vmid
            WHERE r.request_id IS NULL
        )
        UPDATE resources r
        SET request_id = ranked.request_id
        FROM ranked
        WHERE ranked.rn = 1
          AND ranked.vmid = r.vmid
        """
    )

    for table_name in (
        "spec_change_requests",
        "deletion_requests",
        "nat_rule",
        "reverse_proxy_rule",
        "tunnel_proxies",
        "ip_allocation",
        "script_deploy_logs",
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
        ON CONFLICT DO NOTHING
        """
    )

    _create_index_if_not_exists("ix_resources_request_id", "resources", ("request_id",), unique=True)
    _create_index_if_not_exists("ix_resources_user_id", "resources", ("user_id",))
    _create_index_if_not_exists("ix_resources_user_created", "resources", ("user_id", "created_at"))
    _create_index_if_not_exists("ix_resources_auto_stop_at", "resources", ("auto_stop_at",))

    _create_index_if_not_exists(
        "ix_spec_change_requests_resource_vmid",
        "spec_change_requests",
        ("resource_vmid",),
    )
    _create_index_if_not_exists(
        "ix_deletion_requests_resource_vmid",
        "deletion_requests",
        ("resource_vmid",),
    )
    _create_index_if_not_exists("ix_nat_rule_resource_vmid", "nat_rule", ("resource_vmid",))
    _create_index_if_not_exists(
        "ix_reverse_proxy_rule_resource_vmid",
        "reverse_proxy_rule",
        ("resource_vmid",),
    )
    _create_index_if_not_exists(
        "ix_tunnel_proxies_resource_vmid",
        "tunnel_proxies",
        ("resource_vmid",),
    )
    _create_index_if_not_exists("ix_ip_allocation_resource_vmid", "ip_allocation", ("resource_vmid",))
    _create_index_if_not_exists(
        "ix_script_deploy_logs_resource_vmid",
        "script_deploy_logs",
        ("resource_vmid",),
    )
    _create_index_if_not_exists(
        "ix_resource_networks_resource_vmid",
        "resource_networks",
        ("resource_vmid",),
    )
    _create_index_if_not_exists(
        "ix_resource_networks_ip_address",
        "resource_networks",
        ("ip_address",),
        unique=True,
    )

    _create_index_if_not_exists("ix_vm_requests_user_id", "vm_requests", ("user_id",))
    _create_index_if_not_exists("ix_vm_requests_vmid", "vm_requests", ("vmid",))
    _create_index_if_not_exists(
        "ix_vm_requests_user_status_created",
        "vm_requests",
        ("user_id", "status", "created_at"),
    )
    _create_index_if_not_exists(
        "ix_vm_requests_status_created",
        "vm_requests",
        ("status", "created_at"),
    )
    _create_index_if_not_exists(
        "ix_vm_requests_schedule",
        "vm_requests",
        ("status", "start_at", "end_at"),
    )
    _create_index_if_not_exists(
        "ix_vm_requests_gpu_window",
        "vm_requests",
        ("gpu_mapping_id", "start_at", "end_at"),
    )

    _create_index_if_not_exists("ix_audit_logs_created_at", "audit_logs", ("created_at",))
    _create_index_if_not_exists(
        "ix_audit_logs_user_created",
        "audit_logs",
        ("user_id", "created_at"),
    )
    _create_index_if_not_exists(
        "ix_audit_logs_action_created",
        "audit_logs",
        ("action", "created_at"),
    )
    _create_index_if_not_exists(
        "ix_audit_logs_vmid_created",
        "audit_logs",
        ("vmid", "created_at"),
    )

    _create_index_if_not_exists(
        "ix_ai_api_credentials_api_key_prefix",
        "ai_api_credentials",
        ("api_key_prefix",),
    )
    _create_index_if_not_exists(
        "ix_ai_api_credentials_user_id",
        "ai_api_credentials",
        ("user_id",),
    )
    _create_index_if_not_exists(
        "ix_ai_api_credentials_request_id",
        "ai_api_credentials",
        ("request_id",),
    )
    _create_index_if_not_exists(
        "ix_ai_api_credentials_user_revoked",
        "ai_api_credentials",
        ("user_id", "revoked_at"),
    )
    _create_index_if_not_exists("ix_ai_api_requests_user_id", "ai_api_requests", ("user_id",))
    _create_index_if_not_exists(
        "ix_ai_api_requests_reviewer_id",
        "ai_api_requests",
        ("reviewer_id",),
    )
    _create_index_if_not_exists(
        "ix_ai_api_requests_user_status_created",
        "ai_api_requests",
        ("user_id", "status", "created_at"),
    )
    _create_index_if_not_exists(
        "ix_ai_api_requests_status_created",
        "ai_api_requests",
        ("status", "created_at"),
    )
    _create_index_if_not_exists(
        "ix_ai_usage_user_created",
        "ai_api_usage",
        ("user_id", "created_at"),
    )
    _create_index_if_not_exists(
        "ix_ai_usage_model_created",
        "ai_api_usage",
        ("model_name", "created_at"),
    )
    _create_index_if_not_exists(
        "ix_ai_usage_status_created",
        "ai_api_usage",
        ("status", "created_at"),
    )
    _create_index_if_not_exists(
        "ix_ai_template_call_logs_user_created",
        "ai_template_call_logs",
        ("user_id", "created_at"),
    )
    _create_index_if_not_exists(
        "ix_ai_template_call_logs_status_created",
        "ai_template_call_logs",
        ("status", "created_at"),
    )
    _create_index_if_not_exists(
        "ix_ai_template_call_logs_call_type_created",
        "ai_template_call_logs",
        ("call_type", "created_at"),
    )

    op.create_foreign_key(
        "fk_resources_request_id",
        "resources",
        "vm_requests",
        ["request_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_spec_change_requests_resource_vmid",
        "spec_change_requests",
        "resources",
        ["resource_vmid"],
        ["vmid"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_deletion_requests_resource_vmid",
        "deletion_requests",
        "resources",
        ["resource_vmid"],
        ["vmid"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_ip_allocation_resource_vmid",
        "ip_allocation",
        "resources",
        ["resource_vmid"],
        ["vmid"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_script_deploy_logs_resource_vmid",
        "script_deploy_logs",
        "resources",
        ["resource_vmid"],
        ["vmid"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_nat_rule_resource_vmid",
        "nat_rule",
        "resources",
        ["resource_vmid"],
        ["vmid"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_reverse_proxy_rule_resource_vmid",
        "reverse_proxy_rule",
        "resources",
        ["resource_vmid"],
        ["vmid"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_tunnel_proxies_resource_vmid",
        "tunnel_proxies",
        "resources",
        ["resource_vmid"],
        ["vmid"],
        ondelete="CASCADE",
    )

    op.create_unique_constraint("uq_proxmox_nodes_name", "proxmox_nodes", ["name"])
    op.create_unique_constraint(
        "uq_proxmox_storages_node_storage",
        "proxmox_storages",
        ["node_name", "storage"],
    )
    op.create_unique_constraint(
        "uq_nat_rule_host_port_protocol",
        "nat_rule",
        ["ssh_host", "external_port", "protocol"],
    )
    op.create_unique_constraint(
        "uq_tunnel_proxies_vmid_service",
        "tunnel_proxies",
        ["vmid", "service"],
    )
    op.create_unique_constraint("uq_group_owner_name", "group", ["owner_id", "name"])
    op.create_unique_constraint(
        "uq_batch_tasks_job_user",
        "batch_provision_tasks",
        ["job_id", "user_id"],
    )


def downgrade():
    op.drop_constraint("uq_batch_tasks_job_user", "batch_provision_tasks", type_="unique")
    op.drop_constraint("uq_group_owner_name", "group", type_="unique")
    op.drop_constraint("uq_tunnel_proxies_vmid_service", "tunnel_proxies", type_="unique")
    op.drop_constraint("uq_nat_rule_host_port_protocol", "nat_rule", type_="unique")
    op.drop_constraint(
        "uq_proxmox_storages_node_storage",
        "proxmox_storages",
        type_="unique",
    )
    op.drop_constraint("uq_proxmox_nodes_name", "proxmox_nodes", type_="unique")

    for constraint_name, table_name in (
        ("fk_tunnel_proxies_resource_vmid", "tunnel_proxies"),
        ("fk_reverse_proxy_rule_resource_vmid", "reverse_proxy_rule"),
        ("fk_nat_rule_resource_vmid", "nat_rule"),
        ("fk_script_deploy_logs_resource_vmid", "script_deploy_logs"),
        ("fk_ip_allocation_resource_vmid", "ip_allocation"),
        ("fk_deletion_requests_resource_vmid", "deletion_requests"),
        ("fk_spec_change_requests_resource_vmid", "spec_change_requests"),
        ("fk_resources_request_id", "resources"),
    ):
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")

    for index_name in (
        "ix_ai_template_call_logs_call_type_created",
        "ix_ai_template_call_logs_status_created",
        "ix_ai_template_call_logs_user_created",
        "ix_ai_usage_status_created",
        "ix_ai_usage_model_created",
        "ix_ai_usage_user_created",
        "ix_ai_api_requests_status_created",
        "ix_ai_api_requests_user_status_created",
        "ix_ai_api_requests_reviewer_id",
        "ix_ai_api_requests_user_id",
        "ix_ai_api_credentials_user_revoked",
        "ix_ai_api_credentials_request_id",
        "ix_ai_api_credentials_user_id",
        "ix_ai_api_credentials_api_key_prefix",
        "ix_audit_logs_vmid_created",
        "ix_audit_logs_action_created",
        "ix_audit_logs_user_created",
        "ix_audit_logs_created_at",
        "ix_vm_requests_gpu_window",
        "ix_vm_requests_schedule",
        "ix_vm_requests_status_created",
        "ix_vm_requests_user_status_created",
        "ix_vm_requests_vmid",
        "ix_vm_requests_user_id",
        "ix_resource_networks_ip_address",
        "ix_resource_networks_resource_vmid",
        "ix_script_deploy_logs_resource_vmid",
        "ix_ip_allocation_resource_vmid",
        "ix_tunnel_proxies_resource_vmid",
        "ix_reverse_proxy_rule_resource_vmid",
        "ix_nat_rule_resource_vmid",
        "ix_deletion_requests_resource_vmid",
        "ix_spec_change_requests_resource_vmid",
        "ix_resources_auto_stop_at",
        "ix_resources_user_created",
        "ix_resources_user_id",
        "ix_resources_request_id",
    ):
        _drop_index_if_exists(index_name)

    op.drop_table("resource_networks")

    op.drop_column("script_deploy_logs", "resource_vmid")
    op.drop_column("ip_allocation", "resource_vmid")
    op.drop_column("tunnel_proxies", "resource_vmid")
    op.drop_column("reverse_proxy_rule", "resource_vmid")
    op.drop_column("nat_rule", "resource_vmid")
    op.drop_column("deletion_requests", "resource_vmid")
    op.drop_column("spec_change_requests", "resource_vmid")
    op.drop_column("resources", "request_id")
