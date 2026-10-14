"""Persist whole-class lifecycle and per-target execution records.

Revision ID: tjrec01
Revises: mrg10_merge_alertconn_regpolicy
"""

import sqlalchemy as sa
from alembic import op

revision = "tjrec01"
down_revision = "mrg10_merge_alertconn_regpolicy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT r.run_batch_id FROM teacher_judge_script_runs r
                JOIN teacher_judge_script_artifacts a ON a.id = r.artifact_id
                WHERE r.run_batch_id IS NOT NULL GROUP BY r.run_batch_id
                HAVING count(DISTINCT a.teaching_class_id) > 1
                    OR count(DISTINCT a.session_id) > 1
                    OR count(DISTINCT a.artifact_set_id) > 1
            ) THEN
                RAISE EXCEPTION 'Conflicting Teacher Judge batch class/session/script-set ownership';
            END IF;
        END $$
    """)
    op.create_table(
        "teacher_judge_run_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "teaching_class_id",
            sa.Uuid(),
            sa.ForeignKey("teaching_classes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("teacher_judge_sessions.id", ondelete="SET NULL"),
        ),
        sa.Column("artifact_set_id", sa.Uuid()),
        sa.Column(
            "started_by", sa.Uuid(), sa.ForeignKey("user.id", ondelete="SET NULL")
        ),
        sa.Column("request_id", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_message", sa.Text()),
        *[
            sa.Column(key, sa.DateTime(timezone=True))
            for key in ("requested_at", "prepared_at", "started_at", "finished_at")
        ],
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'completed_with_failures', 'failed', 'cancelled')",
            name="ck_teacher_judge_batches_status",
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "teaching_class_id"],
            ["teacher_judge_sessions.id", "teacher_judge_sessions.teaching_class_id"],
            name="fk_teacher_judge_batches_session_same_class",
        ),
    )
    op.create_index(
        "ix_teacher_judge_batches_class_created",
        "teacher_judge_run_batches",
        ["teaching_class_id", "created_at"],
    )
    op.create_index(
        "ix_teacher_judge_run_batches_session_id",
        "teacher_judge_run_batches",
        ["session_id"],
    )
    op.create_index(
        "ix_teacher_judge_run_batches_request_id",
        "teacher_judge_run_batches",
        ["request_id"],
    )
    op.create_table(
        "teacher_judge_run_targets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("teacher_judge_script_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_key", sa.String(255), nullable=False),
        sa.Column("student_id", sa.String(36)),
        sa.Column("vmid", sa.Integer()),
        sa.Column("node_key", sa.String(80)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(80)),
        sa.Column("exit_code", sa.Integer()),
        *[
            sa.Column(key, sa.JSON(), nullable=False)
            for key in ("metadata_json", "timing_json", "result_json")
        ],
        *[
            sa.Column(key, sa.DateTime(timezone=True))
            for key in (
                "queued_at",
                "started_at",
                "script_finished_at",
                "received_at",
                "finished_at",
            )
        ],
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "run_id", "target_key", name="uq_teacher_judge_targets_run_key"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_teacher_judge_targets_status",
        ),
    )
    for name, columns in (
        ("ix_teacher_judge_targets_run_status", ["run_id", "status"]),
        ("ix_teacher_judge_targets_finished", ["finished_at"]),
        ("ix_teacher_judge_run_targets_student_id", ["student_id"]),
        ("ix_teacher_judge_run_targets_vmid", ["vmid"]),
    ):
        op.create_index(name, "teacher_judge_run_targets", columns)
    op.add_column(
        "teacher_judge_script_runs",
        sa.Column("prepared_at", sa.DateTime(timezone=True)),
    )
    op.add_column("teacher_judge_script_runs", sa.Column("request_id", sa.String(64)))
    op.create_index(
        "ix_teacher_judge_script_runs_request_id",
        "teacher_judge_script_runs",
        ["request_id"],
    )
    # Historical request start and target finish were not observed: leave NULL.
    op.execute("""
        INSERT INTO teacher_judge_run_batches
            (id, teaching_class_id, session_id, artifact_set_id, started_by,
             status, started_at, finished_at, created_at, updated_at)
        SELECT r.run_batch_id, min(a.teaching_class_id::text)::uuid,
            min(a.session_id::text)::uuid, min(a.artifact_set_id::text)::uuid,
            min(r.started_by::text)::uuid,
            CASE
                WHEN bool_or(r.status::text = 'running') THEN 'running'
                WHEN bool_or(r.status::text = 'pending') THEN
                    CASE WHEN count(r.started_at) > 0 THEN 'running' ELSE 'pending' END
                WHEN bool_and(r.status::text = 'cancelled') THEN 'cancelled'
                WHEN bool_and(r.status::text = 'completed')
                    AND NOT bool_or(COALESCE(r.result_summary_json->>'failed', '0') != '0') THEN 'completed'
                WHEN bool_or(r.status::text = 'completed') THEN 'completed_with_failures'
                ELSE 'failed'
            END,
            min(r.started_at),
            CASE WHEN count(r.finished_at) = count(*) THEN max(r.finished_at) END,
            min(r.created_at), max(r.updated_at)
        FROM teacher_judge_script_runs r
        JOIN teacher_judge_script_artifacts a ON a.id = r.artifact_id
        WHERE r.run_batch_id IS NOT NULL
        GROUP BY r.run_batch_id
    """)
    op.create_foreign_key(
        "fk_teacher_judge_runs_batch",
        "teacher_judge_script_runs",
        "teacher_judge_run_batches",
        ["run_batch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Preserve malformed legacy timestamps as unknown rather than failing the
    # entire migration or fabricating a timestamp from the run finish.
    op.execute("""
        CREATE OR REPLACE FUNCTION pg_temp.teacher_judge_timestamp(value text)
        RETURNS timestamptz LANGUAGE plpgsql AS $$
        BEGIN RETURN value::timestamptz;
        EXCEPTION WHEN OTHERS THEN RETURN NULL;
        END $$
    """)
    op.execute("""
        WITH legacy AS (
            SELECT r.id AS run_id, r.created_at, r.updated_at, item.value AS result,
                r.status::text AS run_status,
                CASE WHEN NULLIF(item.value->>'student_id', '') IS NOT NULL
                    THEN 'student:' || (item.value->>'student_id') || ':node:' || COALESCE(item.value->>'node_key', '')
                    ELSE 'vmid:' || COALESCE(item.value->>'vmid', 'None') END AS target_key,
                item.ordinality
            FROM teacher_judge_script_runs r
            CROSS JOIN LATERAL jsonb_array_elements(
                (CASE WHEN jsonb_typeof(r.target_snapshot_json::jsonb->'targets') = 'array'
                    THEN r.target_snapshot_json::jsonb->'targets' ELSE '[]'::jsonb END) ||
                (CASE WHEN jsonb_typeof(r.target_snapshot_json::jsonb->'preflight_results') = 'array'
                    THEN r.target_snapshot_json::jsonb->'preflight_results' ELSE '[]'::jsonb END) ||
                (CASE WHEN jsonb_typeof(r.target_results_json::jsonb->'targets') = 'array'
                    THEN r.target_results_json::jsonb->'targets' ELSE '[]'::jsonb END
                )
            ) WITH ORDINALITY AS item(value, ordinality)
            WHERE jsonb_typeof(item.value) = 'object'
        ), targets AS (
            SELECT DISTINCT ON (run_id, target_key) * FROM legacy
            ORDER BY run_id, target_key, ordinality DESC
        )
        INSERT INTO teacher_judge_run_targets
            (id, run_id, target_key, student_id, vmid, node_key, status, stage,
             reason_code, exit_code, metadata_json, timing_json, result_json,
             queued_at, started_at, script_finished_at, received_at, finished_at,
             created_at, updated_at)
        SELECT md5(run_id::text || ':' || target_key)::uuid, run_id, target_key,
            result->>'student_id',
            CASE WHEN result->>'vmid' ~ '^[0-9]{1,9}$' THEN (result->>'vmid')::int END,
            result->>'node_key',
            CASE WHEN result->>'status' IN ('completed', 'failed', 'cancelled') THEN result->>'status'
                WHEN run_status IN ('completed', 'failed', 'cancelled') THEN 'failed' ELSE 'queued' END,
            CASE WHEN result->>'status' IN ('completed', 'failed', 'cancelled') THEN result->>'status'
                WHEN run_status IN ('completed', 'failed', 'cancelled') THEN 'failed' ELSE 'pending_executor' END,
            CASE WHEN result->>'status' IN ('completed', 'failed', 'cancelled') THEN result->>'reason_code'
                WHEN run_status IN ('completed', 'failed', 'cancelled') THEN 'legacy_result_missing' END,
            CASE WHEN result->>'exit_code' ~ '^-?[0-9]{1,9}$' THEN (result->>'exit_code')::int END,
            jsonb_strip_nulls(jsonb_build_object(
                'name', result->'name', 'node_name', result->'node_name',
                'node_role', result->'node_role', 'display_label', result->'display_label',
                'proxmox_node', result->'proxmox_node', 'resource_type', result->'resource_type', 'user', result->'user')),
            CASE WHEN jsonb_typeof(result->'timing') = 'object'
                THEN (result->'timing') - ARRAY['queued_at', 'started_at', 'script_finished_at', 'received_at', 'finished_at']
                ELSE '{}'::jsonb END,
            jsonb_strip_nulls(jsonb_build_object(
                'validation', result->'validation', 'stdout_excerpt', result->'stdout_excerpt',
                'stderr_excerpt', result->'stderr_excerpt', 'raw_result_json', result->'raw_result_json',
                'parsed_result', result->'parsed_result', 'teacher_review', result->'teacher_review', 'failure_stage', result->'failure_stage')),
            pg_temp.teacher_judge_timestamp(result->'timing'->>'queued_at'),
            pg_temp.teacher_judge_timestamp(result->'timing'->>'started_at'),
            pg_temp.teacher_judge_timestamp(result->'timing'->>'script_finished_at'),
            pg_temp.teacher_judge_timestamp(result->'timing'->>'received_at'),
            pg_temp.teacher_judge_timestamp(result->'timing'->>'finished_at'),
            created_at, updated_at
        FROM targets
    """)
    # Keep the original JSON intact as the migration's recoverable legacy source.


def downgrade() -> None:
    # Reconstruct canonical results before dropping the new tables, including
    # teacher reviews made after migration. Historical originals stay intact.
    op.execute("""
        UPDATE teacher_judge_script_runs r SET target_results_json =
            jsonb_build_object('schema_version', 'teacher_judge_run_results.v2', 'targets', q.targets)
        FROM (
            SELECT run_id, jsonb_agg(metadata_json::jsonb || result_json::jsonb || jsonb_build_object(
                'vmid', vmid, 'student_id', student_id, 'node_key', node_key,
                'status', status, 'reason_code', reason_code, 'exit_code', exit_code,
                'timing', timing_json::jsonb || jsonb_strip_nulls(jsonb_build_object(
                    'queued_at', queued_at, 'started_at', started_at, 'script_finished_at', script_finished_at,
                    'received_at', received_at, 'finished_at', finished_at))
            ) ORDER BY vmid NULLS FIRST, student_id) AS targets
            FROM teacher_judge_run_targets WHERE status IN ('completed', 'failed', 'cancelled') GROUP BY run_id
        ) q WHERE r.id = q.run_id
    """)
    op.drop_constraint(
        "fk_teacher_judge_runs_batch", "teacher_judge_script_runs", type_="foreignkey"
    )
    op.drop_index(
        "ix_teacher_judge_script_runs_request_id",
        table_name="teacher_judge_script_runs",
    )
    op.drop_column("teacher_judge_script_runs", "request_id")
    op.drop_column("teacher_judge_script_runs", "prepared_at")
    op.drop_table("teacher_judge_run_targets")
    op.drop_table("teacher_judge_run_batches")
