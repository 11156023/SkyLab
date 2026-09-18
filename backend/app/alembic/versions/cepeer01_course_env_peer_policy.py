"""課程環境版本的機器互通策略。

以前「沒畫任何連線」會退回「共用網段全部互通」，畫了第一條線才變成白名單。
既有版本照當下實際行為回填：一條線都沒有的版本標成 segment，讓已開課的班級
重跑同步時不會突然把學生機器之間的連通拆掉；有線的版本本來就是白名單。
新版本一律 explicit。
"""

import sqlalchemy as sa
from alembic import op

revision = "cepeer01_environment_peer_policy"
down_revision = "cefile01_environment_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "course_environment_versions",
        sa.Column(
            "peer_policy",
            sa.String(length=16),
            nullable=False,
            server_default="explicit",
        ),
    )
    op.execute(
        """
        UPDATE course_environment_versions
        SET peer_policy = 'segment'
        WHERE NOT EXISTS (
            SELECT 1 FROM course_environment_edges
            WHERE course_environment_edges.version_id = course_environment_versions.id
        )
        """
    )


def downgrade() -> None:
    op.drop_column("course_environment_versions", "peer_policy")
