"""Merge the guest_os and multi-machine migration heads.

Revision ID: mrg03_merge_osid_tjmm
Revises: osid01_resource_guest_os, tjmm01_teacher_judge_mm_sets
Create Date: 2026-09-20

main 新增 ``osid01_resource_guest_os``（resources.guest_os），
多機器處理分支新增 ``tjmm01_teacher_judge_mm_sets``
（artifacts.artifact_set_id/target_node_key/source_analysis_revision +
runs.run_batch_id），兩者 down_revision 皆為
(tjtn01_teacher_judge_node_scope, vmreqpw02_password_not_null)，
合併後 ``alembic upgrade head`` 會看到雙頭。
兩邊已各自包含 schema 變更，本 revision 僅收斂版本圖，
無 DDL，upgrade/downgrade 皆為 no-op。
"""

from __future__ import annotations

revision = "mrg03_merge_osid_tjmm"
down_revision = ("osid01_resource_guest_os", "tjmm01_teacher_judge_mm_sets")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
