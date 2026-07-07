"""add course lab tables and governance fields

Revision ID: crs01_course_lab
Revises: e01_teaching
Create Date: 2026-07-05 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "crs01_course_lab"
down_revision = "e01_teaching"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "governance_config",
        sa.Column(
            "course_ttl_hours",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
    )
    op.add_column(
        "governance_config",
        sa.Column(
            "course_max_active_per_user",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )

    path_status = sa.Enum("draft", "published", name="coursepathstatus")
    difficulty = sa.Enum("easy", "medium", "hard", name="coursedifficulty")
    question_type = sa.Enum("flag", "no_answer", name="coursequestiontype")

    op.create_table(
        "course_paths",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("status", path_status, nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_course_paths_status"), "course_paths", ["status"], unique=False
    )

    op.create_table(
        "course_rooms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("path_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("difficulty", difficulty, nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("template_id", sa.Uuid(), nullable=True),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["path_id"], ["course_paths.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["template_id"], ["vm_templates.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_course_rooms_path_id"), "course_rooms", ["path_id"], unique=False
    )

    op.create_table(
        "course_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["room_id"], ["course_rooms.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_course_tasks_room_id"), "course_tasks", ["room_id"], unique=False
    )

    op.create_table(
        "course_questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("prompt", sa.String(length=1000), nullable=False),
        sa.Column("question_type", question_type, nullable=False),
        sa.Column("flag_hash", sa.String(length=64), nullable=True),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["course_tasks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_course_questions_task_id"),
        "course_questions",
        ["task_id"],
        unique=False,
    )

    op.create_table(
        "user_course_progress",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["question_id"], ["course_questions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "question_id", name="uq_user_course_progress"),
    )
    op.create_index(
        op.f("ix_user_course_progress_user_id"),
        "user_course_progress",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_course_progress_question_id"),
        "user_course_progress",
        ["question_id"],
        unique=False,
    )

    op.create_table(
        "course_deployments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("vm_request_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["room_id"], ["course_rooms.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["vm_request_id"], ["vm_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_course_deployments_room_id"),
        "course_deployments",
        ["room_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_course_deployments_vm_request_id"),
        "course_deployments",
        ["vm_request_id"],
        unique=False,
    )
    op.create_index(
        "ix_course_deployments_user_expires",
        "course_deployments",
        ["user_id", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("course_deployments")
    op.drop_table("user_course_progress")
    op.drop_table("course_questions")
    op.drop_table("course_tasks")
    op.drop_table("course_rooms")
    op.drop_table("course_paths")
    sa.Enum(name="coursequestiontype").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="coursedifficulty").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="coursepathstatus").drop(op.get_bind(), checkfirst=True)
    op.drop_column("governance_config", "course_max_active_per_user")
    op.drop_column("governance_config", "course_ttl_hours")
