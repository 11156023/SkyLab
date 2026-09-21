"""Add resources.login_password_pending_encrypted.

An LXC cloned from a template can only get its root password through
``pct exec`` after it boots. Class machines with a schedule are created
stopped, so the generated password used to be discarded: the guest kept the
template's password and the credentials card showed "not recorded". The
pending column keeps the generated password until the first managed start
writes it into the guest.

Revision ID: pwpend01_login_pw_pending
Revises: aud02_course_practice_actions
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "pwpend01_login_pw_pending"
down_revision = "aud02_course_practice_actions"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if _has_column("resources", "login_password_pending_encrypted"):
        return
    op.add_column(
        "resources",
        sa.Column("login_password_pending_encrypted", sa.String(), nullable=True),
    )


def downgrade() -> None:
    if _has_column("resources", "login_password_pending_encrypted"):
        op.drop_column("resources", "login_password_pending_encrypted")
