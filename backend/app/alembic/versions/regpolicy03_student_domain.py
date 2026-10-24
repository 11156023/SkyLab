"""Student e-mail domain for roster CSV files, set by the administrator.

Revision ID: regpolicy03_student_domain
Revises: usrgrp01_user_groups
"""

import sqlalchemy as sa
from alembic import op

revision = "regpolicy03_student_domain"
down_revision = "usrgrp01_user_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "registration_policy",
        sa.Column("student_email_domain", sa.String(length=253), nullable=True),
    )
    # 升級前名單裡的學號一律補 @ntub.edu.tw（寫死在程式裡）；已存過註冊設定的
    # 部署沿用這個行為，管理員可在註冊設定改掉。全新安裝沒有這一列，要自己設定。
    op.execute("UPDATE registration_policy SET student_email_domain = 'ntub.edu.tw'")


def downgrade() -> None:
    op.drop_column("registration_policy", "student_email_domain")
