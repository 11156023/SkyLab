"""課程環境的對外服務：firewall_only 改為 port_forward。

firewall_only 產生的入站 ACCEPT 沒有 source 限制，機器全在同一個 bridge、
同一個子網、沒有 VLAN，等於對整個實驗室（包括別班學生的機器）敞開這個
port。老師宣告它的意圖是「這個 port 要連得到」，port_forward 同樣做到、
而且只從 Gateway 進來，所以既有宣告直接轉過去；已經套在學生機器上的舊規則
不在這裡動（資料面的規則清理另行處理）。
"""

from alembic import op

revision = "cepub02_publication_port_forward"
down_revision = "natpool01_subnet_forward_pool"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE course_environment_publications "
        "SET mode = 'port_forward' WHERE mode = 'firewall_only'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE course_environment_publications "
        "SET mode = 'firewall_only' WHERE mode = 'port_forward'"
    )
