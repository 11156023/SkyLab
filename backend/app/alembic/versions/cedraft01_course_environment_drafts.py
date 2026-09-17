"""Persist unfinished course environment editor drafts."""

import sqlalchemy as sa
from alembic import op

revision = "cedraft01_environment_drafts"
down_revision = "cexp01_resource_class_exposures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "course_environment_versions", sa.Column("draft_data", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("course_environment_versions", "draft_data")
