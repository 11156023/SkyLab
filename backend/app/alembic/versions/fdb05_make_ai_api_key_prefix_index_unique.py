"""make ai api key prefix index unique

Revision ID: fdb05_ai_api_key_prefix_unique
Revises: fdb04_drop_legacy_nat_unique
Create Date: 2026-05-18 00:00:00.000000

The SQLModel field declares ``api_key_prefix`` as a unique indexed value, but
older migrations could leave a non-unique index with the same name.  Recreate
the index with the model's uniqueness so Alembic autogenerate is clean.

"""

from alembic import op

revision = "fdb05_ai_api_key_prefix_unique"
down_revision = "fdb04_drop_legacy_nat_unique"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DROP INDEX IF EXISTS ix_ai_api_credentials_api_key_prefix")
    op.execute(
        """
        CREATE UNIQUE INDEX ix_ai_api_credentials_api_key_prefix
        ON ai_api_credentials (api_key_prefix)
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_ai_api_credentials_api_key_prefix")
    op.execute(
        """
        CREATE INDEX ix_ai_api_credentials_api_key_prefix
        ON ai_api_credentials (api_key_prefix)
        """
    )
