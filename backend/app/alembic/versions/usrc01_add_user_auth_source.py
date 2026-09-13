"""add auth_source to user

Revision ID: usrc01_add_user_auth_source
Revises: wpush01_web_push_tables
Create Date: 2026-09-12 00:00:00.000000

帳號來源標記："local"（本地密碼）| "ldap"（由 LDAP 目錄管理）。
對 LDAP 帳號設定本地密碼沒有意義，還會造成「明明改了卻登不進去」的
困惑（稽核 #9）；有了標記後，使用者管理頁能鎖住 LDAP 帳號的密碼欄位、
後端能拒絕對 LDAP 帳號改本地密碼。

既有資料無從辨識來源，一律回填 "local"；LDAP 登入成功時由
ldap_auth_service 自癒標記為 "ldap"。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "usrc01_add_user_auth_source"
down_revision = "wpush01_web_push_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "auth_source",
            sa.String(length=20),
            nullable=False,
            server_default="local",
        ),
    )


def downgrade() -> None:
    op.drop_column("user", "auth_source")
