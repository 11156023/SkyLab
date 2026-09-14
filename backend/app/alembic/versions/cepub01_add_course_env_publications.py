"""Per-student "internet → machine" declarations on a course environment version.

Revision ID: cepub01_env_publications
Revises: mrg02_merge_gov_tjsum_heads
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "cepub01_env_publications"
down_revision = "mrg02_merge_gov_tjsum_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_environment_publications",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("node_key", sa.String(length=80), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="domain"),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("protocol", sa.String(length=16), nullable=False, server_default="tcp"),
        sa.Column("hostname_prefix", sa.String(length=120), nullable=True),
        sa.Column("zone_id", sa.String(length=64), nullable=True),
        sa.Column(
            "enable_https", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["course_environment_versions.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "version_id",
            "node_key",
            "port",
            "protocol",
            name="uq_course_environment_publication",
        ),
    )
    op.create_index(
        "ix_course_environment_publications_version_id",
        "course_environment_publications",
        ["version_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_course_environment_publications_version_id",
        table_name="course_environment_publications",
    )
    op.drop_table("course_environment_publications")
