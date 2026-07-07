"""add ldap config

Revision ID: gov04_ldap_config
Revises: gov03_vm_request_auto
Create Date: 2026-07-04 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision = "gov04_ldap_config"
down_revision = "gov03_vm_request_auto"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ldap_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "server_uri",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column("use_starttls", sa.Boolean(), nullable=False),
        sa.Column(
            "bind_dn", sqlmodel.sql.sqltypes.AutoString(length=512), nullable=False
        ),
        sa.Column(
            "encrypted_bind_password",
            sqlmodel.sql.sqltypes.AutoString(length=2048),
            nullable=False,
        ),
        sa.Column(
            "user_search_base",
            sqlmodel.sql.sqltypes.AutoString(length=512),
            nullable=False,
        ),
        sa.Column(
            "user_filter_template",
            sqlmodel.sql.sqltypes.AutoString(length=512),
            nullable=False,
        ),
        sa.Column(
            "email_attribute",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "name_attribute",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "teacher_group_dn",
            sqlmodel.sql.sqltypes.AutoString(length=512),
            nullable=True,
        ),
        sa.Column(
            "admin_group_dn",
            sqlmodel.sql.sqltypes.AutoString(length=512),
            nullable=True,
        ),
        sa.Column("auto_create_users", sa.Boolean(), nullable=False),
        sa.Column("connect_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("ldap_config")
