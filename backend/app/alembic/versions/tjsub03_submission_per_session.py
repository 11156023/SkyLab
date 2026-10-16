"""Key student completion by the week's conversation instead of one script.

一週一個對話：學生回報改對到 teacher_judge_sessions，重新製作腳本或多台機器
各一支腳本都不會讓回報消失或分裂。

- 新增 session_id，從腳本所屬的對話補值；
- 同一位學生在同一個對話有多筆回報（不同機器、不同版本的腳本）時合併成一筆：
  保留最近更新的那筆，完成項目取聯集；
- artifact_id 改成可空、腳本刪除時清成空值（只留作歷史）；
- 唯一鍵改成（session_id, student_id）。沒有對話的舊腳本回報 session_id 為空，
  學生端本來就看不到，保留不動。

Revision ID: tjsub03
Revises: mrg12_merge_guides_tjrec
"""

import sqlalchemy as sa
from alembic import op

revision = "tjsub03"
down_revision = "mrg12_merge_guides_tjrec"
branch_labels = None
depends_on = None

_TABLE = "teacher_judge_student_submissions"
_ARTIFACT_FK = "teacher_judge_student_submissions_artifact_id_fkey"
_SESSION_FK = "fk_teacher_judge_student_submissions_session_id"
_OLD_UNIQUE = "uq_teacher_judge_student_submission_artifact_student"
_NEW_UNIQUE = "uq_teacher_judge_student_submission_session_student"

_RANKED = """
    WITH ranked AS (
        SELECT
            id,
            first_value(id) OVER (
                PARTITION BY session_id, student_id
                ORDER BY updated_at DESC, id DESC
            ) AS keep_id
        FROM teacher_judge_student_submissions
        WHERE session_id IS NOT NULL
    )
"""


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("session_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE teacher_judge_student_submissions AS s
        SET session_id = a.session_id
        FROM teacher_judge_script_artifacts AS a
        WHERE a.id = s.artifact_id
        """
    )
    # 合併：先把其他筆的完成項目搬到要保留的那筆，再刪掉其他筆
    op.execute(
        _RANKED
        + """
        INSERT INTO teacher_judge_submission_items (submission_id, item_id)
        SELECT ranked.keep_id, items.item_id
        FROM ranked
        JOIN teacher_judge_submission_items AS items ON items.submission_id = ranked.id
        WHERE ranked.id <> ranked.keep_id
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        _RANKED
        + """
        DELETE FROM teacher_judge_student_submissions AS s
        USING ranked
        WHERE s.id = ranked.id AND ranked.id <> ranked.keep_id
        """
    )

    op.drop_constraint(_OLD_UNIQUE, _TABLE, type_="unique")
    op.drop_constraint(_ARTIFACT_FK, _TABLE, type_="foreignkey")
    op.alter_column(_TABLE, "artifact_id", nullable=True)
    op.create_foreign_key(
        _ARTIFACT_FK,
        _TABLE,
        "teacher_judge_script_artifacts",
        ["artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        _SESSION_FK,
        _TABLE,
        "teacher_judge_sessions",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_teacher_judge_student_submissions_session_id", _TABLE, ["session_id"]
    )
    op.create_index(
        _NEW_UNIQUE,
        _TABLE,
        ["session_id", "student_id"],
        unique=True,
        postgresql_where=sa.text("session_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(_NEW_UNIQUE, table_name=_TABLE)
    op.drop_index("ix_teacher_judge_student_submissions_session_id", table_name=_TABLE)
    op.drop_constraint(_SESSION_FK, _TABLE, type_="foreignkey")
    # 舊結構要求每筆回報都對到一支腳本：對不到的只能刪掉
    op.execute("DELETE FROM teacher_judge_student_submissions WHERE artifact_id IS NULL")
    op.drop_constraint(_ARTIFACT_FK, _TABLE, type_="foreignkey")
    op.alter_column(_TABLE, "artifact_id", nullable=False)
    op.create_foreign_key(
        _ARTIFACT_FK,
        _TABLE,
        "teacher_judge_script_artifacts",
        ["artifact_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(_OLD_UNIQUE, _TABLE, ["artifact_id", "student_id"])
    op.drop_column(_TABLE, "session_id")
