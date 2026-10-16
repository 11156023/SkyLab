"""Student-safe projection of approved Teacher Judge assignments.

Teacher Judge artifacts and Course Lab paths are both linked to one teaching class.
A student can only see approved artifacts from the exact class linked to the path.

Only the rubric requirements are exposed.  Generated scripts, command keys,
detection methods, fallbacks, policy reviews, and other students' results stay
server-side.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, delete, desc, select

from app.ai.teacher_judge import file_service
from app.ai.teacher_judge.script_artifact_service import active_set_children
from app.ai.teacher_judge.script_policy import aggregate_check_status
from app.core.i18n import t
from app.exceptions import NotFoundError
from app.models import User
from app.models.base import get_datetime_utc
from app.models.teacher_judge_file import TeacherJudgeFile
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
)
from app.models.teacher_judge_script_run import TeacherJudgeScriptRun
from app.models.teacher_judge_session import TeacherJudgeSession
from app.models.teacher_judge_student_reply import TeacherJudgeStudentReply
from app.models.teacher_judge_student_submission import (
    TeacherJudgeStudentSubmission,
    TeacherJudgeSubmissionItem,
)
from app.models.teaching_class import (
    PUBLISHED_WEEK_STATUSES,
    TeachingClassMachineNode,
    TeachingClassStudent,
    TeachingClassWeek,
)
from app.schemas.course import (
    CourseAIAssignmentStudent,
    CourseAICheckItemStudent,
    CourseAICheckStudent,
    CourseAICompletionStudent,
    CourseAISourceDocumentStudent,
    CourseAITaskItemStudent,
)
from app.services.course import course_service

_DETECTABLE_VALUES = {"auto", "partial", "manual"}


def _student_submission(
    session: Session,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> TeacherJudgeStudentSubmission | None:
    """學生對這一週對話的回報；重新製作腳本、多台機器都還是同一筆。"""
    return session.exec(
        select(TeacherJudgeStudentSubmission).where(
            TeacherJudgeStudentSubmission.session_id == session_id,
            TeacherJudgeStudentSubmission.student_id == user_id,
        )
    ).first()


def _submission_item_ids(
    session: Session,
    submission: TeacherJudgeStudentSubmission | None,
) -> set[str]:
    if submission is None:
        return set()
    return set(
        session.exec(
            select(TeacherJudgeSubmissionItem.item_id).where(
                TeacherJudgeSubmissionItem.submission_id == submission.id
            )
        ).all()
    )


def _completion_to_student(
    session: Session,
    submission: TeacherJudgeStudentSubmission | None,
    *,
    item_ids: list[str],
) -> CourseAICompletionStudent:
    completed = _submission_item_ids(session, submission)
    completed_item_ids = [item_id for item_id in item_ids if item_id in completed]
    all_completed = bool(item_ids) and len(completed_item_ids) == len(item_ids)
    return CourseAICompletionStudent(
        completed=all_completed,
        completed_item_ids=completed_item_ids,
        ready_at=(
            submission.ready_at
            if submission and all_completed
            else None
        ),
    )


def _requested_item_id(run: TeacherJudgeScriptRun) -> str | None:
    value = (run.target_snapshot_json or {}).get("requested_item_id")
    return str(value) if value else None


def _target_for_student(
    run: TeacherJudgeScriptRun, user_id: uuid.UUID
) -> dict[str, Any]:
    """Return only the result target owned by the requested student."""

    raw_targets = (run.results_document or {}).get("targets")
    if isinstance(raw_targets, list):
        for raw_target in raw_targets:
            if not isinstance(raw_target, dict):
                continue
            user = raw_target.get("user")
            # 產生端（script_run_service / script_executor_service）寫的是 "id"；
            # 相容更早期可能寫成 "user_id" 的快照
            target_user_id = (
                (user.get("id") or user.get("user_id")) if isinstance(user, dict) else None
            )
            if target_user_id and str(target_user_id) == str(user_id):
                return raw_target
    # 舊資料相容：學生自己發起、只有一個 target 且沒有 user 快照的 run，
    # 那唯一的 target 一定是本人的。多 target 的 run 不可拿 started_by 當代理，
    # 否則 targets[0]（別的同學的結果）會回給發起者。
    if (
        run.started_by == user_id
        and isinstance(raw_targets, list)
        and len(raw_targets) == 1
        and isinstance(raw_targets[0], dict)
        and not isinstance(raw_targets[0].get("user"), dict)
    ):
        return raw_targets[0]
    return {}


def _teacher_review(target: dict[str, Any]) -> tuple[str, dict[str, str]]:
    raw_review = target.get("teacher_review")
    if not isinstance(raw_review, dict):
        return "", {}
    raw_decisions = raw_review.get("decisions")
    decisions = (
        {
            str(check_id): str(decision)
            for check_id, decision in raw_decisions.items()
            if decision in {"pass", "fail"}
        }
        if isinstance(raw_decisions, dict)
        else {}
    )
    return str(raw_review.get("feedback") or ""), decisions


def _coverage_mappings(
    artifact: TeacherJudgeScriptArtifact | None,
) -> list[dict[str, Any]]:
    if artifact is None:
        return []
    coverage = (artifact.policy_check_result_json or {}).get("coverage")
    mappings = coverage.get("mappings") if isinstance(coverage, dict) else None
    if not isinstance(mappings, list):
        return []
    return [
        mapping
        for mapping in mappings
        if isinstance(mapping, dict)
        and isinstance(mapping.get("check_id"), str)
        and isinstance(mapping.get("rubric_item_ids"), list)
    ]


def _script_checks(
    target: dict[str, Any],
    *,
    artifact: TeacherJudgeScriptArtifact | None,
    item_id: str | None,
) -> list[CourseAICheckItemStudent]:
    parsed_result = target.get("parsed_result")
    raw_checks = parsed_result.get("checks") if isinstance(parsed_result, dict) else None
    if not isinstance(raw_checks, list):
        return []

    execution_failed = target.get("status") in {"failed", "cancelled"} or (target.get("validation") or {}).get("valid") is False

    _, teacher_decisions = _teacher_review(target)

    check_by_id: dict[str, dict[str, Any]] = {}
    for raw in raw_checks:
        if not isinstance(raw, dict):
            continue
        check_id = str(raw.get("id") or "").strip()
        if check_id:
            check_by_id[check_id] = raw

    if item_id:
        mapped_ids: list[str] = []
        for mapping in _coverage_mappings(artifact):
            if item_id not in {str(value) for value in mapping["rubric_item_ids"]}:
                continue
            check_id = str(mapping["check_id"])
            if check_id not in mapped_ids:
                mapped_ids.append(check_id)
        if not mapped_ids and item_id in check_by_id:
            mapped_ids = [item_id]
        selected = [
            check_by_id[check_id]
            for check_id in mapped_ids
            if check_id in check_by_id
        ]
        if not mapped_ids:
            return []
        incomplete = len(selected) != len(mapped_ids)
        rubric_title = ""
        if artifact is not None:
            for rubric_item in (artifact.rubric_snapshot_json or {}).get("items", []):
                if isinstance(rubric_item, dict) and str(rubric_item.get("id") or "") == item_id:
                    rubric_title = str(rubric_item.get("title") or "")
                    break
        statuses = [
            teacher_decisions.get(
                str(check.get("id") or ""), str(check.get("status") or "unknown")
            )
            for check in selected
        ]
        status = aggregate_check_status(statuses, incomplete=incomplete or execution_failed)
        evidence = "\n".join(
            str(check.get("evidence") or "").strip()
            for check in selected
            if str(check.get("evidence") or "").strip()
        )
        if incomplete:
            evidence = "檢查結果不完整，部分步驟未回傳結果，請重新執行檢查。\n" + evidence
        return [
            CourseAICheckItemStudent(
                item_id=item_id,
                title=rubric_title or (str(selected[0].get("title") or item_id) if selected else item_id),
                status=status,
                comment=evidence,
            )
        ]

    return [
        CourseAICheckItemStudent(
            item_id=check_id,
            title=str(check.get("title") or check_id),
            status="unknown" if execution_failed else teacher_decisions.get(
                check_id, str(check.get("status") or "unknown")
            ),
            comment=str(check.get("evidence") or ""),
        )
        for check_id, check in check_by_id.items()
    ]


def _legacy_judgement_items(
    judgement: dict[str, Any], *, item_id: str | None
) -> list[CourseAICheckItemStudent]:
    raw_items = judgement.get("item_judgements")
    if not isinstance(raw_items, list):
        return []
    items: list[CourseAICheckItemStudent] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        if item_id and str(raw.get("item_id") or "") != item_id:
            continue
        items.append(
            CourseAICheckItemStudent(
                item_id=str(raw.get("item_id") or ""),
                title=str(raw.get("title") or ""),
                status=str(raw.get("status") or "unknown"),
                score=raw.get("score") if isinstance(raw.get("score"), int) else None,
                max_score=(
                    raw.get("max_score")
                    if isinstance(raw.get("max_score"), int)
                    else None
                ),
                comment=str(raw.get("comment") or ""),
            )
        )
    return items


def _check_to_student(
    run: TeacherJudgeScriptRun,
    *,
    user_id: uuid.UUID,
    artifact: TeacherJudgeScriptArtifact | None = None,
    item_id: str | None = None,
) -> CourseAICheckStudent:
    """Project one run down to the feedback that belongs on a student page."""

    target = _target_for_student(run, user_id)
    teacher_feedback, _ = _teacher_review(target)
    judgement = target.get("ai_judgement") if isinstance(target, dict) else {}
    if not isinstance(judgement, dict):
        judgement = {}
    items = _script_checks(target, artifact=artifact, item_id=item_id)
    if not items:
        items = _legacy_judgement_items(judgement, item_id=item_id)

    parsed_result = target.get("parsed_result")
    parsed_summary = (
        str(parsed_result.get("summary") or "")
        if isinstance(parsed_result, dict)
        else ""
    )
    parsed_errors = (
        parsed_result.get("errors")
        if isinstance(parsed_result, dict)
        else []
    )
    target_validation = target.get("validation")
    # 輸出驗證失敗、執行器錯誤、AI 判讀錯誤都是給老師除錯的技術訊息
    # （例如 pydantic 的「N validation errors for ManagedScriptResult」），
    # 學生頁只給一句說明；完整原因仍留在老師端 AI導師檢查面板
    has_internal_error = bool(
        (target_validation.get("error") if isinstance(target_validation, dict) else "")
        or target.get("error")
        or judgement.get("error")
        or target.get("status") in {"failed", "cancelled"}
    )
    # 腳本自己在 errors 回報的訊息屬於輸出契約的一部分，照舊顯示
    script_errors = (
        "; ".join(str(error) for error in parsed_errors if error)
        if isinstance(parsed_errors, list)
        else ""
    )
    student_error = t("course.ai_check_incomplete") if has_internal_error else script_errors
    item_score = items[0].score if item_id and items else None
    item_max_score = items[0].max_score if item_id and items else None
    has_script_result = isinstance(parsed_result, dict)
    return CourseAICheckStudent(
        run_id=run.id,
        status=run.status.value,
        submitted_at=run.created_at,
        finished_at=run.finished_at,
        score=item_score if item_id else (
            judgement.get("score")
            if not has_script_result and isinstance(judgement.get("score"), int)
            else None
        ),
        max_score=item_max_score if item_id else (
            judgement.get("max_score")
            if not has_script_result and isinstance(judgement.get("max_score"), int)
            else None
        ),
        # 舊版 AI 判讀在輸出驗證失敗時會把原始錯誤寫進 ai_judgement.summary（status=skipped），
        # 出現內部錯誤時只採用腳本自己的 summary，避免技術訊息從這裡漏給學生
        summary=parsed_summary or (
            "" if has_internal_error else str(judgement.get("summary") or "")
        ),
        teacher_feedback=teacher_feedback,
        error=student_error,
        items=items,
    )


def _student_runs(
    session: Session,
    *,
    artifact_ids: list[uuid.UUID],
    user_id: uuid.UUID,
) -> list[TeacherJudgeScriptRun]:
    """這個對話所有腳本（每個版本、每台機器）裡帶有該學生結果的 run，新的在前。"""
    if not artifact_ids:
        return []
    runs = session.exec(
        select(TeacherJudgeScriptRun)
        .where(col(TeacherJudgeScriptRun.artifact_id).in_(artifact_ids))
        .order_by(desc(TeacherJudgeScriptRun.created_at))
    ).all()
    return [run for run in runs if _target_for_student(run, user_id)]


def _latest_student_check(
    runs: list[TeacherJudgeScriptRun],
    *,
    user_id: uuid.UUID,
) -> CourseAICheckStudent | None:
    return (
        _check_to_student(runs[0], artifact=runs[0].artifact, user_id=user_id)
        if runs
        else None
    )


def _run_item_ids(run: TeacherJudgeScriptRun) -> set[str]:
    snapshot = run.artifact.rubric_snapshot_json if run.artifact else None
    return {item.id for item in student_task_items(snapshot or {})}


def _latest_student_checkpoint_checks(
    runs: list[TeacherJudgeScriptRun],
    *,
    user_id: uuid.UUID,
    item_ids: list[str],
) -> dict[str, CourseAICheckStudent]:
    """每個檢查點取最新一次「有判定」的結果。

    一個檢查點只在負責它的那台機器的腳本裡；最新一次如果只是環境異常、沒有
    逐項判定，保留更早那次的有效判定。完全沒有判定時才給最新那次，讓學生端
    顯示待檢查或環境異常，而不是把整次執行的「完成」當成通過。
    """
    wanted = set(item_ids)
    checks: dict[str, CourseAICheckStudent] = {}
    without_verdict: dict[str, CourseAICheckStudent] = {}
    for run in runs:
        requested = _requested_item_id(run)
        covered = _run_item_ids(run)
        candidates = [requested] if requested else [item for item in item_ids if item in covered]
        for checkpoint_id in candidates:
            if checkpoint_id not in wanted or checkpoint_id in checks:
                continue
            check = _check_to_student(
                run, artifact=run.artifact, item_id=checkpoint_id, user_id=user_id
            )
            if check.items:
                checks[checkpoint_id] = check
            else:
                without_verdict.setdefault(checkpoint_id, check)
        if len(checks) == len(wanted):
            break
    for checkpoint_id, check in without_verdict.items():
        checks.setdefault(checkpoint_id, check)
    return checks


def student_task_items(snapshot: dict[str, Any]) -> list[CourseAITaskItemStudent]:
    """把 ``{"items": [...]}`` 形式的評分項目整理成學生看得到的檢查項目。

    核准版本的 rubric 快照與尚未核准的 AI 解析結果共用這一份正規化；
    id 保留原值（已完成項目以它比對），detectable 去空白轉小寫後限定三種。
    """
    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        return []

    items: list[CourseAITaskItemStudent] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        detectable = str(raw.get("detectable") or "manual").strip().lower()
        if detectable not in _DETECTABLE_VALUES:
            detectable = "manual"
        items.append(
            CourseAITaskItemStudent(
                id=str(raw.get("id") or f"item-{index + 1}"),
                title=title,
                description=str(raw.get("description") or "").strip(),
                detectable=detectable,  # type: ignore[arg-type]
                order=index,
            )
        )
    return items


def _student_source_document(
    source_file: TeacherJudgeFile | None,
    *,
    teaching_class_id: uuid.UUID,
) -> CourseAISourceDocumentStudent | None:
    """Project only an uploaded PDF's display metadata to the student."""

    if (
        source_file is None
        or source_file.teaching_class_id != teaching_class_id
        or source_file.source_type != "uploaded"
        or not source_file.original_filename
        or not source_file.original_filename.lower().endswith(".pdf")
    ):
        return None
    return CourseAISourceDocumentStudent(
        filename=source_file.original_filename,
        display_name=(source_file.display_name or source_file.original_filename),
    )


