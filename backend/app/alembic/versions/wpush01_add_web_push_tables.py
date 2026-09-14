"""Add Web Push tables: browser subscriptions and the VAPID key singleton.

``push_subscriptions`` holds one row per browser (Service Worker) that
subscribed through ``PushManager.subscribe()``; ``endpoint`` is globally
unique so the same browser re-subscribing under another account simply moves
ownership. ``web_push_config`` is a single-row table (id = 1) that stores the
VAPID key pair generated on first use; rotating it invalidates every existing
subscription, which is why it lives in the database instead of ``.env``.

Revision ID: wpush01_web_push_tables
Revises: mrg03_merge_aud_sched_heads
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "wpush01_web_push_tables"
down_revision = "mrg03_merge_aud_sched_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "web_push_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vapid_private_key_pem", sa.Text(), nullable=False),
        sa.Column(
            "vapid_public_key",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "subject", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column(
            "p256dh", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False
        ),
        sa.Column("auth", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column(
            "user_agent", sqlmodel.sql.sqltypes.AutoString(length=512), nullable=True
        ),
        sa.Column(
            "language", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False
        ),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint"),
    )
    op.create_index(
        op.f("ix_push_subscriptions_user_id"),
        "push_subscriptions",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_push_subscriptions_user_id"), table_name="push_subscriptions"
    )
    op.drop_table("push_subscriptions")
    op.drop_table("web_push_config")
