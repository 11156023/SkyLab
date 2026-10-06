"""Merge the deleted-credential and platform-entry DNS branches.

Revision ID: mrg09_merge_aikeydel_pentryprx
Revises: aikeydel01_deleted_credentials, pentryprx01_dns_proxied
Create Date: 2026-10-06
"""

from __future__ import annotations

revision = "mrg09_merge_aikeydel_pentryprx"
down_revision = (
    "aikeydel01_deleted_credentials",
    "pentryprx01_dns_proxied",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
