"""User group names are unique regardless of case.

usrgrp01 created a plain UNIQUE(name), which is case sensitive in PostgreSQL, while the
service treats ClassA and classa as the same name. Two concurrent requests could both pass
the service's check and both insert; a unique index on lower(name) stops the second one.

Revision ID: usrgrp02_name_ci
Revises: regpolicy03_student_domain
"""

import sqlalchemy as sa
from alembic import op

revision = "usrgrp02_name_ci"
down_revision = "regpolicy03_student_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT lower(name) FROM user_groups GROUP BY lower(name) HAVING count(*) > 1"
            )
        )
        .scalars()
        .all()
    )
    if duplicates:
        # 不替管理員決定留哪一個：列出來，改名或合併後再升級
        raise RuntimeError(
            "user_groups has names that differ only in case; rename them first: "
            + ", ".join(duplicates)
        )
    op.drop_constraint("user_groups_name_key", "user_groups", type_="unique")
    op.create_index(
        "uq_user_groups_name_lower",
        "user_groups",
        [sa.text("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_user_groups_name_lower", table_name="user_groups")
    op.create_unique_constraint("user_groups_name_key", "user_groups", ["name"])
