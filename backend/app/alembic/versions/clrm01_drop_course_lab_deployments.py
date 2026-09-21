"""Drop the Course Lab one-click deployment schema.

Course Lab let a student deploy a machine from the template bound to a course
room. Its only frontend entry (CourseRoomPage) was removed on 2026-09-02 and it
overlaps with quick practice, so the backend is removed as well. The tables
were never used: no deployments, no room ever had a template bound.

The rest of the course domain (paths, rooms, tasks, questions, progress) stays.

Revision ID: clrm01_drop_course_lab_deploy
Revises: pwpend01_login_pw_pending
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "clrm01_drop_course_lab_deploy"
down_revision = "pwpend01_login_pw_pending"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    inspector = _inspector()
    if "course_deployments" in inspector.get_table_names():
        op.drop_table("course_deployments")

    if "course_rooms" in inspector.get_table_names() and "template_id" in {
        c["name"] for c in inspector.get_columns("course_rooms")
    }:
        for fk in inspector.get_foreign_keys("course_rooms"):
            if fk.get("constrained_columns") == ["template_id"] and fk.get("name"):
                op.drop_constraint(fk["name"], "course_rooms", type_="foreignkey")
        for index in inspector.get_indexes("course_rooms"):
            if index.get("column_names") == ["template_id"] and index.get("name"):
                op.drop_index(index["name"], table_name="course_rooms")
        op.drop_column("course_rooms", "template_id")


def downgrade() -> None:
    inspector = _inspector()
    if "template_id" not in {c["name"] for c in inspector.get_columns("course_rooms")}:
        op.add_column(
            "course_rooms",
            sa.Column(
                "template_id",
                sa.Uuid(),
                sa.ForeignKey("vm_templates.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    if "course_deployments" not in inspector.get_table_names():
        op.create_table(
            "course_deployments",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "room_id",
                sa.Uuid(),
                sa.ForeignKey("course_rooms.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("user.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "vm_request_id",
                sa.Uuid(),
                sa.ForeignKey("vm_requests.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_course_deployments_user_expires",
            "course_deployments",
            ["user_id", "expires_at"],
        )
