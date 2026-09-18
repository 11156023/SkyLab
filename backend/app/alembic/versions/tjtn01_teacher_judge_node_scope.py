"""Add logical-node fan-out scope for Teacher Judge runs."""

from __future__ import annotations

from alembic import op

revision = "tjtn01_teacher_judge_node_scope"
down_revision = "cefile01_environment_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE teacherjudgescriptruntargetscope "
            "ADD VALUE IF NOT EXISTS 'all_students_on_node'"
        )


def downgrade() -> None:
    # PostgreSQL enum value removal is not safe while rows may use the new
    # scope. Keep the value on downgrade; the application can still read old
    # scopes and no destructive data rewrite is attempted here.
    pass