def _current_scripts(
    session: Session, judge_session_id: uuid.UUID
) -> list[TeacherJudgeScriptArtifact]:
    """依工作區明確指定的生效組投影，不從歷代核准時間推算。"""
    workspace = session.get(TeacherJudgeSession, judge_session_id)
    if workspace is None:
        return []
    members = active_set_children(session, workspace)
    if not members:
        return []
    # 依班級機器的排列順序（老師在上課環境看到的順序）；同一次製作的建立時間
    # 幾乎相同，不能拿來排
    node_order = {
        node.node_key: node.sort_order
        for node in session.exec(
            select(TeachingClassMachineNode).where(
                TeachingClassMachineNode.class_id == workspace.teaching_class_id
            )
        ).all()
    }
    return sorted(
        members,
        key=lambda row: (
            node_order.get(row.target_node_key or "", len(node_order)),
            row.created_at,
            str(row.id),
        ),
    )


def _merged_items(
    scripts: list[TeacherJudgeScriptArtifact],
) -> list[CourseAITaskItemStudent]:
    """把每台機器那支腳本的檢查點合成一份，並標出在哪台機器上檢查。"""
    items: list[CourseAITaskItemStudent] = []
    seen: set[str] = set()
    for script in scripts:
        for item in student_task_items(script.rubric_snapshot_json or {}):
            if item.id in seen:
                continue
            seen.add(item.id)
            items.append(
                item.model_copy(
                    update={
                        "order": len(items),
                        "machine_node_key": script.target_node_key,
                    }
                )
            )
    return items


