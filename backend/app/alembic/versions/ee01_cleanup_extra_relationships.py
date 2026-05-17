"""cleanup extra relationships

Revision ID: ee01_cleanup_rels
Revises: ed01_resource_norm
Create Date: 2026-05-17 00:00:00.000000

"""

from alembic import op


revision = "ee01_cleanup_rels"
down_revision = "ed01_resource_norm"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE batch_provision_jobs "
        "DROP CONSTRAINT IF EXISTS fk_batch_provision_jobs_reviewer_id_user"
    )
    op.create_foreign_key(
        "fk_script_deploy_logs_user_id",
        "script_deploy_logs",
        "user",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_proxmox_storages_node_name",
        "proxmox_storages",
        "proxmox_nodes",
        ["node_name"],
        ["name"],
        ondelete="CASCADE",
    )


def downgrade():
    op.drop_constraint(
        "fk_proxmox_storages_node_name",
        "proxmox_storages",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_script_deploy_logs_user_id",
        "script_deploy_logs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_batch_provision_jobs_reviewer_id_user",
        "batch_provision_jobs",
        "user",
        ["reviewer_id"],
        ["id"],
        ondelete="SET NULL",
    )
