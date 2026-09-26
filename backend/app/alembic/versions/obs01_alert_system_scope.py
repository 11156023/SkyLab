"""alert_events 新增平台健康告警：alertscope 加 system、alertmetric 加 health。

排程任務連續失敗、背景迴圈停擺、worker／Redis／PVE 連線中斷時，由
``system_health_service.process_system_health_alerts`` 寫入 scope=system 的
AlertEvent，沿用既有的告警清單、ack 與 Email 通知。

PostgreSQL cannot drop enum values, so downgrade is a no-op.

Revision ID: obs01_alert_system_scope
Revises: totp02_user_totp_required
Create Date: 2026-09-25
"""

from __future__ import annotations

from alembic import op

revision = "obs01_alert_system_scope"
down_revision = "totp02_user_totp_required"
branch_labels = None
depends_on = None

_VALUES = (
    ("alertscope", "system"),
    ("alertmetric", "health"),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        for enum_name, value in _VALUES:
            op.execute(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # PostgreSQL has no DROP VALUE; leaving the labels in place is harmless.
    pass