def _session_task(
    session: Session, judge_session: TeacherJudgeSession
) -> tuple[list[CourseAITaskItemStudent], list[TeacherJudgeScriptArtifact], TeacherJudgeFile | None]:
    """這週檢查的項目：有核准的腳本就用腳本的（多台機器合在一起），
    還沒有腳本時用這週檢查表上的項目——學生照樣可以回報完成，只是還不能被檢查。"""
    scripts = _current_scripts(session, judge_session.id)
    if scripts:
        newest = max(scripts, key=lambda row: (row.approved_at or row.created_at))
        source_file = (
            session.get(TeacherJudgeFile, newest.source_file_id)
            if newest.source_file_id
            else None
        )
        return _merged_items(scripts), scripts, source_file
    source_file = (
        session.get(TeacherJudgeFile, judge_session.selected_file_id)
        if judge_session.selected_file_id
        else None
    )
    items = student_task_items((source_file.analysis_json or {}) if source_file else {})
    return items, [], source_file


def _week_visible(session: Session, judge_session: TeacherJudgeSession) -> bool:
    if judge_session.teaching_class_week_id is None:
        return False
    week = session.get(TeachingClassWeek, judge_session.teaching_class_week_id)
    return week is not None and week.status in PUBLISHED_WEEK_STATUSES


