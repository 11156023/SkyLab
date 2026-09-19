"""Add Teacher Judge artifact-set and run-batch identities."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "tjmm01_teacher_judge_mm_sets"
# Merge the existing node-scope and password-nullability heads before adding
# the multi-machine identities, so a normal upgrade reaches one canonical head.
down_revision = (
    "tjtn01_teacher_judge_node_scope",
    "vmreqpw02_password_not_null",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "teacher_judge_script_artifacts",
        sa.Column("artifact_set_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "teacher_judge_script_artifacts",
        sa.Column("target_node_key", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "teacher_judge_script_artifacts",
        sa.Column("source_analysis_revision", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_teacher_judge_script_artifacts_artifact_set_id",
        "teacher_judge_script_artifacts",
        ["artifact_set_id"],
    )
    op.create_index(
        "ix_teacher_judge_script_artifacts_target_node_key",
        "teacher_judge_script_artifacts",
        ["target_node_key"],
    )
    op.add_column(
        "teacher_judge_script_runs",
        sa.Column("run_batch_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_teacher_judge_script_runs_run_batch_id",
        "teacher_judge_script_runs",
        ["run_batch_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_teacher_judge_script_runs_run_batch_id",
        table_name="teacher_judge_script_runs",
    )
    op.drop_column("teacher_judge_script_runs", "run_batch_id")
    op.drop_index(
        "ix_teacher_judge_script_artifacts_target_node_key",
        table_name="teacher_judge_script_artifacts",
    )
    op.drop_index(
        "ix_teacher_judge_script_artifacts_artifact_set_id",
        table_name="teacher_judge_script_artifacts",
    )
    op.drop_column("teacher_judge_script_artifacts", "source_analysis_revision")
    op.drop_column("teacher_judge_script_artifacts", "target_node_key")
    op.drop_column("teacher_judge_script_artifacts", "artifact_set_id")
