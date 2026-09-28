"""allow google as a user authentication source

Revision ID: gauth01_google_auth_source
Revises: vlan01_subnet_vlan_tag
Create Date: 2026-09-29
"""

from alembic import op

revision = "gauth01_google_auth_source"
down_revision = "vlan01_subnet_vlan_tag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_user_auth_source", "user", type_="check")
    op.create_check_constraint(
        "ck_user_auth_source",
        "user",
        "auth_source IN ('local', 'google', 'ldap')",
    )


def downgrade() -> None:
    # Preserve account usability when downgrading to a version that does not
    # understand the Google source marker.
    op.drop_constraint("ck_user_auth_source", "user", type_="check")
    op.execute("UPDATE \"user\" SET auth_source = 'local' WHERE auth_source = 'google'")
    op.create_check_constraint(
        "ck_user_auth_source",
        "user",
        "auth_source IN ('local', 'ldap')",
    )
