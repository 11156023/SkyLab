"""Sync the auditaction enum type with the AuditAction Python enum.

Two kinds of drift are closed here, both idempotently:

- ``login_ldap_*`` (gov04) and ``mining_*`` (gov05) were added to
  ``AuditAction`` without an ``ADD VALUE`` migration, so writing those rows
  fails on any alembic-managed database.
- ``group_*`` and ``cloudflare_zone_activation_check`` are retired labels that
  still have rows in ``audit_logs``; they were restored to ``AuditAction`` so
  those rows stay readable, and are added here so freshly built databases
  match the model as well.

PostgreSQL cannot drop enum values, so downgrade is a no-op.

Revision ID: aud01_sync_auditaction_labels
Revises: mrg02_merge_gov_tjsum_heads
Create Date: 2026-09-07
"""

from __future__ import annotations

from alembic import op

revision = "aud01_sync_auditaction_labels"
down_revision = "mrg02_merge_gov_tjsum_heads"
branch_labels = None
depends_on = None

_ENUM = "auditaction"

VALUES = (
    # AuditAction members that never got an ADD VALUE migration
    "login_ldap_success",
    "login_ldap_failed",
    "mining_detected",
    "mining_suspend",
    "mining_ban",
    "mining_dismiss",
    "mining_exempt_change",
    # Retired labels restored to AuditAction so historical rows stay readable
    "group_create",
    "group_delete",
    "group_member_add",
    "group_member_remove",
    "cloudflare_zone_activation_check",
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