def _student_reply(
    session: Session, *, session_id: uuid.UUID, user_id: uuid.UUID
) -> TeacherJudgeStudentReply | None:
    return session.exec(
        select(TeacherJudgeStudentReply).where(
            TeacherJudgeStudentReply.session_id == session_id,
            TeacherJudgeStudentReply.student_id == user_id,
        )
    ).first()


def save_student_reply(
    session: Session,
    judge_session: TeacherJudgeSession,
    *,
    student_id: uuid.UUID,
    feedback: str,
    teacher_id: uuid.UUID | None,
) -> dict[str, Any]:
    """老師給一位學生這週的回覆：再回覆就覆蓋前一則；空白＝不回覆，刪掉原本的。"""

    enrolled = session.exec(
        select(TeachingClassStudent).where(
            TeachingClassStudent.class_id == judge_session.teaching_class_id,
            TeachingClassStudent.user_id == student_id,
            TeachingClassStudent.status == "active",
        )
    ).first()
    if enrolled is None:
        raise NotFoundError(t("ai_assignment.student_not_in_class"))
    text = feedback.strip()
    reply = _student_reply(session, session_id=judge_session.id, user_id=student_id)
    if not text:
        if reply is not None:
            session.delete(reply)
            session.commit()
        return {"user_id": str(student_id), "teacher_reply": "", "teacher_reply_at": None}
    if reply is None:
        reply = TeacherJudgeStudentReply(
            session_id=judge_session.id, student_id=student_id, feedback=text
        )
    reply.feedback = text
    reply.updated_by = teacher_id
    reply.updated_at = get_datetime_utc()
    session.add(reply)
    session.commit()
    session.refresh(reply)
    return {
        "user_id": str(student_id),
        "teacher_reply": reply.feedback,
        "teacher_reply_at": reply.updated_at.isoformat(),
    }


