"""Merge the class-exposure cleanup and tunnel-cleanup heads.

Revision ID: mrg07_merge_cexp02_mrg06
Revises: cexp02_drop_class_exposures, mrg06_merge_tunnel_upstream
Create Date: 2026-09-22
"""

from __future__ import annotations

revision = "mrg07_merge_cexp02_mrg06"
down_revision = (
    "cexp02_drop_class_exposures",
    "mrg06_merge_tunnel_upstream",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
