"""強制兩步驟驗證改為逐帳號設定：user 表新增 totp_required、移除 auth_policy 全站 singleton。

管理員在「新增使用者／編輯使用者」勾選「強制兩步驟驗證」即寫入 ``user.totp_required``；
舊的全站 ``auth_policy.totp_required`` 若原本為 true，升級時套到所有既有使用者，
語意不變（原本全站強制＝每個人都被要求）。

Revision ID: totp02_user_totp_required
Revises: totp01_user_totp
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "totp02_user_totp_required"
down_revision = "totp01_user_totp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "totp_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # 舊全站政策若已開啟，沿用到每一位既有使用者
    op.execute(
        'UPDATE "user" SET totp_required = true '
        "WHERE EXISTS (SELECT 1 FROM auth_policy WHERE id = 1 AND totp_required = true)"
    )
    op.drop_table("auth_policy")


def downgrade() -> None:
    op.create_table(
        "auth_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "totp_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # 只要有任何一位使用者被要求，就退回成全站強制（downgrade 只能取近似語意）
    op.execute(
        "INSERT INTO auth_policy (id, totp_required, updated_at) "
        'SELECT 1, EXISTS (SELECT 1 FROM "user" WHERE totp_required), now()'
    )
    op.drop_column("user", "totp_required")
