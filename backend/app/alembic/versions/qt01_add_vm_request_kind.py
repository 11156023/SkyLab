"""add request_kind to vm_requests

Revision ID: qt01_request_kind
Revises: 59a23c4591c7
Create Date: 2026-05-09 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "qt01_request_kind"
down_revision = "59a23c4591c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    has_request_kind = any(
        column["name"] == "request_kind"
        for column in inspector.get_columns("vm_requests")
    )
    if not has_request_kind:
        op.add_column(
            "vm_requests",
            sa.Column(
                "request_kind",
                sa.String(),
                nullable=False,
                server_default="research",
            ),
        )
    op.alter_column("vm_requests", "request_kind", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    has_request_kind = any(
        column["name"] == "request_kind"
        for column in inspector.get_columns("vm_requests")
    )
    if has_request_kind:
        op.drop_column("vm_requests", "request_kind")
