"""One AI-check conversation per week, and weeks with a conversation can't be deleted.

- 唯一鍵：同一週最多一個對話（週次為空的舊資料不受限，由清理指令處理）；
- 週次外鍵改成 NO ACTION：週次還有對話就刪不掉。不用 RESTRICT——那會在
  刪除整個班級、週次與對話一起連帶刪除時，因為檢查時機太早而失敗。

同一週還有多個對話時無法建立唯一鍵，這裡先檢查並停下來，不會自動刪任何資料；
請先在 backend 容器內執行清理指令：

    uv run python app/cleanup_judge_sessions.py            # 乾跑，列出會刪什麼
    uv run python app/cleanup_judge_sessions.py --apply    # 實際刪除

Revision ID: tjweek01
Revises: tjsub03
"""

import sqlalchemy as sa
from alembic import op

revision = "tjweek01"
down_revision = "tjsub03"
branch_labels = None
depends_on = None

_TABLE = "teacher_judge_sessions"
_WEEK_FK = "fk_teacher_judge_sessions_week_id"
_OLD_INDEX = "ix_teacher_judge_sessions_teaching_class_week_id"
_UNIQUE = "uq_teacher_judge_sessions_week"


def upgrade() -> None:
    duplicated_weeks = op.get_bind().execute(
        sa.text(
            """
            SELECT count(*) FROM (
                SELECT teaching_class_week_id
                FROM teacher_judge_sessions
                WHERE teaching_class_week_id IS NOT NULL
                GROUP BY teaching_class_week_id
                HAVING count(*) > 1
            ) AS duplicated
            """
        )
    ).scalar_one()
    if duplicated_weeks:
        raise RuntimeError(
            f"{duplicated_weeks} 個週次有多個 AI導師檢查對話，無法套用「一週一個對話」。"
            "請先 `alembic upgrade tjsub03`，再執行 `python app/cleanup_judge_sessions.py --apply`，"
            "最後 `alembic upgrade heads`。"
        )

    op.drop_index(_OLD_INDEX, table_name=_TABLE)
    op.create_index(
        _UNIQUE,
        _TABLE,
        ["teaching_class_week_id"],
        unique=True,
        postgresql_where=sa.text("teaching_class_week_id IS NOT NULL"),
    )
    op.drop_constraint(_WEEK_FK, _TABLE, type_="foreignkey")
    op.create_foreign_key(
        _WEEK_FK, _TABLE, "teaching_class_weeks", ["teaching_class_week_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint(_WEEK_FK, _TABLE, type_="foreignkey")
    op.create_foreign_key(
        _WEEK_FK,
        _TABLE,
        "teaching_class_weeks",
        ["teaching_class_week_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_index(_UNIQUE, table_name=_TABLE)
    op.create_index(_OLD_INDEX, _TABLE, ["teaching_class_week_id"])
