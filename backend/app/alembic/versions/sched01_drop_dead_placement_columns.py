"""drop dead placement columns from proxmox_config

Revision ID: sched01_drop_dead_placement
Revises: cepub01_env_publications
Create Date: 2026-09-09 00:00:00.000000

放置引擎實際讀取的參數在 domain/placement/policy.py 的 get_placement_tuning，
這三欄不在其中：

* ``placement_strategy`` —— get_placement_strategy() 一律回傳
  DEFAULT_PLACEMENT_STRATEGY，寫入端也硬寫同一個常數，DB 值從未被讀取。
  落點紀錄用的是 vm_requests.placement_strategy_used，不受影響。
* ``placement_search_max_reassignments`` / ``placement_search_depth`` ——
  ret01 移除 VM 搬遷功能時，把 rebalance_search_* 改名保留下來的殘留欄位，
  全庫沒有任何讀取點。

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sched01_drop_dead_placement"
down_revision = "cepub01_env_publications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("proxmox_config", "placement_strategy")
    op.drop_column("proxmox_config", "placement_search_max_reassignments")
    op.drop_column("proxmox_config", "placement_search_depth")


def downgrade() -> None:
    # 還原成 ret01 之後、本次移除之前的樣子（NOT NULL 需 server_default）
    op.add_column(
        "proxmox_config",
        sa.Column(
            "placement_search_depth",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
    )
    op.add_column(
        "proxmox_config",
        sa.Column(
            "placement_search_max_reassignments",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("2"),
        ),
    )
    op.add_column(
        "proxmox_config",
        sa.Column(
            "placement_strategy",
            sa.String(length=64),
            nullable=False,
            server_default="priority_dominant_share",
        ),
    )
