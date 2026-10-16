"""Teacher's weekly reply to each student (one per student per week, overwritten on edit).

Revision ID: tjreply01
Revises: tjweek01
"""

import sqlalchemy as sa
from alembic import op

revision = "tjreply01"
down_revision = "tjweek01"
branch_labels = None
depends_on = None

_TABLE = "teacher_judge_student_replies"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["teacher_judge_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["student_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "student_id",
            name="uq_teacher_judge_student_reply_session_student",
        ),
    )
    op.create_index(
        "ix_teacher_judge_student_replies_session_id", _TABLE, ["session_id"]
    )
    op.create_index(
        "ix_teacher_judge_student_replies_student_id", _TABLE, ["student_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_teacher_judge_student_replies_student_id", table_name=_TABLE)
    op.drop_index("ix_teacher_judge_student_replies_session_id", table_name=_TABLE)
    op.drop_table(_TABLE)
