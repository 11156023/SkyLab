"""gateway_config 加上管理員自備 HTTPS 憑證的路徑（系統不再以 certbot 簽發）

Revision ID: gwcert01_manual_certificate
Revises: bkup01_machine_backup
Create Date: 2026-10-04 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "gwcert01_manual_certificate"
down_revision = "bkup01_machine_backup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gateway_config",
        sa.Column(
            "ssl_certificate_path",
            sa.String(length=512),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "gateway_config",
        sa.Column(
            "ssl_certificate_key_path",
            sa.String(length=512),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("gateway_config", "ssl_certificate_key_path")
    op.drop_column("gateway_config", "ssl_certificate_path")
