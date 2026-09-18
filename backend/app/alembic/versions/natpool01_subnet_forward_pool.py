"""對外 port 轉發的自動配號池與入口主機。

課程環境的 port_forward 發布是「一份規格、每位學生各一份」，對外 port 不能
寫死在模板上，得在開課時逐人從池子配號。池子範圍與學生看到的入口主機放在
SubnetConfig 這個 singleton 上，跟 Gateway VM 的其他網路設定一起管理。
"""

import sqlalchemy as sa
from alembic import op

revision = "natpool01_subnet_forward_pool"
down_revision = "cepeer01_environment_peer_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subnet_config",
        sa.Column(
            "forward_port_start", sa.Integer(), nullable=False, server_default="30000"
        ),
    )
    op.add_column(
        "subnet_config",
        sa.Column(
            "forward_port_end", sa.Integer(), nullable=False, server_default="39999"
        ),
    )
    op.add_column(
        "subnet_config",
        sa.Column("forward_public_host", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subnet_config", "forward_public_host")
    op.drop_column("subnet_config", "forward_port_end")
    op.drop_column("subnet_config", "forward_port_start")