def list_student_ai_assignments(
    session: Session,
    *,
    user_id: uuid.UUID,
    path_id: uuid.UUID,
) -> list[CourseAIAssignmentStudent]:
    """Return the AI checks visible to one student for a path.

    一週一個對話，學生看到的作業就是那個對話：檢查點來自最新一次製作的整組
    腳本（多台機器合在一起），結果只採用生效組的 child，保留同組重跑結果。
    """

    teaching_class = course_service.get_student_class_for_path(
        session,
        user_id=user_id,
        path_id=path_id,
    )
    if teaching_class is None:
        return []
    enrolled = session.exec(
        select(TeachingClassStudent).where(
            TeachingClassStudent.class_id == teaching_class.id,
            TeachingClassStudent.user_id == user_id,
            TeachingClassStudent.status == "active",
        )
    ).first()
    if enrolled is None:
        return []

    judge_sessions = session.exec(
        select(TeacherJudgeSession)
        .where(TeacherJudgeSession.teaching_class_id == teaching_class.id)
        .order_by(col(TeacherJudgeSession.created_at))
    ).all()

    assignments: list[CourseAIAssignmentStudent] = []
    for judge_session in judge_sessions:
        items, scripts, source_file = _session_task(session, judge_session)
        # 作業跟著老師每週的內容發布：週次發布後學生才看得到，草稿不外流
        if not items or not _week_visible(session, judge_session):
            continue
        newest = (
            max(scripts, key=lambda row: (row.approved_at or row.created_at))
            if scripts
            else None
        )
        item_ids = [item.id for item in items]
        runs = (
            _student_runs(
                session,
                artifact_ids=[script.id for script in scripts],
                user_id=user_id,
            )
            if scripts
            else []
        )
        reply = _student_reply(session, session_id=judge_session.id, user_id=user_id)
        snapshot = (
            newest.rubric_snapshot_json
            if newest is not None
            else (source_file.analysis_json if source_file else None)
        ) or {}
        assignments.append(
            CourseAIAssignmentStudent(
                id=judge_session.id,
                teaching_class_id=teaching_class.id,
                teaching_class_name=teaching_class.name,
                session_id=judge_session.id,
                teaching_class_week_id=judge_session.teaching_class_week_id,
                title=judge_session.title,
                summary=str(snapshot.get("summary") or "").strip(),
                template_key=(
                    newest.template_key
                    if newest is not None
                    else (source_file.template_key if source_file else "")
                ),
                version=max((script.version for script in scripts), default=0),
                approved_at=newest.approved_at if newest is not None else None,
                check_available=bool(scripts),
                teacher_reply=reply.feedback if reply is not None else "",
                teacher_reply_at=reply.updated_at if reply is not None else None,
                items=items,
                source_document=_student_source_document(
                    source_file,
                    teaching_class_id=teaching_class.id,
                ),
                completion=_completion_to_student(
                    session,
                    _student_submission(
                        session, session_id=judge_session.id, user_id=user_id
                    ),
                    item_ids=item_ids,
                ),
                latest_check=_latest_student_check(runs, user_id=user_id),
                checkpoint_checks=_latest_student_checkpoint_checks(
                    runs, user_id=user_id, item_ids=item_ids
                ),
            )
        )
    return assignments


