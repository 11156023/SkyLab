"""Add course / quick-practice labels to the auditaction enum type.

``course_lab_deploy``, ``course_answer_submit`` and
``quick_practice_machine_create`` were passed to ``audit_service.log_action``
as string literals but never added to ``AuditAction`` or the PostgreSQL enum,
so launching a quick practice, deploying a course lab or submitting a course
answer failed with ``ValueError: ... is not a valid AuditAction``.

PostgreSQL cannot drop enum values, so downgrade is a no-op.

Revision ID: aud02_course_practice_actions
Revises: mrg05_merge_pwnull_mm
Create Date: 2026-09-21
"""

from __future__ import annotations

from alembic import op

revision = "aud02_course_practice_actions"
down_revision = "mrg05_merge_pwnull_mm"
branch_labels = None
depends_on = None

_ENUM = "auditaction"

VALUES = (
    "course_lab_deploy",
    "course_answer_submit",
    "quick_practice_machine_create",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        for value in VALUES:
            op.execute(f"ALTER TYPE {_ENUM} ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # PostgreSQL has no DROP VALUE; leaving the labels in place is harmless.
    pass
