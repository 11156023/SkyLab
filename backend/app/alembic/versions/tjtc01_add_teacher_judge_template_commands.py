"""add teacher judge template command catalog

Revision ID: tjtc01_template_commands
Revises: vmr01_normalize_vmrequest_status
Create Date: 2026-05-30 00:00:00.000000

"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "tjtc01_template_commands"
down_revision = "vmr01_normalize_vmrequest_status"
branch_labels = None
depends_on = None


COMMANDS = [
    (
        "linux",
        "linux.os_info",
        "OS 版本資訊",
        "system",
        "cat /etc/os-release",
        "讀取 Linux 發行版與版本資訊。",
    ),
    (
        "linux",
        "linux.kernel",
        "Kernel 資訊",
        "system",
        "uname -a",
        "讀取核心版本與系統架構資訊。",
    ),
    (
        "linux",
        "linux.disk_usage",
        "磁碟使用量",
        "system",
        "df -h",
        "查看檔案系統容量與磁碟使用情況。",
    ),
    (
        "linux",
        "linux.memory_usage",
        "記憶體使用量",
        "system",
        "free -m",
        "查看系統記憶體使用情況。",
    ),
    (
        "linux",
        "linux.listening_ports",
        "監聽連接埠",
        "port",
        "ss -lntp",
        "列出正在監聽的 TCP 連接埠與相關程序。",
    ),
    (
        "linux",
        "linux.processes",
        "程序清單",
        "process",
        "ps aux",
        "列出目前執行中的程序。",
    ),
    (
        "python",
        "python.version",
        "Python 版本",
        "runtime",
        "python3 --version",
        "查看 Python 直譯器版本。",
    ),
    (
        "python",
        "python.pip_list",
        "Python 套件清單",
        "runtime",
        "python3 -m pip list",
        "列出目前 Python 環境已安裝套件。",
    ),
    (
        "python",
        "python.processes",
        "Python 相關程序",
        "process",
        "ps aux | grep -E 'python|uvicorn|flask|django'",
        "查找 Python、Uvicorn、Flask 或 Django 相關程序。",
    ),
    (
        "python",
        "python.listening_ports",
        "監聽連接埠",
        "port",
        "ss -lntp",
        "列出正在監聽的 TCP 連接埠與相關程序。",
    ),
    (
        "n8n",
        "n8n.port_check",
        "n8n 連接埠檢查",
        "port",
        "ss -lntp | grep ':5678'",
        "檢查 n8n 預設 5678 連接埠是否正在監聽。",
    ),
    (
        "n8n",
        "n8n.http_check",
        "n8n HTTP 檢查",
        "service",
        "curl -I --max-time 5 http://127.0.0.1:5678",
        "檢查本機 n8n Web 服務是否有 HTTP 回應。",
    ),
    (
        "n8n",
        "n8n.process_check",
        "n8n 程序檢查",
        "process",
        "ps aux | grep -i n8n",
        "查找 n8n 相關程序。",
    ),
    (
        "n8n",
        "n8n.docker_check",
        "n8n Docker 檢查",
        "service",
        "docker ps --format '{{.Names}} {{.Status}} {{.Ports}}' | grep -i n8n",
        "查找 Docker 中是否有 n8n 相關容器與連接埠資訊。",
    ),
]


def upgrade() -> None:
    op.create_table(
        "teacher_judge_template_commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_key", sa.String(length=50), nullable=False),
        sa.Column("command_key", sa.String(length=100), nullable=False),
        sa.Column("command_label", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("command_template", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "risk_level",
            sa.String(length=30),
            nullable=False,
            server_default="read_only",
        ),
        sa.Column(
            "requires_confirmation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "template_key",
            "command_key",
            name="uq_teacher_judge_template_command_key",
        ),
    )
    op.create_index(
        op.f("ix_teacher_judge_template_commands_template_key"),
        "teacher_judge_template_commands",
        ["template_key"],
    )
    op.create_index(
        op.f("ix_teacher_judge_template_commands_enabled"),
        "teacher_judge_template_commands",
        ["enabled"],
    )

    command_table = sa.table(
        "teacher_judge_template_commands",
        sa.column("id", sa.Uuid()),
        sa.column("template_key", sa.String()),
        sa.column("command_key", sa.String()),
        sa.column("command_label", sa.String()),
        sa.column("category", sa.String()),
        sa.column("command_template", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("risk_level", sa.String()),
        sa.column("requires_confirmation", sa.Boolean()),
        sa.column("enabled", sa.Boolean()),
    )
    op.bulk_insert(
        command_table,
        [
            {
                "id": uuid.uuid4(),
                "template_key": template_key,
                "command_key": command_key,
                "command_label": command_label,
                "category": category,
                "command_template": command_template,
                "description": description,
                "risk_level": "read_only",
                "requires_confirmation": True,
                "enabled": True,
            }
            for (
                template_key,
                command_key,
                command_label,
                category,
                command_template,
                description,
            ) in COMMANDS
        ],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_teacher_judge_template_commands_enabled"),
        table_name="teacher_judge_template_commands",
    )
    op.drop_index(
        op.f("ix_teacher_judge_template_commands_template_key"),
        table_name="teacher_judge_template_commands",
    )
    op.drop_table("teacher_judge_template_commands")
