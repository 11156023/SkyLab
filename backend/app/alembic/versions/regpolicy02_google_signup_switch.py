"""Separate switch for automatic account creation on first Google login.

Revision ID: regpolicy02_google_signup
Revises: cpuovc01
"""

import sqlalchemy as sa
from alembic import op

revision = "regpolicy02_google_signup"
down_revision = "cpuovc01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "registration_policy",
        sa.Column(
            "google_signup_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    # 既有設定沿用升級前的行為：Google 首次登入自動建帳原本跟著「允許自行註冊」走。
    op.execute("UPDATE registration_policy SET google_signup_enabled = signup_enabled")
    # Keep the server default so older application instances can still insert rows.


def downgrade() -> None:
    op.drop_column("registration_policy", "google_signup_enabled")
