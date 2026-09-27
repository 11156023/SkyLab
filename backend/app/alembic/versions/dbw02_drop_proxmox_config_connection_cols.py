"""proxmox_config 移除舊版單連線欄位，只保留放置／排程策略。

多連線改版（pmc01）後，PVE 連線的帳密、SSL、pool、storage、gateway 由
proxmox_connections 管理；proxmox_config 的同名欄位只剩「一筆連線都沒有時」
的相容退路，而那條退路只會在刪光連線後把舊帳密撈回來用。

升級步驟：
1. 若還沒有任何連線、而 proxmox_config 存著有效帳密（host 與密碼非空），
   先轉成預設連線，保留現行可用的設定（同 pmc01，但一併帶上 pool / storage /
   gateway 等叢集設定）。
2. 只有一個連線時，把尚未歸屬的節點與資源回填到該連線（舊 /sync-now 退路
   建出來的節點 connection_id 為 NULL，拿掉退路後會路由不到）。
3. 刪除 13 個連線欄位。

Revision ID: dbw02_drop_pve_config_conn_cols
Revises: dbw01_drop_audience_cleanup
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "dbw02_drop_pve_config_conn_cols"
down_revision = "dbw01_drop_audience_cleanup"
branch_labels = None
depends_on = None


_LEGACY_COLUMNS = (
    "host",
    "user",
    "encrypted_password",
    "verify_ssl",
    "iso_storage",
    "data_storage",
    "api_timeout",
    "task_check_interval",
    "pool_name",
    "ca_cert",
    "gateway_ip",
    "local_subnet",
    "default_node",
)


def _config_columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("proxmox_config")}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _config_columns()

    if {"host", "encrypted_password"} <= columns:
        has_connection = bind.execute(
            sa.text("SELECT 1 FROM proxmox_connections LIMIT 1")
        ).fetchone()
        config = bind.execute(
            sa.text(
                'SELECT host, "user", encrypted_password, verify_ssl, ca_cert, '
                "api_timeout, pool_name, iso_storage, data_storage, "
                "task_check_interval, gateway_ip, local_subnet, default_node "
                "FROM proxmox_config WHERE id = 1"
            )
        ).fetchone()
        if (
            has_connection is None
            and config is not None
            and (config.host or "").strip()
            and (config.encrypted_password or "").strip()
        ):
            bind.execute(
                sa.text(
                    "INSERT INTO proxmox_connections "
                    '(name, host, port, "user", encrypted_password, verify_ssl, '
                    " ca_cert, api_timeout, pool_name, iso_storage, data_storage, "
                    " task_check_interval, gateway_ip, local_subnet, default_node, "
                    " enabled, is_default, created_at, updated_at) "
                    "VALUES (:host, :host, 8006, :user, :encrypted_password, "
                    " :verify_ssl, :ca_cert, :api_timeout, :pool_name, :iso_storage, "
                    " :data_storage, :task_check_interval, :gateway_ip, "
                    " :local_subnet, :default_node, true, true, now(), now())"
                ),
                dict(config._mapping),
            )

    connection_ids = [
        row[0]
        for row in bind.execute(
            sa.text("SELECT id FROM proxmox_connections")
        ).fetchall()
    ]
    if len(connection_ids) == 1:
        params = {"cid": connection_ids[0]}
        bind.execute(
            sa.text(
                "UPDATE proxmox_nodes SET connection_id = :cid "
                "WHERE connection_id IS NULL"
            ),
            params,
        )
        bind.execute(
            sa.text(
                "UPDATE resources SET connection_id = :cid WHERE connection_id IS NULL"
            ),
            params,
        )

    for column in _LEGACY_COLUMNS:
        if column in columns:
            op.drop_column("proxmox_config", column)


def downgrade() -> None:
    columns = _config_columns()
    specs: list[sa.Column] = [
        sa.Column("host", sa.String(255), nullable=False, server_default=""),
        sa.Column("user", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "encrypted_password", sa.String(2048), nullable=False, server_default=""
        ),
        sa.Column(
            "verify_ssl", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "iso_storage", sa.String(255), nullable=False, server_default="local"
        ),
        sa.Column(
            "data_storage", sa.String(255), nullable=False, server_default="local-lvm"
        ),
        sa.Column("api_timeout", sa.Integer(), nullable=False, server_default="30"),
        sa.Column(
            "task_check_interval", sa.Integer(), nullable=False, server_default="2"
        ),
        sa.Column("pool_name", sa.String(255), nullable=False, server_default="SkyLab"),
        sa.Column("ca_cert", sa.Text(), nullable=True),
        sa.Column("gateway_ip", sa.String(255), nullable=True),
        sa.Column("local_subnet", sa.String(50), nullable=True),
        sa.Column("default_node", sa.String(255), nullable=True),
    ]
    for column in specs:
        if column.name not in columns:
            op.add_column("proxmox_config", column)
    # 以預設連線回填，讓舊版程式的相容退路仍有值可用
    op.execute(
        """
        UPDATE proxmox_config c SET
            host = p.host, "user" = p."user", encrypted_password = p.encrypted_password,
            verify_ssl = p.verify_ssl, ca_cert = p.ca_cert, api_timeout = p.api_timeout,
            pool_name = p.pool_name, iso_storage = p.iso_storage,
            data_storage = p.data_storage, task_check_interval = p.task_check_interval,
            gateway_ip = p.gateway_ip, local_subnet = p.local_subnet,
            default_node = p.default_node
        FROM proxmox_connections p
        WHERE c.id = 1 AND p.is_default
        """
    )
