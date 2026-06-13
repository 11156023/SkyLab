"""Add api_key_name and request duration

Revision ID: 7b4a08d7cf39
Revises: l3m4n5o6p7q8
Create Date: 2026-04-04 19:53:48.703670

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '7b4a08d7cf39'
down_revision = 'l3m4n5o6p7q8'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    ai_api_requests_cols = [col['name'] for col in inspector.get_columns('ai_api_requests')]
    if 'duration' not in ai_api_requests_cols:
        op.add_column('ai_api_requests', sa.Column('duration', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False, server_default="never"))

    ai_api_credentials_cols = [col['name'] for col in inspector.get_columns('ai_api_credentials')]
    if 'api_key_name' not in ai_api_credentials_cols:
        op.add_column('ai_api_credentials', sa.Column('api_key_name', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False, server_default="test"))

def downgrade():
    op.drop_column('ai_api_requests', 'duration')
    op.drop_column('ai_api_credentials', 'api_key_name')
