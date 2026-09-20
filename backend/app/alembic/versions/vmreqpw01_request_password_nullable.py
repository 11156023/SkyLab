"""vm_requests.password 改為可空。

密碼改存 resources.login_password_encrypted（provision 完成即清空申請單副本），
Course Lab 申請單則一開始就是 NULL（沿用範本內烘焙的憑證）。

Revision ID: vmreqpw01_request_password_null
Revises: cexp01_resource_class_exposures
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op

revision = "vmreqpw01_request_password_null"
down_revision = "cexp01_resource_class_exposures"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "vm_requests",
        "password",
        existing_type=sa.String(),
        nullable=True,
    )


def downgrade():
    # 回填空字串再收緊 NOT NULL（無法還原已清空的密碼）
    op.execute("UPDATE vm_requests SET password = '' WHERE password IS NULL")
    op.alter_column(
        "vm_requests",
        "password",
        existing_type=sa.String(),
        nullable=False,
    )
