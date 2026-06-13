"""Add api_key_name to AIAPIRequest

Revision ID: 55989c510c6c
Revises: 7b4a08d7cf39
Create Date: 2026-04-04 20:44:15.961492

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision = '55989c510c6c'
down_revision = '7b4a08d7cf39'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = [col['name'] for col in inspector.get_columns('ai_api_requests')]
    if 'api_key_name' not in columns:
        op.add_column('ai_api_requests', sa.Column('api_key_name', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False, server_default="test"))


def downgrade():
    op.drop_column('ai_api_requests', 'api_key_name')
