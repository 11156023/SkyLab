"""Remove the retired desktop tunnel proxy registry.

Revision ID: tnrm01_drop_tunnel_proxies
Revises: mrg03_merge_osid_tjmm
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "tnrm01_drop_tunnel_proxies"
down_revision = "mrg03_merge_osid_tjmm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS tunnel_proxies"))


def downgrade() -> None:
    op.create_table(
        "tunnel_proxies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("vmid", sa.Integer(), nullable=False),
        sa.Column("resource_vmid", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("service", sa.String(length=10), nullable=False),
        sa.Column("internal_port", sa.Integer(), nullable=False),
        sa.Column("secret_key", sa.String(length=64), nullable=False),
        sa.Column("proxy_name", sa.String(length=100), nullable=False),
        sa.Column("visitor_port", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["resource_vmid"], ["resources.vmid"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["vmid"], ["resources.vmid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proxy_name", name="uq_tunnel_proxies_proxy_name"),
        sa.UniqueConstraint(
            "vmid", "service", name="uq_tunnel_proxies_vmid_service"
        ),
        sa.UniqueConstraint("visitor_port", name="uq_tunnel_proxies_visitor_port"),
    )
    op.create_index(
        op.f("ix_tunnel_proxies_resource_vmid"),
        "tunnel_proxies",
        ["resource_vmid"],
    )
    op.create_index(
        op.f("ix_tunnel_proxies_user_id"), "tunnel_proxies", ["user_id"]
    )
    op.create_index(op.f("ix_tunnel_proxies_vmid"), "tunnel_proxies", ["vmid"])
