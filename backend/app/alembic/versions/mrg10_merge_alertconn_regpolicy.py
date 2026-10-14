"""Merge the alert-connection and registration-approval branches.

Revision ID: mrg10_merge_alertconn_regpolicy
Revises: alertconn01_alert_connection, regpolicy01
Create Date: 2026-10-14
"""

from __future__ import annotations

revision = "mrg10_merge_alertconn_regpolicy"
down_revision = (
    "alertconn01_alert_connection",
    "regpolicy01",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
