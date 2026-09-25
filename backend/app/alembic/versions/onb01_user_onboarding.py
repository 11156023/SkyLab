"""user 表新增 onboarding_completed：首次登入引導精靈（語言／外觀／兩步驟驗證）。

新帳號預設 False，第一次登入時前端只顯示引導畫面，走完或略過後由
``POST /users/me/onboarding/complete`` 標為 True。既有帳號在升級時一律標為 True，
避免所有舊使用者在升級後被導去精靈。

Revision ID: onb01_user_onboarding
Revises: obs01_alert_system_scope
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "onb01_user_onboarding"
down_revision = "obs01_alert_system_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "onboarding_completed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # 既有使用者不需要再走首次登入引導
    op.execute('UPDATE "user" SET onboarding_completed = true')


def downgrade() -> None:
    op.drop_column("user", "onboarding_completed")
