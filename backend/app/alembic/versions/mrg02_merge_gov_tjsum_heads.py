"""Merge governance and Teacher Judge summary migration heads.

The parent revisions already contain their schema changes.  This revision
only joins the two branches so ``alembic upgrade head`` has one deterministic
target.
"""

from __future__ import annotations

revision = "mrg02_merge_gov_tjsum_heads"
down_revision = (
    "gov06_idle_notify_after",
    "tjsum01_summary_boundaries",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
