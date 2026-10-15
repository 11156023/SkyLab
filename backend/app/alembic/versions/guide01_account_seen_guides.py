"""Persist page guide viewing on the account, independently of guide versions.

Additive and compatible with the old application. Repeated schema preparation
must never reset existing viewing history.
"""

import sqlalchemy as sa
from alembic import op

revision = "guide01_account_seen_guides"
down_revision = "regpolicy01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    context = op.get_context()
    if not context.as_sql:
        columns = sa.inspect(op.get_bind()).get_columns("user")
        existing = next((c for c in columns if c["name"] == "seen_user_guides"), None)
        if existing is not None:
            if not isinstance(existing["type"], sa.JSON) or existing["nullable"]:
                raise RuntimeError(
                    "seen_user_guides exists with an incompatible schema"
                )
            return

    if context.dialect.name == "postgresql":
        # Fail promptly instead of queuing a DDL lock that blocks sign-ins.
        op.execute("SET LOCAL lock_timeout = '2s'")
        op.execute("SET LOCAL statement_timeout = '10s'")
    # PostgreSQL 11+ installs a constant default without rewriting every user row.
    # Existing users stay quiet; the next statement gives NEW accounts an empty list.
    op.add_column(
        "user",
        sa.Column(
            "seen_user_guides",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("""'["student-home", "my-requests"]'"""),
        ),
    )
    with op.batch_alter_table("user") as batch:
        batch.alter_column(
            "seen_user_guides",
            existing_type=sa.JSON(),
            existing_nullable=False,
            server_default=sa.text("'[]'"),
        )


def downgrade() -> None:
    op.drop_column("user", "seen_user_guides")
