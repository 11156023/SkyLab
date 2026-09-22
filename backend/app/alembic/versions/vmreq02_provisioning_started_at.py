"""vm_requests.provisioning_started_at：running 狀態的起始時間。

以前 provisioning_status=running 沒有任何逾時回收：clone 中容器重啟或
寫回結果失敗，申請單就永遠卡在 running（retry 也拒絕）。有了起始時間，
排程器可以把超時的 running 視為孤兒重新接手。

Revision ID: vmreq02_provisioning_started_at
Revises: mrg07_merge_cexp02_mrg06
Create Date: 2026-09-22
"""

import sqlalchemy as sa
from alembic import op

revision = "vmreq02_provisioning_started_at"
down_revision = "mrg07_merge_cexp02_mrg06"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "vm_requests",
        sa.Column("provisioning_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("vm_requests", "provisioning_started_at")
