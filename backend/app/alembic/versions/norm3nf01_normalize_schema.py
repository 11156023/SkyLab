"""資料庫正規化（1NF／3NF）：拆多值欄位、移除遞移相依、補一致性約束

1NF（多值欄位拆成子表）：
- subnet_config.dns_servers／extra_blocked_subnets → subnet_dns_servers／subnet_blocked_subnets
- teacher_judge_student_submissions.completed_item_ids → teacher_judge_submission_items
- wireguard_peers.allowed_endpoints → wireguard_peer_endpoints

3NF（移除遞移相依的複製欄位）：
- teacher_judge_script_runs.teaching_class_id（= artifact.teaching_class_id）
- teacher_judge_student_submissions.teaching_class_id（= artifact.teaching_class_id）、
  is_ready（= ready_at IS NOT NULL）
- teacher_judge_session_attachments.message_id → teacher_judge_message_attachments
  （訊息已決定 session，附件表不再同時存兩者）
- quick_practice_session_machines.name／role／resource_type／sort_order
  （= 已發佈版本中同 node_key 節點的值）
- teaching_class_student_machines.vmid／status／error（= batch task 的值）

一致性約束（保留的冗餘交給資料庫把關）：
- audit_logs／deletion_requests／spec_change_requests／ip_allocation：
  resource_vmid 只能是 NULL 或等於 vmid
- 選填參照必須屬於同一個班級／使用者的複合外鍵（artifact→session／檔案、
  session→週次／檔案、ai_api_usage→憑證擁有者）

升級前會檢查既有資料；任何會遺失資料或違反新約束的列都會讓 migration
直接失敗並列出原因，不會默默修正。

Revision ID: norm3nf01_normalize_schema
Revises: pentrydns01_platform_entry_dns
Create Date: 2026-10-05 00:00:00.000000

"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision = "norm3nf01_normalize_schema"
down_revision = "pentrydns01_platform_entry_dns"
branch_labels = None
depends_on = None


_VMID_CHECK = "resource_vmid IS NULL OR (vmid IS NOT NULL AND resource_vmid = vmid)"
_VMID_CHECK_TABLES = (
    "audit_logs",
    "deletion_requests",
    "spec_change_requests",
    "ip_allocation",
)

# (name, source table, referent table, local columns, remote columns)
_SAME_PARENT_FKS = (
    (
        "fk_teacher_judge_artifacts_session_same_class",
        "teacher_judge_script_artifacts",
        "teacher_judge_sessions",
        ["session_id", "teaching_class_id"],
        ["id", "teaching_class_id"],
    ),
    (
        "fk_teacher_judge_artifacts_file_same_class",
        "teacher_judge_script_artifacts",
        "teacher_judge_files",
        ["source_file_id", "teaching_class_id"],
        ["id", "teaching_class_id"],
    ),
    (
        "fk_teacher_judge_sessions_week_same_class",
        "teacher_judge_sessions",
        "teaching_class_weeks",
        ["teaching_class_week_id", "teaching_class_id"],
        ["id", "class_id"],
    ),
    (
        "fk_teacher_judge_sessions_file_same_class",
        "teacher_judge_sessions",
        "teacher_judge_files",
        ["selected_file_id", "teaching_class_id"],
        ["id", "teaching_class_id"],
    ),
    (
        "fk_ai_api_usage_credential_owner",
        "ai_api_usage",
        "ai_api_credentials",
        ["credential_id", "user_id"],
        ["id", "user_id"],
    ),
)

# (name, table, columns) — referents of the composite FKs above
_PARENT_UNIQUES = (
    (
        "uq_teacher_judge_sessions_id_class",
        "teacher_judge_sessions",
        ["id", "teaching_class_id"],
    ),
    (
        "uq_teacher_judge_files_id_class",
        "teacher_judge_files",
        ["id", "teaching_class_id"],
    ),
    ("uq_teaching_class_weeks_id_class", "teaching_class_weeks", ["id", "class_id"]),
    ("uq_ai_api_credentials_id_user", "ai_api_credentials", ["id", "user_id"]),
)


def _fk_violation_query(
    source: str, referent: str, local: list[str], remote: list[str]
) -> str:
    """Count child rows whose (non-null) composite reference has no parent row."""
    present = " AND ".join(f"c.{column} IS NOT NULL" for column in local)
    matches = " AND ".join(
        f"p.{remote_column} = c.{local_column}"
        for local_column, remote_column in zip(local, remote, strict=True)
    )
    return (
        f"SELECT count(*) FROM {source} c WHERE {present} "
        f"AND NOT EXISTS (SELECT 1 FROM {referent} p WHERE {matches})"
    )


# 每一項：(說明, 回傳違規列數的 SQL)
_PRECHECKS = (
    (
        "quick_practice_session_machines without a matching node in the session's "
        "environment version (name/role/type would be lost)",
        """
        SELECT count(*) FROM quick_practice_session_machines m
        JOIN quick_practice_sessions s ON s.id = m.session_id
        LEFT JOIN course_environment_nodes n
          ON n.version_id = s.environment_version_id AND n.node_key = m.node_key
        WHERE n.id IS NULL
        """,
    ),
    (
        "teaching_class_student_machines with no batch task whose vmid is an "
        "existing resource (the machine link would be lost)",
        """
        SELECT count(*) FROM teaching_class_student_machines m
        WHERE m.batch_task_id IS NULL
          AND EXISTS (SELECT 1 FROM resources r WHERE r.vmid = m.vmid)
        """,
    ),
    (
        "teacher_judge_student_submissions whose completed_item_ids is not a JSON array",
        """
        SELECT count(*) FROM teacher_judge_student_submissions
        WHERE json_typeof(completed_item_ids) <> 'array'
        """,
    ),
    (
        "wireguard_peers whose allowed_endpoints is not a JSON array",
        """
        SELECT count(*) FROM wireguard_peers
        WHERE json_typeof(allowed_endpoints) <> 'array'
        """,
    ),
    (
        "teacher_judge_session_attachments bound to a message of another session",
        """
        SELECT count(*) FROM teacher_judge_session_attachments a
        JOIN teacher_judge_session_messages m ON m.id = a.message_id
        WHERE m.session_id <> a.session_id
        """,
    ),
    *(
        (
            f"{table} rows whose resource_vmid differs from vmid",
            f"SELECT count(*) FROM {table} WHERE NOT ({_VMID_CHECK})",
        )
        for table in _VMID_CHECK_TABLES
    ),
    *(
        (
            f"{source} rows violating {name}",
            _fk_violation_query(source, referent, local, remote),
        )
        for name, source, referent, local, remote in _SAME_PARENT_FKS
    ),
)


def _run_prechecks() -> None:
    conn = op.get_bind()
    problems = []
    for description, query in _PRECHECKS:
        count = conn.execute(sa.text(query)).scalar_one()
        if count:
            problems.append(f"- {count} {description}")
    if problems:
        raise RuntimeError(
            "norm3nf01: existing data must be fixed before normalizing:\n"
            + "\n".join(problems)
        )


def upgrade() -> None:
    _run_prechecks()

    # ── teacher_judge_script_runs：班級由 artifact 決定 ─────────────────────
    op.drop_column("teacher_judge_script_runs", "teaching_class_id")

    # ── teacher_judge_student_submissions ───────────────────────────────────
    op.create_table(
        "teacher_judge_submission_items",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["teacher_judge_student_submissions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("submission_id", "item_id"),
    )
    op.execute(
        """
        INSERT INTO teacher_judge_submission_items (submission_id, item_id)
        SELECT DISTINCT s.id, e.item_id
        FROM teacher_judge_student_submissions s
        CROSS JOIN LATERAL json_array_elements_text(s.completed_item_ids) AS e(item_id)
        WHERE e.item_id <> ''
        """
    )
    # 就緒 = ready_at 有值：先把兩欄對齊（未就緒不留時間、已就緒補上時間）
    op.execute(
        "UPDATE teacher_judge_student_submissions SET ready_at = NULL WHERE NOT is_ready"
    )
    op.execute(
        "UPDATE teacher_judge_student_submissions SET ready_at = updated_at "
        "WHERE is_ready AND ready_at IS NULL"
    )
    op.drop_column("teacher_judge_student_submissions", "completed_item_ids")
    op.drop_column("teacher_judge_student_submissions", "is_ready")
    op.drop_column("teacher_judge_student_submissions", "teaching_class_id")

    # ── 附件屬於哪則訊息改由連結表記錄 ─────────────────────────────────────
    op.create_table(
        "teacher_judge_message_attachments",
        sa.Column("attachment_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["attachment_id"],
            ["teacher_judge_session_attachments.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["teacher_judge_session_messages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("attachment_id"),
    )
    op.create_index(
        "ix_teacher_judge_message_attachments_message_id",
        "teacher_judge_message_attachments",
        ["message_id"],
    )
    op.execute(
        """
        INSERT INTO teacher_judge_message_attachments (attachment_id, message_id)
        SELECT id, message_id FROM teacher_judge_session_attachments
        WHERE message_id IS NOT NULL
        """
    )
    op.drop_column("teacher_judge_session_attachments", "message_id")

    # ── subnet_config 的多值欄位 ─────────────────────────────────────────────
    for table, value_column in (
        ("subnet_dns_servers", "address"),
        ("subnet_blocked_subnets", "cidr"),
    ):
        op.create_table(
            table,
            sa.Column("subnet_config_id", sa.Integer(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column(value_column, sa.String(length=64), nullable=False),
            sa.ForeignKeyConstraint(
                ["subnet_config_id"], ["subnet_config.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("subnet_config_id", "position"),
            sa.UniqueConstraint(
                "subnet_config_id", value_column, name=f"uq_{table}_{value_column}"
            ),
        )
    # 逗號、分號、空白、換行都當分隔；同值只留第一次出現的位置
    for table, value_column, source_column in (
        ("subnet_dns_servers", "address", "dns_servers"),
        ("subnet_blocked_subnets", "cidr", "extra_blocked_subnets"),
    ):
        op.execute(
            f"""
            INSERT INTO {table} (subnet_config_id, position, {value_column})
            SELECT id, row_number() OVER (PARTITION BY id ORDER BY first_seen) - 1, value
            FROM (
                SELECT c.id, t.value, min(t.ord) AS first_seen
                FROM subnet_config c
                CROSS JOIN LATERAL regexp_split_to_table(
                    c.{source_column}, '[,;[:space:]]+'
                ) WITH ORDINALITY AS t(value, ord)
                WHERE c.{source_column} IS NOT NULL AND t.value <> ''
                GROUP BY c.id, t.value
            ) AS items
            """
        )
    op.drop_column("subnet_config", "dns_servers")
    op.drop_column("subnet_config", "extra_blocked_subnets")

    # ── wireguard_peers.allowed_endpoints ───────────────────────────────────
    op.create_table(
        "wireguard_peer_endpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("peer_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("vmid", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("service", sa.String(length=8), nullable=False),
        sa.Column("host", sa.String(length=45), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["peer_id"], ["wireguard_peers.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_wireguard_peer_endpoints_peer_id", "wireguard_peer_endpoints", ["peer_id"]
    )
    op.execute(
        """
        INSERT INTO wireguard_peer_endpoints
            (id, peer_id, position, vmid, name, service, host, port)
        SELECT gen_random_uuid(), p.id, e.ord - 1,
               (e.value->>'vmid')::integer,
               coalesce(e.value->>'name', ''),
               e.value->>'service',
               e.value->>'host',
               (e.value->>'port')::integer
        FROM wireguard_peers p
        CROSS JOIN LATERAL json_array_elements(p.allowed_endpoints)
            WITH ORDINALITY AS e(value, ord)
        """
    )
    op.drop_column("wireguard_peers", "allowed_endpoints")

    # ── 複製欄位 ─────────────────────────────────────────────────────────────
    # 沒有 batch task、vmid 也不是現存機器的對應列本來就是懸空資料（複製欄位
    # 沒跟著來源更新的結果）；拿掉複製欄位後它們會如實顯示為尚未建機。
    dangling = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT count(*) FROM teaching_class_student_machines m
                WHERE m.batch_task_id IS NULL AND m.vmid IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM resources r WHERE r.vmid = m.vmid)
                """
            )
        )
        .scalar_one()
    )
    if dangling:
        logger.warning(
            "norm3nf01: %s teaching_class_student_machines row(s) pointed at a vmid "
            "with no resource and no batch task; they now read as not provisioned",
            dangling,
        )
    for column in ("name", "role", "resource_type", "sort_order"):
        op.drop_column("quick_practice_session_machines", column)
    for column in ("vmid", "status", "error"):
        op.drop_column("teaching_class_student_machines", column)

    # ── 一致性約束 ───────────────────────────────────────────────────────────
    for table in _VMID_CHECK_TABLES:
        op.create_check_constraint(
            f"ck_{table}_resource_vmid_matches", table, _VMID_CHECK
        )
    for name, table, columns in _PARENT_UNIQUES:
        op.create_unique_constraint(name, table, columns)
    for name, source, referent, local, remote in _SAME_PARENT_FKS:
        op.create_foreign_key(name, source, referent, local, remote)


