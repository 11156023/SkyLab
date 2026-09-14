"""Add Teacher Judge chat message attachments.

Revision ID: tjatt01_msg_attachments
Revises: clsmerge01_current_heads
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "tjatt01_msg_attachments"
down_revision = "clsmerge01_current_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teacher_judge_session_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=150), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ready", "failed", name="teacherjudgeattachmentstatus"),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["teacher_judge_session_messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["teacher_judge_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["uploaded_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(
        "ix_teacher_judge_session_attachments_session_id",
        "teacher_judge_session_attachments",
        ["session_id"],
    )
    op.create_index(
        "ix_teacher_judge_session_attachments_message_id",
        "teacher_judge_session_attachments",
        ["message_id"],
    )
    op.create_index(
        "ix_teacher_judge_session_attachments_uploaded_by",
        "teacher_judge_session_attachments",
        ["uploaded_by"],
    )
    op.create_index(
        "ix_teacher_judge_session_attachments_file_hash",
        "teacher_judge_session_attachments",
        ["file_hash"],
    )
    op.create_index(
        "ix_teacher_judge_session_attachments_status",
        "teacher_judge_session_attachments",
        ["status"],
    )
    op.create_index(
        "ix_teacher_judge_session_attachments_created_at",
        "teacher_judge_session_attachments",
        ["created_at"],
    )
    op.create_index(
        "ix_teacher_judge_session_attachments_session_created",
        "teacher_judge_session_attachments",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_teacher_judge_session_attachments_session_created",
        table_name="teacher_judge_session_attachments",
    )
    for name in (
        "ix_teacher_judge_session_attachments_created_at",
        "ix_teacher_judge_session_attachments_status",
        "ix_teacher_judge_session_attachments_file_hash",
        "ix_teacher_judge_session_attachments_uploaded_by",
        "ix_teacher_judge_session_attachments_message_id",
        "ix_teacher_judge_session_attachments_session_id",
    ):
        op.drop_index(name, table_name="teacher_judge_session_attachments")
    op.drop_table("teacher_judge_session_attachments")
    sa.Enum(name="teacherjudgeattachmentstatus").drop(op.get_bind(), checkfirst=True)
