"""Merge the password-nullability and multi-machine heads.

Revision ID: mrg05_merge_pwnull_mm
Revises: mrg03_merge_osid_tjmm, mrg04_merge_vmreqpw_cefile
Create Date: 2026-09-20

``mrg04`` 收斂本地的 ``vmreqpw01_request_password_null``（vm_requests.password
可空），``mrg03`` 收斂 upstream 的多機器 / guest_os 分支；兩者互不知道對方，
``alembic upgrade head`` 會看到雙頭。

另一件事要一起處理：upstream 的 ``vmreqpw02_password_not_null`` 是在
``vmreqpw01`` 還沒進 repo 時寫的，用意是把共用 DB 拉回「password NOT NULL」。
現在 ``vmreqpw01`` 已經進 repo，model 也是 ``password: str | None``（provision
完成就清掉申請單上的密碼副本），那個假設已經失效。兩支改的是同一欄卻分屬不同
分支，套用順序不保證，因此在這個匯流點明確把欄位收斂成可空，讓乾淨 DB 與已經
跑過 ``vmreqpw02`` 的 DB 最終狀態一致，``alembic check`` 才會與 model 相符。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "mrg05_merge_pwnull_mm"
down_revision = ("mrg03_merge_osid_tjmm", "mrg04_merge_vmreqpw_cefile")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "vm_requests",
        "password",
        existing_type=sa.String(),
        nullable=True,
    )


def downgrade() -> None:
    # 回填空字串再收緊 NOT NULL（無法還原已清空的密碼）
    op.execute("UPDATE vm_requests SET password = '' WHERE password IS NULL")
    op.alter_column(
        "vm_requests",
        "password",
        existing_type=sa.String(),
        nullable=False,
    )
