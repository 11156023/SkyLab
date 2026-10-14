"""Merge account guide history with upstream alert and registration migrations."""

revision = "mrg11_merge_guides_alerts"
down_revision = ("guide01_account_seen_guides", "mrg10_merge_alertconn_regpolicy")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