def get_student_ai_assignment(
    session: Session,
    *,
    user_id: uuid.UUID,
    path_id: uuid.UUID,
    assignment_id: uuid.UUID,
) -> CourseAIAssignmentStudent:
    """Return one visible assignment or hide it behind a 404."""

    assignment = next(
        (
            item
            for item in list_student_ai_assignments(
                session,
                user_id=user_id,
                path_id=path_id,
            )
            if item.id == assignment_id
        ),
        None,
    )
    if assignment is None:
        raise NotFoundError(t("ai_assignment.not_found"))
    return assignment


def get_student_ai_assignment_source_document(
    session: Session,
    *,
    user_id: uuid.UUID,
    path_id: uuid.UUID,
    assignment_id: uuid.UUID,
) -> tuple[Path, str]:
    """Return an enrolled student's PDF path after assignment authorization."""

    assignment = get_student_ai_assignment(
        session,
        user_id=user_id,
        path_id=path_id,
        assignment_id=assignment_id,
    )
    judge_session = session.get(TeacherJudgeSession, assignment.session_id)
    source_file = (
        _session_task(session, judge_session)[2] if judge_session is not None else None
    )
    if (
        _student_source_document(
            source_file,
            teaching_class_id=assignment.teaching_class_id,
        )
        is None
        or source_file is None
    ):
        raise NotFoundError(t("ai_assignment.source_pdf_not_found"))
    return file_service.get_file_download(
        session=session,
        teaching_class_id=assignment.teaching_class_id,
        file_id=source_file.id,
    )


