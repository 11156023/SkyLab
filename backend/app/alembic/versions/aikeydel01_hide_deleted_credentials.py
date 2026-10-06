"""Keep deleted AI credentials as backend-only accounting records.

Revision ID: aikeydel01_deleted_credentials
Revises: usrsoft01_soft_delete_users
Create Date: 2026-10-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "aikeydel01_deleted_credentials"
down_revision = "usrsoft01_soft_delete_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ai_api_credentials") as batch:
        batch.add_column(
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_check_constraint(
            "ck_ai_api_credentials_deleted_revoked",
            "deleted_at IS NULL OR revoked_at IS NOT NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_api_credentials") as batch:
        batch.drop_constraint(
            "ck_ai_api_credentials_deleted_revoked", type_="check"
        )
        batch.drop_column("deleted_at")
