"""一次性清理：讓 AI導師檢查符合「一週一個對話」。

會清掉兩種對話（teacher_judge_sessions）：
- 沒有掛週次的對話；
- 同一週有兩個以上對話的週次——那一週的對話全部刪除，由老師重新建立。

刪除走跟網頁上「刪除檢查」同一段邏輯（delete_session_data），檢查表、附件、
訊息、腳本與執行紀錄一併清掉，磁碟上的檔案也會刪；學生回報隨腳本連帶刪除。

預設只列出會刪什麼，加 --apply 才真的刪除。先在 backend 容器內乾跑：

    docker compose exec backend uv run python app/cleanup_judge_sessions.py
    docker compose exec backend uv run python app/cleanup_judge_sessions.py --class <班級 id>
    docker compose exec backend uv run python app/cleanup_judge_sessions.py --apply

加上「一週一個對話」限制的 migration 會先檢查這兩種資料；還有殘留就停下來，
請先跑完這支指令。
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.ai.teacher_judge.session_service import delete_session_data
from app.core.db import engine
from app.models import TeachingClass, TeachingClassWeek
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import TeacherJudgeScriptRun
from app.models.teacher_judge_session import TeacherJudgeSession
from app.models.teacher_judge_student_submission import TeacherJudgeStudentSubmission

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

REASON_NO_WEEK = "no_week"
REASON_DUPLICATE_WEEK = "duplicate_week"
_REASON_LABELS = {
    REASON_NO_WEEK: "沒有週次",
    REASON_DUPLICATE_WEEK: "同週多個對話",
}


@dataclass(frozen=True)
class CleanupTarget:
    session_id: uuid.UUID
    reason: str
    class_name: str
    week_number: int | None
    title: str
    scripts: int
    approved_scripts: int
    runs: int
    submissions: int


def _count(session: Session, statement) -> int:
    return int(session.exec(statement).one() or 0)


def find_targets(
    session: Session, *, class_id: uuid.UUID | None = None
) -> list[CleanupTarget]:
    """列出違反「一週一個對話」的對話，附上刪除後會一起消失的資料量。"""

    query = select(TeacherJudgeSession)
    if class_id is not None:
        query = query.where(TeacherJudgeSession.teaching_class_id == class_id)
    rows = list(session.exec(query).all())
    week_counts = Counter(
        row.teaching_class_week_id for row in rows if row.teaching_class_week_id
    )

    targets: list[CleanupTarget] = []
    for row in rows:
        if row.teaching_class_week_id is None:
            reason = REASON_NO_WEEK
        elif week_counts[row.teaching_class_week_id] > 1:
            reason = REASON_DUPLICATE_WEEK
        else:
            continue
        teaching_class = session.get(TeachingClass, row.teaching_class_id)
        week = (
            session.get(TeachingClassWeek, row.teaching_class_week_id)
            if row.teaching_class_week_id
            else None
        )
        artifact_ids = select(TeacherJudgeScriptArtifact.id).where(
            TeacherJudgeScriptArtifact.session_id == row.id
        )
        targets.append(
            CleanupTarget(
                session_id=row.id,
                reason=reason,
                class_name=teaching_class.name if teaching_class else "",
                week_number=week.week_number if week else None,
                title=row.title,
                scripts=_count(
                    session,
                    select(func.count()).select_from(artifact_ids.subquery()),
                ),
                approved_scripts=_count(
                    session,
                    select(func.count())
                    .select_from(TeacherJudgeScriptArtifact)
                    .where(
                        TeacherJudgeScriptArtifact.session_id == row.id,
                        TeacherJudgeScriptArtifact.status
                        == TeacherJudgeScriptStatus.approved,
                    ),
                ),
                runs=_count(
                    session,
                    select(func.count())
                    .select_from(TeacherJudgeScriptRun)
                    .where(col(TeacherJudgeScriptRun.artifact_id).in_(artifact_ids)),
                ),
                submissions=_count(
                    session,
                    select(func.count())
                    .select_from(TeacherJudgeStudentSubmission)
                    .where(
                        col(TeacherJudgeStudentSubmission.artifact_id).in_(artifact_ids)
                    ),
                ),
            )
        )
    return sorted(
        targets,
        key=lambda item: (item.class_name, item.week_number or 0, item.title),
    )


def _describe(target: CleanupTarget) -> str:
    week = f"第 {target.week_number} 週" if target.week_number else "未指定週次"
    return (
        f"[{_REASON_LABELS[target.reason]}] {target.class_name}｜{week}｜{target.title}"
        f"（腳本 {target.scripts}，已核准 {target.approved_scripts}，"
        f"執行紀錄 {target.runs}，學生回報 {target.submissions}）"
        f"  id={target.session_id}"
    )


def cleanup(*, class_id: uuid.UUID | None = None, apply: bool = False) -> int:
    with Session(engine) as session:
        targets = find_targets(session, class_id=class_id)
    if not targets:
        logger.info("沒有需要清理的對話。")
        return 0

    for target in targets:
        logger.info(_describe(target))
    logger.info("共 %d 個對話。", len(targets))
    if not apply:
        logger.info("這是乾跑，沒有刪除任何資料；確認後加上 --apply 執行。")
        return 0

    failed = 0
    for target in targets:
        # 每個對話各自一個交易：某一個刪不掉不影響其他對話
        with Session(engine) as session:
            item = session.get(TeacherJudgeSession, target.session_id)
            if item is None:
                continue
            try:
                delete_session_data(session, item)
            except Exception as exc:  # noqa: BLE001 - 記下來繼續處理下一個
                failed += 1
                logger.error("刪除失敗 %s：%s", target.session_id, exc)
    logger.info("已刪除 %d 個對話，失敗 %d 個。", len(targets) - failed, failed)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="清理沒有週次、或同週有多個對話的 AI導師檢查對話。"
    )
    parser.add_argument("--class", dest="class_id", type=uuid.UUID, help="只處理這個班級")
    parser.add_argument("--apply", action="store_true", help="實際刪除（預設只列出）")
    args = parser.parse_args(argv)
    return cleanup(class_id=args.class_id, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
