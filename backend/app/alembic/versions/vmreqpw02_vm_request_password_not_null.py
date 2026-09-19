"""vm_requests.password 回到 NOT NULL。

這條 repo 的 migration 從建表起就宣告 password 不能空（a3b7c9d1e2f4），model 也是
``password: str``；但有 DB 曾被另一個 checkout 裡、沒進 repo 的 migration 放寬成
nullable，``alembic check`` 因此一直報不一致。把 DB 拉回 repo 說的狀態。

若 DB 裡真的有 NULL 列，Postgres 會直接拒絕（不偷塞空字串——那欄存的是加密後的
登入密碼，空值沒有意義），請先查明那些列再重跑。
"""

from alembic import op

revision = "vmreqpw02_password_not_null"
down_revision = "cepub02_publication_port_forward"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("vm_requests", "password", nullable=False)


def downgrade() -> None:
    op.alter_column("vm_requests", "password", nullable=True)
