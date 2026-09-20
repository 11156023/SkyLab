"""Add resources.guest_os structured Guest OS identity.

Revision ID: osid01_resource_guest_os
Revises: (tjtn01_teacher_judge_node_scope, vmreqpw02_password_not_null)
Create Date: 2026-09-19

同時是 merge revision：上游目前有兩個 head，本 revision 把它們收斂，
之後 ``alembic upgrade head`` 恢復單頭可用。
"""

import sqlalchemy as sa
from alembic import op

revision = "osid01_resource_guest_os"
down_revision = ("tjtn01_teacher_judge_node_scope", "vmreqpw02_password_not_null")
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade():
    # 冪等：重播時欄位可能已存在
    if _has_column("resources", "guest_os"):
        return
    op.add_column(
        "resources",
        sa.Column("guest_os", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column("resources", "guest_os")
