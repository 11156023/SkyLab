"""狀態／列舉類字串欄位補 CHECK 約束（M4）。

這些欄位存成 VARCHAR，程式寫入的值是封閉集合，但資料庫完全不擋，
打錯字或舊資料只會在讀取端變成「看不懂的狀態」。只挑值域確定封閉的欄位；
自由文字（environment_type、placement_strategy_used、call_type 等）不加；
ai_template_call_logs.preset 舊資料曾存 Teacher Judge 的範本 key（linux／n8n…），也不加。

先以 NOT VALID 加上（只約束新寫入），再檢查既有資料：
沒有違規列才 VALIDATE；有的話保留 NOT VALID 並印出違規值，由維運清理後
再手動 ``ALTER TABLE ... VALIDATE CONSTRAINT``。NULL 不受 IN 限制。

Revision ID: dbm04_status_checks
Revises: dbm07_course_node_key_fks
Create Date: 2026-09-27
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "dbm04_status_checks"
down_revision = "dbm07_course_node_key_fks"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


# (表, 欄位, 允許值)
_CHECKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "quick_practice_sessions",
        "status",
        ("creating", "ready", "partial_failed", "stopping", "reclaiming", "reclaimed"),
    ),
    ("quick_practice_session_machines", "resource_type", ("qemu", "lxc")),
    ("teaching_class_machine_nodes", "source_type", ("template", "custom")),
    (
        "teaching_class_student_machines",
        "status",
        ("pending", "running", "completed", "failed", "reclaimed"),
    ),
    ("teaching_class_weeks", "status", ("draft", "published", "completed")),
    ("course_environments", "usage_scope", ("course", "quick_practice", "both")),
    ("course_environment_versions", "peer_policy", ("explicit", "segment")),
    ("course_environment_nodes", "resource_type", ("qemu", "lxc")),
    ("course_environment_publications", "mode", ("domain", "port_forward")),
    ("course_environment_publications", "protocol", ("tcp", "udp")),
    ("course_environment_edges", "direction", ("one_way", "bidirectional")),
    (
        "course_environment_edges",
        "protocol",
        ("any", "tcp", "udp", "icmp", "icmpv6", "sctp"),
    ),
    ("class_capacity_reservations", "status", ("reserved", "consumed", "released")),
    ("resources", "allocation_scope", ("personal", "teaching_class")),
    ("resources", "control_policy", ("owner", "class_member")),
    (
        "resources",
        "auto_stop_reason",
        ("ttl_expired", "idle", "window_grace", "practice_quota"),
    ),
    ("vm_requests", "resource_type", ("vm", "lxc")),
    ("vm_requests", "requested_mode", ("manual", "auto")),
    ("vm_requests", "request_kind", ("research", "quick_template", "course")),
    ("user", "auth_source", ("local", "ldap")),
    ("ai_api_usage", "status", ("success", "error", "cancelled")),
    ("ai_template_call_logs", "status", ("success", "error")),
    ("proxmox_storages", "speed_tier", ("nvme", "ssd", "hdd", "unknown")),
    ("batch_provision_jobs", "resource_type", ("lxc", "qemu")),
    ("teacher_judge_files", "source_type", ("created", "uploaded")),
)


def _name(table: str, column: str) -> str:
    return f"ck_{table}_{column}"


def _condition(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _quoted(table: str) -> str:
    return f'"{table}"'


def _existing_checks(table: str) -> set[str]:
    return {
        c["name"]
        for c in sa.inspect(op.get_bind()).get_check_constraints(table)
        if c.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    for table, column, values in _CHECKS:
        name = _name(table, column)
        if name in _existing_checks(table):
            continue
        condition = _condition(column, values)
        op.create_check_constraint(name, table, condition, postgresql_not_valid=True)
        bad = bind.execute(
            sa.text(
                f"SELECT DISTINCT {column} FROM {_quoted(table)} "
                f"WHERE {column} IS NOT NULL AND NOT ({condition})"
            )
        ).fetchall()
        if bad:
            logger.warning(
                "%s.%s 有不在允許值內的既有資料 %s；%s 保留 NOT VALID，清理後請手動 VALIDATE",
                table,
                column,
                sorted(str(row[0]) for row in bad),
                name,
            )
            continue
        op.execute(f"ALTER TABLE {_quoted(table)} VALIDATE CONSTRAINT {name}")


def downgrade() -> None:
    for table, column, _values in reversed(_CHECKS):
        name = _name(table, column)
        if name in _existing_checks(table):
            op.drop_constraint(name, table, type_="check")
