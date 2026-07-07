"""add vm templates and task records

Revision ID: vmt01_vm_templates
Revises: tjfl01_teacher_judge_files
Create Date: 2026-07-04 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "vmt01_vm_templates"
down_revision = "tjfl01_teacher_judge_files"
branch_labels = None
depends_on = None


template_status_enum = postgresql.ENUM(
    "creating",
    "ready",
    "updating",
    "failed",
    "deleted",
    name="vmtemplatestatus",
    create_type=False,
)

template_visibility_enum = postgresql.ENUM(
    "global",
    "groups",
    name="vmtemplatevisibility",
    create_type=False,
)

task_status_enum = postgresql.ENUM(
    "queued",
    "running",
    "succeeded",
    "failed",
    name="taskrecordstatus",
    create_type=False,
)


def _table_exists(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _index_exists(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _create_index_if_missing(
    index_name: str,
    table_name: str,
    columns: list[str],
    unique: bool = False,
) -> None:
    if not _index_exists(table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    template_status_enum.create(bind, checkfirst=True)
    template_visibility_enum.create(bind, checkfirst=True)
    task_status_enum.create(bind, checkfirst=True)

    if not _table_exists("vm_templates"):
        op.create_table(
            "vm_templates",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("pve_vmid", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.String(length=1000), nullable=True),
            sa.Column("owner_id", sa.Uuid(), nullable=True),
            sa.Column("node", sa.String(length=63), nullable=False),
            sa.Column("storage", sa.String(length=128), nullable=True),
            sa.Column(
                "resource_type",
                sa.String(length=10),
                nullable=False,
                server_default="qemu",
            ),
            sa.Column(
                "status",
                template_status_enum,
                nullable=False,
                server_default="creating",
            ),
            sa.Column(
                "visibility",
                template_visibility_enum,
                nullable=False,
                server_default="groups",
            ),
            sa.Column("default_cores", sa.Integer(), nullable=True),
            sa.Column("default_memory", sa.Integer(), nullable=True),
            sa.Column("default_disk", sa.Integer(), nullable=True),
            sa.Column("source_vmid", sa.Integer(), nullable=True),
            sa.Column(
                "version", sa.Integer(), nullable=False, server_default="1"
            ),
            sa.Column("error_message", sa.String(length=1000), nullable=True),
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
            sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(
        op.f("ix_vm_templates_pve_vmid"), "vm_templates", ["pve_vmid"], unique=True
    )
    _create_index_if_missing(
        op.f("ix_vm_templates_owner_id"), "vm_templates", ["owner_id"]
    )
    _create_index_if_missing(
        "ix_vm_templates_status_visibility",
        "vm_templates",
        ["status", "visibility"],
    )

    if not _table_exists("vm_template_group_links"):
        op.create_table(
            "vm_template_group_links",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("template_id", sa.Uuid(), nullable=False),
            sa.Column("group_id", sa.Uuid(), nullable=False),
            sa.ForeignKeyConstraint(
                ["template_id"], ["vm_templates.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["group_id"], ["group.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "template_id", "group_id", name="uq_vm_template_group_links"
            ),
        )
    _create_index_if_missing(
        op.f("ix_vm_template_group_links_template_id"),
        "vm_template_group_links",
        ["template_id"],
    )
    _create_index_if_missing(
        op.f("ix_vm_template_group_links_group_id"),
        "vm_template_group_links",
        ["group_id"],
    )

    if not _table_exists("task_records"):
        op.create_table(
            "task_records",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("task_type", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("template_id", sa.Uuid(), nullable=True),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column(
                "status",
                task_status_enum,
                nullable=False,
                server_default="queued",
            ),
            sa.Column(
                "progress", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("error", sa.String(length=1000), nullable=True),
            sa.Column("resource_vmid", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["template_id"], ["vm_templates.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(
        op.f("ix_task_records_task_type"), "task_records", ["task_type"]
    )
    _create_index_if_missing(
        op.f("ix_task_records_user_id"), "task_records", ["user_id"]
    )
    _create_index_if_missing(
        op.f("ix_task_records_template_id"), "task_records", ["template_id"]
    )
    _create_index_if_missing(
        op.f("ix_task_records_created_at"), "task_records", ["created_at"]
    )
    _create_index_if_missing(
        "ix_task_records_user_created", "task_records", ["user_id", "created_at"]
    )


def downgrade() -> None:
    if _table_exists("task_records"):
        for index_name in (
            "ix_task_records_user_created",
            op.f("ix_task_records_created_at"),
            op.f("ix_task_records_template_id"),
            op.f("ix_task_records_user_id"),
            op.f("ix_task_records_task_type"),
        ):
            if _index_exists("task_records", index_name):
                op.drop_index(index_name, table_name="task_records")
        op.drop_table("task_records")

    if _table_exists("vm_template_group_links"):
        for index_name in (
            op.f("ix_vm_template_group_links_group_id"),
            op.f("ix_vm_template_group_links_template_id"),
        ):
            if _index_exists("vm_template_group_links", index_name):
                op.drop_index(index_name, table_name="vm_template_group_links")
        op.drop_table("vm_template_group_links")

    if _table_exists("vm_templates"):
        for index_name in (
            "ix_vm_templates_status_visibility",
            op.f("ix_vm_templates_owner_id"),
            op.f("ix_vm_templates_pve_vmid"),
        ):
            if _index_exists("vm_templates", index_name):
                op.drop_index(index_name, table_name="vm_templates")
        op.drop_table("vm_templates")

    task_status_enum.drop(op.get_bind(), checkfirst=True)
    template_visibility_enum.drop(op.get_bind(), checkfirst=True)
    template_status_enum.drop(op.get_bind(), checkfirst=True)