def get_student_ai_check(
    session: Session,
    *,
    user_id: uuid.UUID,
    path_id: uuid.UUID,
    assignment_id: uuid.UUID,
    run_id: uuid.UUID,
) -> CourseAICheckStudent:
    """Return only the current student's own run for a visible assignment."""

    assignment = get_student_ai_assignment(
        session,
        user_id=user_id,
        path_id=path_id,
        assignment_id=assignment_id,
    )
    # 作業就是這一週的對話：run 的腳本必須屬於這個對話，也就釘住了班級
    run = session.get(TeacherJudgeScriptRun, run_id)
    if (
        run is None
        or run.artifact is None
        or run.artifact.session_id != assignment.id
        or not _target_for_student(run, user_id)
    ):
        raise NotFoundError(t("ai_assignment.check_not_found"))
    return _check_to_student(
        run,
        artifact=run.artifact,
        item_id=_requested_item_id(run),
        user_id=user_id,
    )


def update_student_completion(
    session: Session,
    *,
    user_id: uuid.UUID,
    path_id: uuid.UUID,
    assignment_id: uuid.UUID,
    item_id: str | None,
    completed: bool,
) -> CourseAICompletionStudent:
    """Persist completion without starting any check.

    When ``item_id`` is omitted, all rubric items are toggled atomically so the
    student-facing course page can expose one completion control per week.
    """

    assignment = get_student_ai_assignment(
        session,
        user_id=user_id,
        path_id=path_id,
        assignment_id=assignment_id,
    )
    submission = _student_submission(
        session,
        session_id=assignment.id,
        user_id=user_id,
    )
    valid_item_ids = [item.id for item in assignment.items]
    if item_id is not None and item_id not in valid_item_ids:
        raise NotFoundError(t("ai_assignment.task_item_not_found"))

    now = get_datetime_utc()
    if item_id is None:
        completed_items = set(valid_item_ids if completed else [])
    else:
        completed_items = _submission_item_ids(session, submission)
        if completed:
            completed_items.add(item_id)
        else:
            completed_items.discard(item_id)
    completed_item_ids = [
        valid_item_id
        for valid_item_id in valid_item_ids
        if valid_item_id in completed_items
    ]
    all_completed = len(completed_item_ids) == len(valid_item_ids)
    if submission is None:
        submission = TeacherJudgeStudentSubmission(
            session_id=assignment.id,
            student_id=user_id,
            ready_at=now if all_completed else None,
            updated_at=now,
        )
        session.add(submission)
        session.flush()
    else:
        # 已就緒（ready_at 有值）且仍全部完成時保留原本的就緒時間
        if not all_completed:
            submission.ready_at = None
        elif submission.ready_at is None:
            submission.ready_at = now
        submission.updated_at = now
        session.add(submission)
        session.exec(
            delete(TeacherJudgeSubmissionItem).where(
                col(TeacherJudgeSubmissionItem.submission_id) == submission.id
            )
        )
    for completed_item_id in completed_item_ids:
        session.add(
            TeacherJudgeSubmissionItem(
                submission_id=submission.id, item_id=completed_item_id
            )
        )
    session.commit()
    session.refresh(submission)
    return _completion_to_student(
        session,
        submission,
        item_ids=valid_item_ids,
    )


