"""老師掛在學習環境上的說明文件。"""

import sqlalchemy as sa
from alembic import op

revision = "cefile01_environment_files"
down_revision = "cedraft01_environment_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_environment_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["course_environments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["uploaded_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_course_environment_file_storage"),
    )
    op.create_index(
        "ix_course_environment_files_environment_id",
        "course_environment_files",
        ["environment_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_course_environment_files_environment_id",
        table_name="course_environment_files",
    )
    op.drop_table("course_environment_files")
