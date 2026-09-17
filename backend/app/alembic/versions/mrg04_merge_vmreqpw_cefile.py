"""Merge the two heads left by the upstream merge on 2026-09-17.

``vmreqpw01_request_password_null`` (local: vm_requests.password nullable) and
``cefile01_environment_files`` (upstream course-environment drafts/files) both
descend from ``cexp01_resource_class_exposures`` and neither knows about the
other, so ``alembic upgrade head`` refuses to run with "multiple heads". This
revision carries no schema change; it only joins the two branches.

Revision ID: mrg04_merge_vmreqpw_cefile
Revises: vmreqpw01_request_password_null, cefile01_environment_files
Create Date: 2026-09-17
"""

from __future__ import annotations

revision = "mrg04_merge_vmreqpw_cefile"
down_revision = ("vmreqpw01_request_password_null", "cefile01_environment_files")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
