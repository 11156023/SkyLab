"""移除快速練習開放對象、user.created_at 改 NOT NULL、回收外洩的 IP。

- course_environments.audience 與 course_environment_audiences：開放對象的
  介面早已移除，quick_practice 也不再用它判斷（提供為快速練習就是全校可見），
  只剩 API 還在寫、沒有人讀。
- user.created_at：一律由程式填入，資料庫卻允許 NULL。
- ip_allocation：資源刪除流程中，清理批次任務參照失敗時的 session.rollback()
  會把已 flush 的 IP 釋放一起撤銷，留下 vmid 已不在 resources 的配發列，
  佔著 IP、也讓 next_vmid 永遠跳過那些 VMID。只回收一天以前配發、非班級預留
  的 VM/LXC 列，避免動到正在建立中的機器（IP 會先於 resources 列寫入）。

Revision ID: dbw01_drop_audience_cleanup
Revises: dbm06_text_json_columns
Create Date: 2026-09-27
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "dbw01_drop_audience_cleanup"
down_revision = "dbm06_text_json_columns"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    inspector = _inspector()
    if "course_environment_audiences" in inspector.get_table_names():
        op.drop_table("course_environment_audiences")
    if "audience" in {c["name"] for c in inspector.get_columns("course_environments")}:
        op.drop_column("course_environments", "audience")

    op.execute('UPDATE "user" SET created_at = now() WHERE created_at IS NULL')
    op.alter_column("user", "created_at", nullable=False)

    leaked = (
        op.get_bind()
        .execute(
            sa.text(
                """
                DELETE FROM ip_allocation a
                WHERE a.vmid IS NOT NULL
                  AND a.reservation_key IS NULL
                  AND a.purpose IN ('vm', 'lxc')
                  AND a.allocated_at < now() - interval '1 day'
                  AND NOT EXISTS (SELECT 1 FROM resources r WHERE r.vmid = a.vmid)
                RETURNING a.ip_address, a.vmid
                """
            )
        )
        .fetchall()
    )
    if leaked:
        logger.warning(
            "回收 %d 筆已無資源的 IP 配發：%s",
            len(leaked),
            ", ".join(f"{ip}(VMID {vmid})" for ip, vmid in leaked),
        )


def downgrade() -> None:
    # 回收的 IP 配發無法還原（原本就是外洩資料）
    op.alter_column("user", "created_at", nullable=True)

    inspector = _inspector()
    if "audience" not in {c["name"] for c in inspector.get_columns("course_environments")}:
        op.add_column(
            "course_environments",
            sa.Column(
                "audience", sa.String(length=24), nullable=False, server_default="campus"
            ),
        )
    if "course_environment_audiences" not in inspector.get_table_names():
        op.create_table(
            "course_environment_audiences",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "environment_id",
                sa.Uuid(),
                sa.ForeignKey("course_environments.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "class_id",
                sa.Uuid(),
                sa.ForeignKey("teaching_classes.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "environment_id", "class_id", name="uq_course_environment_audience"
            ),
        )
