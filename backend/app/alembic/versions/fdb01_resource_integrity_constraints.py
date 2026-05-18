"""add resource integrity constraints

Revision ID: fdb01_resource_integrity
Revises: ee01_cleanup_rels
Create Date: 2026-05-18 00:00:00.000000

"""

from alembic import op

revision = "fdb01_resource_integrity"
down_revision = "ee01_cleanup_rels"
branch_labels = None
depends_on = None


def _create_constraint_if_missing(name: str, ddl: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = '{name}'
            ) THEN
                {ddl}
            END IF;
        END $$;
        """
    )


def _drop_constraint_if_exists(table: str, name: str) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")


def upgrade():
    # Remove invalid duplicates before adding uniqueness constraints.  The newest
    # row wins because these tables represent current runtime routing state.
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY ssh_host, external_port, protocol
                    ORDER BY created_at DESC, id DESC
                ) AS rn
            FROM nat_rule
        )
        DELETE FROM nat_rule
        USING ranked
        WHERE nat_rule.id = ranked.id
          AND ranked.rn > 1
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY vmid, service
                    ORDER BY created_at DESC, id DESC
                ) AS rn
            FROM tunnel_proxies
        )
        DELETE FROM tunnel_proxies
        USING ranked
        WHERE tunnel_proxies.id = ranked.id
          AND ranked.rn > 1
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY visitor_port
                    ORDER BY created_at DESC, id DESC
                ) AS rn
            FROM tunnel_proxies
        )
        DELETE FROM tunnel_proxies
        USING ranked
        WHERE tunnel_proxies.id = ranked.id
          AND ranked.rn > 1
        """
    )

    # Drop routing rows whose target resource no longer exists.  Keep historical
    # IP allocation rows, but clear their VM reference.
    op.execute(
        """
        DELETE FROM nat_rule
        WHERE NOT EXISTS (
            SELECT 1 FROM resources WHERE resources.vmid = nat_rule.vmid
        )
        """
    )
    op.execute(
        """
        DELETE FROM reverse_proxy_rule
        WHERE NOT EXISTS (
            SELECT 1 FROM resources WHERE resources.vmid = reverse_proxy_rule.vmid
        )
        """
    )
    op.execute(
        """
        DELETE FROM tunnel_proxies
        WHERE NOT EXISTS (
            SELECT 1 FROM resources WHERE resources.vmid = tunnel_proxies.vmid
        )
        """
    )
    _create_constraint_if_missing(
        "fk_nat_rule_vmid_resources",
        """
        ALTER TABLE nat_rule
        ADD CONSTRAINT fk_nat_rule_vmid_resources
        FOREIGN KEY (vmid) REFERENCES resources (vmid) ON DELETE CASCADE;
        """,
    )
    _create_constraint_if_missing(
        "uq_nat_rule_host_external_port_protocol",
        """
        ALTER TABLE nat_rule
        ADD CONSTRAINT uq_nat_rule_host_external_port_protocol
        UNIQUE (ssh_host, external_port, protocol);
        """,
    )

    _create_constraint_if_missing(
        "fk_reverse_proxy_rule_vmid_resources",
        """
        ALTER TABLE reverse_proxy_rule
        ADD CONSTRAINT fk_reverse_proxy_rule_vmid_resources
        FOREIGN KEY (vmid) REFERENCES resources (vmid) ON DELETE CASCADE;
        """,
    )

    _create_constraint_if_missing(
        "fk_tunnel_proxies_vmid_resources",
        """
        ALTER TABLE tunnel_proxies
        ADD CONSTRAINT fk_tunnel_proxies_vmid_resources
        FOREIGN KEY (vmid) REFERENCES resources (vmid) ON DELETE CASCADE;
        """,
    )
    _create_constraint_if_missing(
        "uq_tunnel_proxies_vmid_service",
        """
        ALTER TABLE tunnel_proxies
        ADD CONSTRAINT uq_tunnel_proxies_vmid_service
        UNIQUE (vmid, service);
        """,
    )
    _create_constraint_if_missing(
        "uq_tunnel_proxies_visitor_port",
        """
        ALTER TABLE tunnel_proxies
        ADD CONSTRAINT uq_tunnel_proxies_visitor_port
        UNIQUE (visitor_port);
        """,
    )


def downgrade():
    _drop_constraint_if_exists("tunnel_proxies", "uq_tunnel_proxies_visitor_port")
    _drop_constraint_if_exists("tunnel_proxies", "uq_tunnel_proxies_vmid_service")
    _drop_constraint_if_exists("tunnel_proxies", "fk_tunnel_proxies_vmid_resources")
    _drop_constraint_if_exists(
        "reverse_proxy_rule",
        "fk_reverse_proxy_rule_vmid_resources",
    )
    _drop_constraint_if_exists(
        "nat_rule",
        "uq_nat_rule_host_external_port_protocol",
    )
    _drop_constraint_if_exists("nat_rule", "fk_nat_rule_vmid_resources")
