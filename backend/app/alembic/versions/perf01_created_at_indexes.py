"""add created_at indexes for job and alert listings

管理員任務清單（/ws/jobs 每 3 秒、/jobs REST）對 vm_requests、
spec_change_requests、deletion_requests 下 ``created_at >= since ORDER BY
created_at DESC LIMIT``，既有複合索引都以 status／user_id 開頭用不上；
alert_events 的閾值檢查每輪查近 N 分鐘、清單依時間倒序，且這張表不會清理。

Revision ID: perf01_created_at_indexes
Revises: gauth01_google_auth_source
Create Date: 2026-09-29
"""

from alembic import op

revision = "perf01_created_at_indexes"
down_revision = "gauth01_google_auth_source"
branch_labels = None
depends_on = None

_INDEXES: list[tuple[str, str, list[str]]] = [
    ("ix_alert_events_created_at", "alert_events", ["created_at"]),
    ("ix_vm_requests_created_at", "vm_requests", ["created_at"]),
    ("ix_spec_change_requests_created_at", "spec_change_requests", ["created_at"]),
    ("ix_deletion_requests_created_at", "deletion_requests", ["created_at"]),
    ("ix_deletion_requests_user_created", "deletion_requests", ["user_id", "created_at"]),
]


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        op.create_index(name, table, columns, if_not_exists=True)


def downgrade() -> None:
    for name, table, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table, if_exists=True)
