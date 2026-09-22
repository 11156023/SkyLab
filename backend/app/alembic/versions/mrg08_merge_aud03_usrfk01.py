"""Merge the AI SSH audit-action and user FK ondelete heads.

Revision ID: mrg08_merge_aud03_usrfk01
Revises: aud03_ai_ssh_exec_actions, usrfk01_user_fk_ondelete
Create Date: 2026-09-22
"""

from __future__ import annotations

revision = "mrg08_merge_aud03_usrfk01"
down_revision = (
    "aud03_ai_ssh_exec_actions",
    "usrfk01_user_fk_ondelete",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
