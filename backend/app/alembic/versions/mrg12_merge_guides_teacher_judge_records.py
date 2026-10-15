"""Merge page-guide and Teacher Judge execution-record migrations."""

revision = "mrg12_merge_guides_tjrec"
down_revision = ("mrg11_merge_guides_alerts", "tjrec01")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
