"""Merge the tunnel-cleanup and current upstream heads.

Revision ID: mrg06_merge_tunnel_upstream
Revises: tnrm01_drop_tunnel_proxies, clrm01_drop_course_lab_deploy
Create Date: 2026-09-21
"""

from __future__ import annotations

revision = "mrg06_merge_tunnel_upstream"
down_revision = (
    "tnrm01_drop_tunnel_proxies",
    "clrm01_drop_course_lab_deploy",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