def downgrade() -> None:
    for name, source, _referent, _local, _remote in _SAME_PARENT_FKS:
        op.drop_constraint(name, source, type_="foreignkey")
    for name, table, _columns in _PARENT_UNIQUES:
        op.drop_constraint(name, table, type_="unique")
    for table in _VMID_CHECK_TABLES:
        op.drop_constraint(f"ck_{table}_resource_vmid_matches", table, type_="check")

    # ── teaching_class_student_machines：從 batch task 抄回 ─────────────────
    op.add_column(
        "teaching_class_student_machines",
        sa.Column("vmid", sa.Integer(), nullable=True),
    )
    op.add_column(
        "teaching_class_student_machines",
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="pending"
        ),
    )
    op.add_column(
        "teaching_class_student_machines",
        sa.Column("error", sa.String(length=500), nullable=True),
    )
    op.execute(
        """
        UPDATE teaching_class_student_machines m
        SET vmid = t.vmid,
            status = CASE
                WHEN t.status::text = 'completed' AND t.vmid IS NULL THEN 'reclaimed'
                ELSE t.status::text
            END,
            error = t.error
        FROM batch_provision_tasks t
        WHERE t.id = m.batch_task_id
        """
    )
    op.alter_column("teaching_class_student_machines", "status", server_default=None)
    op.create_check_constraint(
        "ck_teaching_class_student_machines_status",
        "teaching_class_student_machines",
        "status IN ('pending', 'running', 'completed', 'failed', 'reclaimed')",
    )

    # ── quick_practice_session_machines：從節點抄回 ─────────────────────────
    op.add_column(
        "quick_practice_session_machines",
        sa.Column("name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "quick_practice_session_machines",
        sa.Column("role", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "quick_practice_session_machines",
        sa.Column("resource_type", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "quick_practice_session_machines",
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        """
        UPDATE quick_practice_session_machines m
        SET name = n.name, role = n.role, resource_type = n.resource_type,
            sort_order = n.sort_order
        FROM quick_practice_sessions s, course_environment_nodes n
        WHERE s.id = m.session_id
          AND n.version_id = s.environment_version_id
          AND n.node_key = m.node_key
        """
    )
    for column in ("name", "role", "resource_type"):
        op.alter_column("quick_practice_session_machines", column, nullable=False)
    op.alter_column(
        "quick_practice_session_machines", "sort_order", server_default=None
    )
    op.create_check_constraint(
        "ck_quick_practice_session_machines_resource_type",
        "quick_practice_session_machines",
        "resource_type IN ('qemu', 'lxc')",
    )

    # ── wireguard_peers.allowed_endpoints ───────────────────────────────────
    op.add_column(
        "wireguard_peers",
        sa.Column("allowed_endpoints", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.execute(
        """
        UPDATE wireguard_peers p
        SET allowed_endpoints = e.endpoints
        FROM (
            SELECT peer_id,
                   json_agg(
                       json_build_object(
                           'vmid', vmid, 'name', name, 'service', service,
                           'host', host, 'port', port
                       ) ORDER BY position
                   ) AS endpoints
            FROM wireguard_peer_endpoints
            GROUP BY peer_id
        ) AS e
        WHERE e.peer_id = p.id
        """
    )
    op.alter_column("wireguard_peers", "allowed_endpoints", server_default=None)
    op.drop_index("ix_wireguard_peer_endpoints_peer_id", "wireguard_peer_endpoints")
    op.drop_table("wireguard_peer_endpoints")

    # ── subnet_config ───────────────────────────────────────────────────────
    op.add_column(
        "subnet_config", sa.Column("dns_servers", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "subnet_config", sa.Column("extra_blocked_subnets", sa.Text(), nullable=True)
    )
    op.execute(
        """
        UPDATE subnet_config c SET dns_servers = d.joined
        FROM (
            SELECT subnet_config_id, string_agg(address, ',' ORDER BY position) AS joined
            FROM subnet_dns_servers GROUP BY subnet_config_id
        ) AS d
        WHERE d.subnet_config_id = c.id
        """
    )
    op.execute(
        """
        UPDATE subnet_config c SET extra_blocked_subnets = b.joined
        FROM (
            SELECT subnet_config_id, string_agg(cidr, ',' ORDER BY position) AS joined
            FROM subnet_blocked_subnets GROUP BY subnet_config_id
        ) AS b
        WHERE b.subnet_config_id = c.id
        """
    )
    op.drop_table("subnet_blocked_subnets")
    op.drop_table("subnet_dns_servers")

    # ── teacher_judge_session_attachments.message_id ───────────────────────
    op.add_column(
        "teacher_judge_session_attachments",
        sa.Column("message_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        """
        UPDATE teacher_judge_session_attachments a SET message_id = l.message_id
        FROM teacher_judge_message_attachments l
        WHERE l.attachment_id = a.id
        """
    )
    op.create_foreign_key(
        "teacher_judge_session_attachments_message_id_fkey",
        "teacher_judge_session_attachments",
        "teacher_judge_session_messages",
        ["message_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_teacher_judge_session_attachments_message_id",
        "teacher_judge_session_attachments",
        ["message_id"],
    )
    op.drop_index(
        "ix_teacher_judge_message_attachments_message_id",
        "teacher_judge_message_attachments",
    )
    op.drop_table("teacher_judge_message_attachments")

    # ── teacher_judge_student_submissions ───────────────────────────────────
    op.add_column(
        "teacher_judge_student_submissions",
        sa.Column("teaching_class_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "teacher_judge_student_submissions",
        sa.Column("completed_item_ids", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "teacher_judge_student_submissions",
        sa.Column("is_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE teacher_judge_student_submissions s
        SET teaching_class_id = a.teaching_class_id, is_ready = s.ready_at IS NOT NULL
        FROM teacher_judge_script_artifacts a
        WHERE a.id = s.artifact_id
        """
    )
    op.execute(
        """
        UPDATE teacher_judge_student_submissions s SET completed_item_ids = i.items
        FROM (
            SELECT submission_id, json_agg(item_id ORDER BY item_id) AS items
            FROM teacher_judge_submission_items GROUP BY submission_id
        ) AS i
        WHERE i.submission_id = s.id
        """
    )
    op.alter_column(
        "teacher_judge_student_submissions", "teaching_class_id", nullable=False
    )
    op.alter_column(
        "teacher_judge_student_submissions", "completed_item_ids", server_default=None
    )
    op.alter_column(
        "teacher_judge_student_submissions", "is_ready", server_default=None
    )
    op.create_foreign_key(
        "teacher_judge_student_submissions_teaching_class_id_fkey",
        "teacher_judge_student_submissions",
        "teaching_classes",
        ["teaching_class_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_teacher_judge_student_submissions_teaching_class_id",
        "teacher_judge_student_submissions",
        ["teaching_class_id"],
    )
    op.create_index(
        "ix_teacher_judge_student_submissions_class_artifact_ready",
        "teacher_judge_student_submissions",
        ["teaching_class_id", "artifact_id", "is_ready"],
    )
    op.drop_table("teacher_judge_submission_items")

    # ── teacher_judge_script_runs.teaching_class_id ─────────────────────────
    op.add_column(
        "teacher_judge_script_runs",
        sa.Column("teaching_class_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        """
        UPDATE teacher_judge_script_runs r SET teaching_class_id = a.teaching_class_id
        FROM teacher_judge_script_artifacts a
        WHERE a.id = r.artifact_id
        """
    )
    op.alter_column("teacher_judge_script_runs", "teaching_class_id", nullable=False)
    op.create_foreign_key(
        "fk_teacher_judge_script_runs_teaching_class_id",
        "teacher_judge_script_runs",
        "teaching_classes",
        ["teaching_class_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_teacher_judge_script_runs_teaching_class_id",
        "teacher_judge_script_runs",
        ["teaching_class_id"],
    )
    op.create_index(
        "ix_teacher_judge_script_runs_class_status",
        "teacher_judge_script_runs",
        ["teaching_class_id", "status"],
    )
