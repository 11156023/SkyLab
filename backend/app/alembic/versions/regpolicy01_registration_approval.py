"""Add registration policy and explicit registration review state.

Revision ID: regpolicy01
Revises: pwhash01_login_password_hash
"""

import sqlalchemy as sa
from alembic import op

revision = "regpolicy01"
down_revision = "pwhash01_login_password_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registration_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("signup_enabled", sa.Boolean(), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
    )
    op.add_column(
        "user",
        sa.Column(
            "registration_status",
            sa.String(length=20),
            nullable=False,
            server_default="approved",
        ),
    )
    op.create_check_constraint(
        "ck_user_registration_status",
        "user",
        "registration_status IN ('approved', 'pending_email', 'pending_review', 'rejected')",
    )
    # Keep the default so older application instances can still create accounts.


def downgrade() -> None:
    op.drop_constraint("ck_user_registration_status", "user", type_="check")
    op.drop_column("user", "registration_status")
    op.drop_table("registration_policy")
