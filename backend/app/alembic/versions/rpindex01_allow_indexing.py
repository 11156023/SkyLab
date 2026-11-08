"""Machine sites published through the gateway are not indexed unless the owner allows it.

Revision ID: rpindex01_allow_indexing
Revises: usrgrp02_name_ci
"""

import sqlalchemy as sa
from alembic import op

revision = "rpindex01_allow_indexing"
down_revision = "usrgrp02_name_ci"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 既有的網址一律從不收錄開始：下次同步 Gateway 後回應會帶 noindex，擁有者要自己打開
    op.add_column(
        "reverse_proxy_rule",
        sa.Column(
            "allow_indexing", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("reverse_proxy_rule", "allow_indexing")
