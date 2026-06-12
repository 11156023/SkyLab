"""restore vm_requests window indexes

Revision ID: d4f05b0e324a
Revises: 99116213834b
Create Date: 2026-05-12 01:13:23.340581

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'd4f05b0e324a'
down_revision = '99116213834b'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE INDEX IF NOT EXISTS ix_vm_requests_next_window_end ON vm_requests (next_window_end)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_vm_requests_next_window_start ON vm_requests (next_window_start)')


def downgrade():
    op.drop_index(op.f('ix_vm_requests_next_window_end'), table_name='vm_requests')
    op.drop_index(op.f('ix_vm_requests_next_window_start'), table_name='vm_requests')
