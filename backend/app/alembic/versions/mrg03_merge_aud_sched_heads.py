"""Merge the two heads left by the upstream merge on 2026-09-09.

``aud01_sync_auditaction_labels`` (local audit enum sync) and
``sched01_drop_dead_placement`` (upstream placement cleanup) both descend from
``mrg02_merge_gov_tjsum_heads`` and neither knows about the other, so
``alembic upgrade head`` refuses to run with "multiple heads". This revision
carries no schema change; it only joins the two branches.

Revision ID: mrg03_merge_aud_sched_heads
Revises: aud01_sync_auditaction_labels, sched01_drop_dead_placement
Create Date: 2026-09-09
"""

from __future__ import annotations

revision = "mrg03_merge_aud_sched_heads"
down_revision = ("aud01_sync_auditaction_labels", "sched01_drop_dead_placement")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
