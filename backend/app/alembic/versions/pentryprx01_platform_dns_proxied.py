"""platform_entry_config 新增 dns_proxied：平台網域可選擇經 Cloudflare 代理（橘色雲）

Revision ID: pentryprx01_dns_proxied
Revises: norm3nf01_normalize_schema
Create Date: 2026-10-05 12:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "pentryprx01_dns_proxied"
down_revision = "norm3nf01_normalize_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_entry_config",
        sa.Column(
            "dns_proxied", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("platform_entry_config", "dns_proxied")
