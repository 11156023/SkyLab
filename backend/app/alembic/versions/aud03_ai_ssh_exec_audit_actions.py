"""Add AI SSH execution labels to the auditaction enum type.

``ai_ssh_exec`` / ``ai_ssh_exec_blocked`` record every command the AI
assistant runs over SSH on a guest — executed, user-confirmed, or stopped by
the command guard. Without the enum labels ``audit_service.log_action``
raises ``ValueError: ... is not a valid AuditAction``.

PostgreSQL cannot drop enum values, so downgrade is a no-op.

Revision ID: aud03_ai_ssh_exec_actions
Revises: vmreq02_provisioning_started_at
Create Date: 2026-09-22
"""

from __future__ import annotations

from alembic import op

revision = "aud03_ai_ssh_exec_actions"
down_revision = "vmreq02_provisioning_started_at"
branch_labels = None
depends_on = None

_ENUM = "auditaction"

VALUES = (
    "ai_ssh_exec",
    "ai_ssh_exec_blocked",
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
