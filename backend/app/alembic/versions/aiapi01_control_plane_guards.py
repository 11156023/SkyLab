"""Add the AI API active-credential invariant.

Revision ID: aiapi01_control_guards
Revises: bkup01_machine_backup
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "aiapi01_control_guards"
down_revision = "bkup01_machine_backup"
branch_labels = None
depends_on = None

_INDEX = "uq_ai_api_credentials_active_request"


def _index_names() -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("ai_api_credentials")
        if index.get("name")
    }


def upgrade() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT request_id FROM ai_api_credentials "
                "WHERE revoked_at IS NULL "
                "GROUP BY request_id HAVING count(*) > 1"
            )
        )
        .fetchall()
    )
    if duplicates:
        request_ids = ", ".join(str(row[0]) for row in duplicates)
        raise RuntimeError(
            "ai_api_credentials contains multiple active keys for request_id: "
            f"{request_ids}. Revoke the unintended keys explicitly before retrying."
        )

    if _INDEX not in _index_names():
        op.create_index(
            _INDEX,
            "ai_api_credentials",
            ["request_id"],
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        )


def downgrade() -> None:
    if _INDEX in _index_names():
        op.drop_index(_INDEX, table_name="ai_api_credentials")
