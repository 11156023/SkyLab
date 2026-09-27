"""補上常用過濾欄位與外鍵欄位缺的索引。

- spec_change_requests 除 resource_vmid 外沒有任何索引，但申請人列表
  （user_id + status）與審核佇列（status）都依 created_at 排序。
- 其餘是沒有索引的外鍵欄位：RESTRICT / SET NULL / CASCADE 在刪除父列時
  都要掃子表，沒有索引就是全表掃描（例如刪帳號要掃每張 *_by 表）。

Revision ID: dbm05_missing_fk_indexes
Revises: dbh01_user_fk_template_uq
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "dbm05_missing_fk_indexes"
down_revision = "dbh01_user_fk_template_uq"
branch_labels = None
depends_on = None


# (索引名, 表, 欄位)
_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "ix_spec_change_requests_user_status_created",
        "spec_change_requests",
        ("user_id", "status", "created_at"),
    ),
    (
        "ix_spec_change_requests_status_created",
        "spec_change_requests",
        ("status", "created_at"),
    ),
    ("ix_spec_change_requests_reviewer_id", "spec_change_requests", ("reviewer_id",)),
    ("ix_vm_requests_reviewer_id", "vm_requests", ("reviewer_id",)),
    ("ix_vm_requests_batch_job_id", "vm_requests", ("batch_job_id",)),
    ("ix_resources_batch_job_id", "resources", ("batch_job_id",)),
    ("ix_batch_provision_tasks_user_id", "batch_provision_tasks", ("user_id",)),
    ("ix_batch_provision_jobs_initiated_by", "batch_provision_jobs", ("initiated_by",)),
    ("ix_batch_provision_jobs_reviewer_id", "batch_provision_jobs", ("reviewer_id",)),
    (
        "ix_teaching_class_student_machines_machine_node_id",
        "teaching_class_student_machines",
        ("machine_node_id",),
    ),
    (
        "ix_teaching_class_student_machines_batch_task_id",
        "teaching_class_student_machines",
        ("batch_task_id",),
    ),
    (
        "ix_course_environment_nodes_source_template_id",
        "course_environment_nodes",
        ("source_template_id",),
    ),
    (
        "ix_teaching_class_machine_nodes_source_template_id",
        "teaching_class_machine_nodes",
        ("source_template_id",),
    ),
    (
        "ix_class_capacity_reservations_course_version_id",
        "class_capacity_reservations",
        ("course_version_id",),
    ),
    ("ix_alert_events_acknowledged_by", "alert_events", ("acknowledged_by",)),
    ("ix_mining_incidents_reviewed_by", "mining_incidents", ("reviewed_by",)),
    ("ix_resource_shares_granted_by", "resource_shares", ("granted_by",)),
    (
        "ix_course_environment_files_uploaded_by",
        "course_environment_files",
        ("uploaded_by",),
    ),
    (
        "ix_teacher_judge_script_artifacts_approved_by",
        "teacher_judge_script_artifacts",
        ("approved_by",),
    ),
)


def _existing(table: str) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table)
        if index.get("name")
    }


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        if name not in _existing(table):
            op.create_index(name, table, list(columns))


def downgrade() -> None:
    for name, table, _columns in reversed(_INDEXES):
        if name in _existing(table):
            op.drop_index(name, table_name=table)