def session_progress(
    session: Session, judge_session: TeacherJudgeSession
) -> dict[str, Any]:
    """導師核查用：全班每位學生在這週檢查回報完成到哪裡（不含執行結果）。

    項目與學生端看到的同一份（最新一次製作的腳本，多台機器合在一起）；
    還沒有核准的腳本時用這週檢查表上的項目，進度照樣看得到，只是不能執行檢查。
    """
    items = _session_task(session, judge_session)[0]
    item_ids = [item.id for item in items]
    enrollments = session.exec(
        select(TeachingClassStudent).where(
            TeachingClassStudent.class_id == judge_session.teaching_class_id,
            TeachingClassStudent.status == "active",
        )
    ).all()
    users = {
        user.id: user
        for user in session.exec(
            select(User).where(col(User.id).in_([row.user_id for row in enrollments]))
        ).all()
    }
    submissions = {
        row.student_id: row
        for row in session.exec(
            select(TeacherJudgeStudentSubmission).where(
                TeacherJudgeStudentSubmission.session_id == judge_session.id
            )
        ).all()
    }
    done_by_submission: dict[uuid.UUID, set[str]] = {}
    if submissions:
        for submission_id, item_id in session.exec(
            select(
                TeacherJudgeSubmissionItem.submission_id,
                TeacherJudgeSubmissionItem.item_id,
            ).where(
                col(TeacherJudgeSubmissionItem.submission_id).in_(
                    [row.id for row in submissions.values()]
                )
            )
        ).all():
            done_by_submission.setdefault(submission_id, set()).add(item_id)

    replies = {
        row.student_id: row
        for row in session.exec(
            select(TeacherJudgeStudentReply).where(
                TeacherJudgeStudentReply.session_id == judge_session.id
            )
        ).all()
    }

    students: list[dict[str, Any]] = []
    for enrollment in enrollments:
        user = users.get(enrollment.user_id)
        if user is None:
            continue
        submission = submissions.get(user.id)
        reply = replies.get(user.id)
        done = done_by_submission.get(submission.id, set()) if submission else set()
        completed = [item_id for item_id in item_ids if item_id in done]
        all_done = bool(item_ids) and len(completed) == len(item_ids)
        students.append(
            {
                "user_id": str(user.id),
                "full_name": user.full_name,
                "email": user.email,
                "completed_item_ids": completed,
                "ready_at": (
                    submission.ready_at.isoformat()
                    if submission and submission.ready_at and all_done
                    else None
                ),
                "updated_at": submission.updated_at.isoformat() if submission else None,
                "teacher_reply": reply.feedback if reply else "",
                "teacher_reply_at": reply.updated_at.isoformat() if reply else None,
            }
        )
    students.sort(key=lambda row: row["email"])
    return {
        "items": [
            {"id": item.id, "title": item.title, "machine_node_key": item.machine_node_key}
            for item in items
        ],
        "students": students,
    }


__all__ = [
    "get_student_ai_assignment",
    "get_student_ai_assignment_source_document",
    "get_student_ai_check",
    "list_student_ai_assignments",
    "session_progress",
    "update_student_completion",
]
