"""platform_entry_config 記下 SkyLab 在 Cloudflare 建的平台網域 DNS 紀錄

Revision ID: pentrydns01_platform_entry_dns
Revises: gwcert01_manual_certificate
Create Date: 2026-10-05 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "pentrydns01_platform_entry_dns"
down_revision = "gwcert01_manual_certificate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_entry_config",
        sa.Column("dns_zone_id", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "platform_entry_config",
        sa.Column("dns_record_id", sa.String(length=64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("platform_entry_config", "dns_record_id")
    op.drop_column("platform_entry_config", "dns_zone_id")
