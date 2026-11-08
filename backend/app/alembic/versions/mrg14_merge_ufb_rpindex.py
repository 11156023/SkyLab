"""Merge user-feedback and search-indexing migrations."""

revision = "mrg14_merge_ufb_rpindex"
down_revision = ("ufb01_user_feedback", "rpindex01_allow_indexing")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
