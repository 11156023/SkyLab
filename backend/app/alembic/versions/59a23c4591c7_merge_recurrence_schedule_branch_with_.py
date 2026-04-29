"""merge recurrence schedule branch with deletion/service template branch

Revision ID: 59a23c4591c7
Revises: r7s8t9u0v1w2, dr01_sync_constraints
Create Date: 2026-04-29 00:38:49.161684

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision = '59a23c4591c7'
down_revision = ('r7s8t9u0v1w2', 'dr01_sync_constraints')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
