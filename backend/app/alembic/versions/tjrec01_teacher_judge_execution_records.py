"""Persist whole-class lifecycle and per-target execution records.

Revision ID: tjrec01
Revises: mrg10_merge_alertconn_regpolicy
"""

import hashlib
import re
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "tjrec01"
down_revision = "mrg10_merge_alertconn_regpolicy"
branch_labels = None
depends_on = None

_TIME_KEYS = (
    "queued_at",
    "started_at",
    "script_finished_at",
    "received_at",
    "finished_at",
)
_METADATA_KEYS = (
    "name",
    "node_name",
    "node_role",
    "display_label",
    "proxmox_node",
    "resource_type",
    "user",
)
_RESULT_KEYS = (
    "validation",
    "stdout_excerpt",
    "stderr_excerpt",
    "raw_result_json",
    "parsed_result",
    "teacher_review",
    "failure_stage",
)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strip_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_nulls(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_strip_nulls(item) for item in value]
    return value


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _target_key(result: dict[str, Any]) -> str:
    student_id = result.get("student_id")
    if student_id not in (None, ""):
        return f"student:{student_id}:node:{result.get('node_key') or ''}"
    return f"vmid:{result.get('vmid')}"


def _backfill_targets() -> None:
    connection = op.get_bind()
    runs = sa.table(
        "teacher_judge_script_runs",
        sa.column("id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("status", sa.Text()),
        sa.column("target_snapshot_json", sa.JSON()),
        sa.column("target_results_json", sa.JSON()),
    )
    targets = sa.table(
        "teacher_judge_run_targets",
        sa.column("id", sa.Uuid()),
        sa.column("run_id", sa.Uuid()),
        sa.column("target_key", sa.String()),
        sa.column("student_id", sa.String()),
        sa.column("vmid", sa.Integer()),
        sa.column("node_key", sa.String()),
        sa.column("status", sa.String()),
        sa.column("stage", sa.String()),
        sa.column("reason_code", sa.String()),
        sa.column("exit_code", sa.Integer()),
        sa.column("metadata_json", sa.JSON()),
        sa.column("timing_json", sa.JSON()),
        sa.column("result_json", sa.JSON()),
        *(sa.column(key, sa.DateTime(timezone=True)) for key in _TIME_KEYS),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    last_id = None
    while True:
        query = sa.select(
            runs.c.id,
            runs.c.created_at,
            runs.c.updated_at,
            runs.c.status,
            runs.c.target_snapshot_json,
            runs.c.target_results_json,
        ).order_by(runs.c.id).limit(200)
        if last_id is not None:
            query = query.where(runs.c.id > last_id)
        run_rows = connection.execute(query).mappings().all()
        if not run_rows:
            break

        inserts = []
        for run in run_rows:
            snapshot = _mapping(run["target_snapshot_json"])
            results_document = _mapping(run["target_results_json"])
            sources = []
            for key in ("targets", "preflight_results"):
                value = snapshot.get(key)
                if isinstance(value, list):
                    sources.extend(value)
            value = results_document.get("targets")
            if isinstance(value, list):
                sources.extend(value)

            # Later canonical results override preflight/snapshot rows with the
            # same identity, matching the old JSONB backfill ordering.
            by_key: dict[str, dict[str, Any]] = {}
            for item in sources:
                if isinstance(item, dict):
                    by_key[_target_key(item)] = item

            run_status = str(run["status"])
            for key, result in by_key.items():
                result_status = result.get("status")
                terminal = result_status in {"completed", "failed", "cancelled"}
                run_terminal = run_status in {"completed", "failed", "cancelled"}
                status = result_status if terminal else "failed" if run_terminal else "queued"
                stage = (
                    result_status
                    if terminal
                    else "failed"
                    if run_terminal
                    else "pending_executor"
                )
                reason = (
                    result.get("reason_code")
                    if terminal
                    else "legacy_result_missing"
                    if run_terminal
                    else None
                )
                raw_vmid = result.get("vmid")
                vmid = (
                    int(raw_vmid)
                    if re.fullmatch(r"[0-9]{1,9}", str(raw_vmid or ""))
                    else None
                )
                raw_exit_code = result.get("exit_code")
                exit_code = (
                    int(raw_exit_code)
                    if re.fullmatch(r"-?[0-9]{1,9}", str(raw_exit_code or ""))
                    else None
                )
                timing = _mapping(result.get("timing"))
                inserts.append(
                    {
                        "id": uuid.UUID(
                            hashlib.md5(
                                f"{run['id']}:{key}".encode(),
                                usedforsecurity=False,
                            ).hexdigest()
                        ),
                        "run_id": run["id"],
                        "target_key": key,
                        "student_id": (
                            str(result["student_id"])
                            if result.get("student_id") is not None
                            else None
                        ),
                        "vmid": vmid,
                        "node_key": result.get("node_key"),
                        "status": status,
                        "stage": stage,
                        "reason_code": reason,
                        "exit_code": exit_code,
                        "metadata_json": _strip_nulls(
                            {name: result[name] for name in _METADATA_KEYS if name in result}
                        ),
                        "timing_json": {
                            name: value
                            for name, value in timing.items()
                            if name not in _TIME_KEYS
                        },
                        "result_json": _strip_nulls(
                            {name: result[name] for name in _RESULT_KEYS if name in result}
                        ),
                        **{name: _timestamp(timing.get(name)) for name in _TIME_KEYS},
                        "created_at": run["created_at"],
                        "updated_at": run["updated_at"],
                    }
                )
        if inserts:
            connection.execute(targets.insert(), inserts)
        last_id = run_rows[-1]["id"]


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
    # Decode legacy JSON with the driver's UTF-8 JSON loader and write it back to
    # JSON columns. Casting through JSONB fails on SQL_ASCII databases when a
    # payload contains escaped non-ASCII characters (for example Chinese text).
    _backfill_targets()
    # Keep the original JSON intact as the migration's recoverable legacy source.


def downgrade() -> None:
    # Reconstruct canonical results before dropping the new tables, including
    # teacher reviews made after migration. Historical originals stay intact.
    connection = op.get_bind()
    targets = sa.table(
        "teacher_judge_run_targets",
        sa.column("run_id", sa.Uuid()),
        sa.column("target_key", sa.String()),
        sa.column("student_id", sa.String()),
        sa.column("vmid", sa.Integer()),
        sa.column("node_key", sa.String()),
        sa.column("status", sa.String()),
        sa.column("reason_code", sa.String()),
        sa.column("exit_code", sa.Integer()),
        sa.column("metadata_json", sa.JSON()),
        sa.column("timing_json", sa.JSON()),
        sa.column("result_json", sa.JSON()),
        *(sa.column(key, sa.DateTime(timezone=True)) for key in _TIME_KEYS),
    )
    query = (
        sa.select(targets)
        .where(targets.c.status.in_(("completed", "failed", "cancelled")))
        .order_by(
            targets.c.run_id,
            targets.c.vmid,
            targets.c.student_id,
            targets.c.target_key,
        )
    )
    grouped: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for row in connection.execute(query).mappings():
        timing = _mapping(row["timing_json"])
        for key in _TIME_KEYS:
            value = row[key]
            if value is not None:
                timing[key] = value.isoformat()
        result = {
            **_mapping(row["metadata_json"]),
            **_mapping(row["result_json"]),
            "vmid": row["vmid"],
            "student_id": str(row["student_id"]) if row["student_id"] else None,
            "node_key": row["node_key"],
            "status": row["status"],
            "reason_code": row["reason_code"],
            "exit_code": row["exit_code"],
        }
        if timing:
            result["timing"] = timing
        grouped.setdefault(row["run_id"], []).append(result)

    runs = sa.table(
        "teacher_judge_script_runs",
        sa.column("id", sa.Uuid()),
        sa.column("target_results_json", sa.JSON()),
    )
    update = sa.update(runs).where(runs.c.id == sa.bindparam("p_run_id")).values(
        target_results_json=sa.bindparam("p_payload", type_=sa.JSON())
    )
    if grouped:
        connection.execute(
            update,
            [
            {
                "p_run_id": run_id,
                "p_payload": {
                    "schema_version": "teacher_judge_run_results.v2",
                    "targets": values,
                },
            }
            for run_id, values in grouped.items()
            ],
        )
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
