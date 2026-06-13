"""add_rate_limit_to_ai_api_requests_and_credentials

Revision ID: f6b3542f1194
Revises: 55989c510c6c
Create Date: 2026-04-04 21:42:31.895406

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f6b3542f1194"
down_revision = "55989c510c6c"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    request_columns = {col["name"] for col in inspector.get_columns("ai_api_requests")}
    if "rate_limit" not in request_columns:
        op.add_column(
            "ai_api_requests",
            sa.Column(
                "rate_limit",
                sa.Integer(),
                nullable=True,
                comment="每分鐘請求限制（1-1000），None 使用預設值 20",
            ),
        )

    credential_columns = {
        col["name"] for col in inspector.get_columns("ai_api_credentials")
    }
    if "rate_limit" not in credential_columns:
        op.add_column(
            "ai_api_credentials",
            sa.Column(
                "rate_limit",
                sa.Integer(),
                nullable=True,
                comment="每分鐘請求限制（1-1000），None 使用預設值 20",
            ),
        )


def downgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    credential_columns = {
        col["name"] for col in inspector.get_columns("ai_api_credentials")
    }
    if "rate_limit" in credential_columns:
        op.drop_column("ai_api_credentials", "rate_limit")

    request_columns = {col["name"] for col in inspector.get_columns("ai_api_requests")}
    if "rate_limit" in request_columns:
        op.drop_column("ai_api_requests", "rate_limit")
