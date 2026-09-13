"""Persistent Teacher Judge session workflow."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import HTTPException
from sqlmodel import Session, col, desc, func, select

from app.ai.teacher_judge.attachment_service import (
    attachment_context,
    attachment_public,
    storage_path,
)
from app.ai.teacher_judge.file_service import (
    FileDeleteStage,
    _stored_path,
    _unlink_if_exists,
    clone_file_asset,
    finalize_file_delete,
    restore_file_delete,
    stage_file_delete,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricChatMessage,
    TeacherJudgeSessionMessagePublic,
    TeacherJudgeSessionPublic,
)
from app.ai.teacher_judge.service import summarize_conversation
from app.core.db import engine
from app.core.i18n import t
from app.infrastructure.worker import submit
from app.models.teacher_judge_attachment import TeacherJudgeSessionAttachment
from app.models.teacher_judge_file import TeacherJudgeFile, TeacherJudgeFileStatus
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptArtifact
from app.models.teacher_judge_script_run import TeacherJudgeScriptRun
from app.models.teacher_judge_session import (
    TeacherJudgeMessageRole,
    TeacherJudgeMessageType,
    TeacherJudgeSession,
    TeacherJudgeSessionMessage,
    TeacherJudgeSessionStatus,
)
from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand

logger = logging.getLogger(__name__)

HISTORY_MESSAGE_LIMIT = 20
HISTORY_CHARACTER_LIMIT = 24000
SUMMARY_TURN_INTERVAL = 10
SUMMARY_CONTEXT_CHARACTER_LIMIT = 8000
_WHITESPACE_ENTITIES = re.compile(r"(?:&#x20;|&#32;|&nbsp;)", re.IGNORECASE)
_SENSITIVE_PATTERNS = (
    re.compile(
        r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization)\b"
        r"(\s*[:=]\s*)([^\s,;]+)"
    ),
    # 有界量詞避免 polynomial ReDoS（CodeQL py/polynomial-redos）：
    # header 詞彙固定為大寫（RSA/EC/OPENSSH/ENCRYPTED…），本體長度設上限
    re.compile(
        r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----"
        r"[\s\S]{0,16384}?"
        r"-----END [A-Z ]{0,40}PRIVATE KEY-----"
    ),
)


@dataclass(frozen=True, slots=True)
class _SummaryJobSnapshot:
    """Immutable data captured before a summary worker calls the model."""

    session_id: uuid.UUID
    teaching_class_id: uuid.UUID
    boundary_message_id: uuid.UUID
    assistant_count: int
    selected_file_id: uuid.UUID | None
    analysis_revision: int | None
    messages: tuple[TeacherJudgeRubricChatMessage, ...]
    previous_summary: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def redact_message_content(value: str) -> str:
    redacted = value
    redacted = _SENSITIVE_PATTERNS[0].sub(r"\1\2[REDACTED]", redacted)
    redacted = _SENSITIVE_PATTERNS[1].sub("[REDACTED PRIVATE KEY]", redacted)
    return redacted


def normalize_message_text(value: str) -> str:
    """Normalize whitespace entities without interpreting message content as HTML."""
    return _WHITESPACE_ENTITIES.sub(" ", value)


def require_selected_file(db: Session, item: TeacherJudgeSession) -> TeacherJudgeFile:
    if not item.selected_file_id:
        raise HTTPException(status_code=409, detail=t("session.no_rubric_selected"))
    file = db.get(TeacherJudgeFile, item.selected_file_id)
    if (
        not file
        or file.teaching_class_id != item.teaching_class_id
        or file.status != TeacherJudgeFileStatus.active
    ):
        raise HTTPException(
            status_code=409, detail=t("session.selected_file_unavailable")
        )
    return file


def selected_file_for_chat(
    db: Session, item: TeacherJudgeSession
) -> TeacherJudgeFile | None:
    """Return the selected rubric when present; a chat can start without one."""
    if not item.selected_file_id:
        return None
    return require_selected_file(db, item)


def get_session(
    db: Session, class_id: uuid.UUID, session_id: uuid.UUID
) -> TeacherJudgeSession:
    item = db.get(TeacherJudgeSession, session_id)
    if not item or item.teaching_class_id != class_id:
        raise HTTPException(status_code=404, detail=t("session.not_found"))
    return item


def delete_session_data(db: Session, item: TeacherJudgeSession) -> None:
    """Delete a session and its private rubric, messages, scripts, and runs."""
    source_file_stage: FileDeleteStage | None = None
    attachment_rows = list(
        db.exec(
            select(TeacherJudgeSessionAttachment).where(
                TeacherJudgeSessionAttachment.session_id == item.id
            )
        )
    )
    try:
        if item.selected_file_id:
            source_file = db.get(TeacherJudgeFile, item.selected_file_id)
            if (
                source_file is not None
                and source_file.teaching_class_id == item.teaching_class_id
            ):
                # A unique DB index prevents this for new data.  The guard keeps a
                # legacy shared row from being removed underneath another session
                # if deletion runs before the ownership migration is applied.
                other_session = db.exec(
                    select(TeacherJudgeSession).where(
                        TeacherJudgeSession.selected_file_id == source_file.id,
                        TeacherJudgeSession.id != item.id,
                    )
                ).first()
                if other_session is None:
                    source_file_stage = stage_file_delete(
                        session=db, file=source_file
                    )

        artifacts = list(
            db.exec(
                select(TeacherJudgeScriptArtifact).where(
                    TeacherJudgeScriptArtifact.session_id == item.id
                )
            )
        )
        for artifact in artifacts:
            runs = list(
                db.exec(
                    select(TeacherJudgeScriptRun).where(
                        TeacherJudgeScriptRun.artifact_id == artifact.id
                    )
                )
            )
            for run in runs:
                db.delete(run)

        messages = list(
            db.exec(
                select(TeacherJudgeSessionMessage).where(
                    TeacherJudgeSessionMessage.session_id == item.id
                )
            )
        )
        for attachment in attachment_rows:
            db.delete(attachment)
        for message in messages:
            db.delete(message)
        for artifact in artifacts:
            db.delete(artifact)

        db.delete(item)
        db.commit()
    except Exception:
        db.rollback()
        restore_file_delete(source_file_stage)
        raise
    else:
        finalize_file_delete(source_file_stage)
        for attachment in attachment_rows:
            try:
                storage_path(attachment).unlink(missing_ok=True)
            except OSError:
                # 附件實體檔刪不掉不影響 DB 刪除，留給清理排程處理
                pass


def _reset_summary_state(item: TeacherJudgeSession) -> None:
    item.summary = ""
    item.summary_through_message_id = None
    item.summary_through_assistant_count = 0


def finalize_cleared_attachments(
    attachments: Sequence[TeacherJudgeSessionAttachment],
) -> None:
    """Remove attachment files after the corresponding DB transaction commits."""
    for attachment in attachments:
        try:
            storage_path(attachment).unlink(missing_ok=True)
        except OSError:
            # 附件實體檔刪不掉不影響 DB 狀態，留給清理排程處理
            pass


def clear_session_messages(
    db: Session,
    item: TeacherJudgeSession,
    *,
    commit: bool = True,
) -> list[TeacherJudgeSessionAttachment]:
    """Clear conversation history while keeping the session and its artifacts.

    ``commit=False`` is used by source switching so the selected source and the
    history reset become one transaction.  Attachment files are always removed
    only after the caller commits successfully.
    """
    attachments = list(
        db.exec(
            select(TeacherJudgeSessionAttachment).where(
                TeacherJudgeSessionAttachment.session_id == item.id
            )
        )
    )
    messages = list(
        db.exec(
            select(TeacherJudgeSessionMessage).where(
                TeacherJudgeSessionMessage.session_id == item.id
            )
        )
    )
    for attachment in attachments:
        db.delete(attachment)
    for message in messages:
        db.delete(message)

    now = _now()
    _reset_summary_state(item)
    item.updated_at = now
    item.last_activity_at = now
    db.add(item)
    if commit:
        db.commit()
        db.refresh(item)
        finalize_cleared_attachments(attachments)
    return attachments


def ensure_selected_file_available(
    db: Session,
    file_id: uuid.UUID,
    *,
    exclude_session_id: uuid.UUID | None = None,
) -> None:
    """Reject attaching a rubric that another session already owns.

    Session fork is the explicit copy boundary.  Regular create/update flows
    may claim an unassigned class file, but they never silently clone or share
    a source with another session.
    """
    statement = select(TeacherJudgeSession).where(
        TeacherJudgeSession.selected_file_id == file_id
    )
    if exclude_session_id is not None:
        statement = statement.where(TeacherJudgeSession.id != exclude_session_id)
    owner = db.exec(statement).first()
    if owner is None:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "teacher_judge_file_in_use",
            "message": t("session.file_in_use"),
            "session_id": str(owner.id),
        },
    )


def ensure_active(item: TeacherJudgeSession) -> None:
    if item.status == TeacherJudgeSessionStatus.archived:
        raise HTTPException(status_code=409, detail=t("session.archived_readonly"))


def validate_selected_file(
    db: Session, class_id: uuid.UUID, file_id: uuid.UUID | None
) -> None:
    if file_id is None:
        return
    file = db.get(TeacherJudgeFile, file_id)
    if (
        not file
        or file.teaching_class_id != class_id
        or file.status != TeacherJudgeFileStatus.active
    ):
        raise HTTPException(
            status_code=400,
            detail=t("session.file_not_in_class"),
        )


def _session_public(
    item: TeacherJudgeSession,
    *,
    file: TeacherJudgeFile | None,
    message_count: int,
    script_count: int,
    run_count: int,
) -> TeacherJudgeSessionPublic:
    return TeacherJudgeSessionPublic(
        id=str(item.id),
        teaching_class_id=str(item.teaching_class_id),
        teaching_class_week_id=(
            str(item.teaching_class_week_id) if item.teaching_class_week_id else None
        ),
        title=item.title,
        status=item.status.value,
        selected_file_id=str(item.selected_file_id) if item.selected_file_id else None,
        selected_file_name=(file.display_name or file.original_filename) if file else None,
        selected_file_item_count=(
            len(file.analysis_json.get("items", []))
            if file and isinstance(file.analysis_json, dict)
            and isinstance(file.analysis_json.get("items"), list)
            else None
        ),
        template_key=file.template_key if file else None,
        summary=item.summary,
        message_count=message_count,
        script_count=script_count,
        run_count=run_count,
        created_by=str(item.created_by) if item.created_by else None,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
        last_activity_at=item.last_activity_at.isoformat(),
        pinned_at=item.pinned_at.isoformat() if item.pinned_at else None,
    )


def session_public(db: Session, item: TeacherJudgeSession) -> TeacherJudgeSessionPublic:
    """Build one public session without changing the existing response contract."""
    file = (
        db.get(TeacherJudgeFile, item.selected_file_id)
        if item.selected_file_id
        else None
    )
    message_count = db.exec(
        select(func.count())
        .select_from(TeacherJudgeSessionMessage)
        .where(TeacherJudgeSessionMessage.session_id == item.id)
    ).one()
    script_count = db.exec(
        select(func.count())
        .select_from(TeacherJudgeScriptArtifact)
        .where(TeacherJudgeScriptArtifact.session_id == item.id)
    ).one()
    run_count = db.exec(
        select(func.count())
        .select_from(TeacherJudgeScriptRun)
        .join(TeacherJudgeScriptArtifact)
        .where(TeacherJudgeScriptArtifact.session_id == item.id)
    ).one()
    return _session_public(
        item,
        file=file,
        message_count=message_count,
        script_count=script_count,
        run_count=run_count,
    )


def session_public_many(
    db: Session, items: Sequence[TeacherJudgeSession]
) -> list[TeacherJudgeSessionPublic]:
    """Build list responses with batched file and count lookups."""
    if not items:
        return []
    session_ids = list(dict.fromkeys(item.id for item in items))
    file_ids = list(
        dict.fromkeys(
            item.selected_file_id for item in items if item.selected_file_id is not None
        )
    )
    files_by_id = {
        row.id: row
        for row in (
            db.exec(
                select(TeacherJudgeFile).where(col(TeacherJudgeFile.id).in_(file_ids))
            ).all()
            if file_ids
            else []
        )
    }
    message_counts = dict(
        db.exec(
            select(col(TeacherJudgeSessionMessage.session_id), func.count())
            .where(col(TeacherJudgeSessionMessage.session_id).in_(session_ids))
            .group_by(col(TeacherJudgeSessionMessage.session_id))
        ).all()
    )
    script_counts = dict(
        db.exec(
            select(col(TeacherJudgeScriptArtifact.session_id), func.count())
            .where(col(TeacherJudgeScriptArtifact.session_id).in_(session_ids))
            .group_by(col(TeacherJudgeScriptArtifact.session_id))
        ).all()
    )
    run_counts = dict(
        db.exec(
            select(
                col(TeacherJudgeScriptArtifact.session_id),
                func.count(col(TeacherJudgeScriptRun.id)),
            )
            .select_from(TeacherJudgeScriptRun)
            .join(
                TeacherJudgeScriptArtifact,
                col(TeacherJudgeScriptRun.artifact_id)
                == col(TeacherJudgeScriptArtifact.id),
            )
            .where(col(TeacherJudgeScriptArtifact.session_id).in_(session_ids))
            .group_by(col(TeacherJudgeScriptArtifact.session_id))
        ).all()
    )
    return [
        _session_public(
            item,
            file=(
                files_by_id.get(item.selected_file_id)
                if item.selected_file_id is not None
                else None
            ),
            message_count=message_counts.get(item.id, 0),
            script_count=script_counts.get(item.id, 0),
            run_count=run_counts.get(item.id, 0),
        )
        for item in items
    ]


def _fork_title(db: Session, class_id: uuid.UUID, title: str) -> str:
    base = f"{title}（副本）"
    existing = {
        row.title
        for row in db.exec(
            select(TeacherJudgeSession).where(
                TeacherJudgeSession.teaching_class_id == class_id
            )
        )
    }
    if base not in existing:
        return base
    for index in range(2, 1000):
        candidate = f"{title}（副本 {index}）"
        if candidate not in existing:
            return candidate
    raise HTTPException(status_code=409, detail=t("session.fork_title_exhausted"))


def fork_session_data(
    db: Session,
    source: TeacherJudgeSession,
    *,
    title: str | None,
    created_by: uuid.UUID | None,
) -> TeacherJudgeSession:
    """Clone editable settings only; history and execution evidence stay behind."""
    cloned_file: TeacherJudgeFile | None = None
    try:
        if source.selected_file_id:
            source_file = db.get(TeacherJudgeFile, source.selected_file_id)
            if (
                source_file is None
                or source_file.teaching_class_id != source.teaching_class_id
                or source_file.status != TeacherJudgeFileStatus.active
            ):
                raise HTTPException(
                    status_code=409, detail=t("session.fork_file_unavailable")
                )
            cloned_file = clone_file_asset(
                session=db,
                source=source_file,
                teaching_class_id=source.teaching_class_id,
                created_by=created_by,
            )
        clone = TeacherJudgeSession(
            teaching_class_id=source.teaching_class_id,
            teaching_class_week_id=source.teaching_class_week_id,
            title=(title.strip() if title else _fork_title(db, source.teaching_class_id, source.title)),
            status=TeacherJudgeSessionStatus.active,
            selected_file_id=cloned_file.id if cloned_file else None,
            summary="",
            created_by=created_by,
        )
        db.add(clone)
        db.commit()
        db.refresh(clone)
        return clone
    except Exception:
        db.rollback()
        if cloned_file and cloned_file.original_filename:
            _unlink_if_exists(_stored_path(cloned_file.id, cloned_file.original_filename))
        raise


def message_attachments(
    db: Session, message_id: uuid.UUID
) -> list[TeacherJudgeSessionAttachment]:
    return list(
        db.exec(
            select(TeacherJudgeSessionAttachment)
            .where(TeacherJudgeSessionAttachment.message_id == message_id)
            .order_by(
                col(TeacherJudgeSessionAttachment.created_at),
                col(TeacherJudgeSessionAttachment.id),
            )
        )
    )


def message_attachments_by_message_ids(
    db: Session, message_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[TeacherJudgeSessionAttachment]]:
    """Load message attachments in one query for list/history responses."""
    unique_ids = list(dict.fromkeys(message_ids))
    if not unique_ids:
        return {}
    rows = list(
        db.exec(
            select(TeacherJudgeSessionAttachment)
            .where(col(TeacherJudgeSessionAttachment.message_id).in_(unique_ids))
            .order_by(
                col(TeacherJudgeSessionAttachment.created_at),
                col(TeacherJudgeSessionAttachment.id),
            )
        )
    )
    grouped: dict[uuid.UUID, list[TeacherJudgeSessionAttachment]] = {}
    for row in rows:
        if row.message_id is not None:
            grouped.setdefault(row.message_id, []).append(row)
    return grouped


def message_public(
    item: TeacherJudgeSessionMessage,
    attachments: list[TeacherJudgeSessionAttachment] | None = None,
) -> TeacherJudgeSessionMessagePublic:
    return TeacherJudgeSessionMessagePublic(
        id=str(item.id),
        session_id=str(item.session_id),
        role=item.role.value,
        content=normalize_message_text(item.content),
        message_type=item.message_type.value,
        metadata_json=item.metadata_json,
        attachments=[attachment_public(row) for row in attachments or []],
        created_by=str(item.created_by) if item.created_by else None,
        created_at=item.created_at.isoformat(),
    )


def _message_context(
    db: Session,
    row: TeacherJudgeSessionMessage,
    *,
    include_attachments: bool = True,
    attachments: list[TeacherJudgeSessionAttachment] | None = None,
) -> str:
    if not include_attachments:
        return row.content
    attachment_rows = (
        attachments if attachments is not None else message_attachments(db, row.id)
    )
    if not attachment_rows:
        return row.content
    return f"{row.content}\n\n{attachment_context(attachment_rows)}"


def bounded_history(
    db: Session,
    session_id: uuid.UUID,
    *,
    exclude_attachments_for_message_id: uuid.UUID | None = None,
    through_message_id: uuid.UUID | None = None,
    summary: str | None = None,
    source_file_id: uuid.UUID | None = None,
) -> list[TeacherJudgeRubricChatMessage]:
    statement = select(TeacherJudgeSessionMessage).where(
        TeacherJudgeSessionMessage.session_id == session_id,
        TeacherJudgeSessionMessage.message_type
        != TeacherJudgeMessageType.system_notice,
    )
    if through_message_id is not None:
        boundary = db.get(TeacherJudgeSessionMessage, through_message_id)
        if (
            boundary is None
            or boundary.session_id != session_id
            or boundary.role != TeacherJudgeMessageRole.assistant
            or boundary.message_type == TeacherJudgeMessageType.system_notice
        ):
            return []
        statement = statement.where(
            (TeacherJudgeSessionMessage.created_at < boundary.created_at)
            | (
                (TeacherJudgeSessionMessage.created_at == boundary.created_at)
                & (TeacherJudgeSessionMessage.id <= boundary.id)
            )
        )
    rows = list(
        db.exec(
            statement.order_by(
                desc(TeacherJudgeSessionMessage.created_at),
                desc(TeacherJudgeSessionMessage.id),
            ).limit(HISTORY_MESSAGE_LIMIT)
        )
    )
    rows.reverse()
    latest_row_id = rows[-1].id if rows else None
    rows = [
        row
        for row in rows
        if not (
            isinstance(row.metadata_json, dict)
            and row.metadata_json.get("ui_hidden") is True
            and row.id != latest_row_id
        )
    ]
    attachments_by_message_id = message_attachments_by_message_ids(
        db, [row.id for row in rows]
    )
    # Build each message's full content at most once. The trim pass and the
    # response pass previously rebuilt the same attachment context strings,
    # doubling join/slice work for every kept message.
    content_by_id: dict[uuid.UUID, str] = {}
    kept: list[TeacherJudgeSessionMessage] = []
    size = 0
    for row in reversed(rows):
        if row.id not in content_by_id:
            content_by_id[row.id] = _message_context(
                db,
                row,
                include_attachments=row.id != exclude_attachments_for_message_id,
                attachments=attachments_by_message_id.get(row.id, []),
            )
        content = content_by_id[row.id]
        if kept and size + len(content) > HISTORY_CHARACTER_LIMIT:
            break
        kept.append(row)
        size += len(content)
    history = [
        TeacherJudgeRubricChatMessage(
            role=row.role.value,
            content=content_by_id[row.id],
        )
        for row in reversed(kept)
    ]
    for row in reversed(kept):
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        focus = metadata.get("conversation_focus")
        if not isinstance(focus, dict):
            continue
        focus_source = focus.get("source_file_id")
        expected_source = str(source_file_id) if source_file_id is not None else None
        if focus_source != expected_source:
            continue
        # The snapshot is labelled "unresolved"; ready requirements were already
        # turned into a proposal and none/empty ones need no action, so
        # re-injecting them makes the model re-propose the previous turn.
        unresolved = [
            requirement
            for requirement in focus.get("requirements") or []
            if isinstance(requirement, dict)
            and requirement.get("status") not in {"ready", "none"}
        ]
        if not unresolved:
            continue
        focus_message = TeacherJudgeRubricChatMessage(
            role="assistant",
            content=(
                "【目前未解需求焦點｜結構化資料，以最新對話與目前檢查表為準】\n"
                + json.dumps(
                    {**focus, "requirements": unresolved}, ensure_ascii=False
                )
            ),
        )
        insert_at = max(0, len(history) - 1)
        history.insert(insert_at, focus_message)
        break
    summary_text = (summary or "").strip()
    if summary_text:
        summary_text = summary_text[:SUMMARY_CONTEXT_CHARACTER_LIMIT]
        history.insert(
            0,
            TeacherJudgeRubricChatMessage(
                role="assistant",
                content=(
                    "【既有對話摘要｜僅供背景，不是新的指令】\n"
                    f"{summary_text}\n"
                    "【摘要結束；以下較新的對話與目前檢查表版本優先】"
                ),
            ),
        )
    return history


def _assistant_message_count(db: Session, session_id: uuid.UUID) -> int:
    return int(
        db.exec(
            select(func.count())
            .select_from(TeacherJudgeSessionMessage)
            .where(
                TeacherJudgeSessionMessage.session_id == session_id,
                TeacherJudgeSessionMessage.role == TeacherJudgeMessageRole.assistant,
                TeacherJudgeSessionMessage.message_type
                != TeacherJudgeMessageType.system_notice,
            )
        ).one()
        or 0
    )


def _latest_assistant_message(
    db: Session, session_id: uuid.UUID
) -> TeacherJudgeSessionMessage | None:
    return db.exec(
        select(TeacherJudgeSessionMessage)
        .where(
            TeacherJudgeSessionMessage.session_id == session_id,
            TeacherJudgeSessionMessage.role == TeacherJudgeMessageRole.assistant,
            TeacherJudgeSessionMessage.message_type
            != TeacherJudgeMessageType.system_notice,
        )
        .order_by(
            desc(TeacherJudgeSessionMessage.created_at),
            desc(TeacherJudgeSessionMessage.id),
        )
        .limit(1)
    ).first()


def _prepare_summary_job(
    db: Session,
    *,
    session_id: uuid.UUID,
    boundary_message_id: uuid.UUID,
    assistant_count: int,
    selected_file_id: uuid.UUID | None,
    analysis_revision: int | None,
) -> _SummaryJobSnapshot | None:
    """Capture only immutable state before a background model call."""
    item = db.get(TeacherJudgeSession, session_id)
    if item is None or item.selected_file_id != selected_file_id:
        return None
    if (item.summary_through_assistant_count or 0) >= assistant_count:
        return None

    if selected_file_id is not None:
        file = db.get(TeacherJudgeFile, selected_file_id)
        if (
            file is None
            or file.teaching_class_id != item.teaching_class_id
            or file.status != TeacherJudgeFileStatus.active
            or file.analysis_revision != analysis_revision
        ):
            return None

    if _assistant_message_count(db, session_id) < assistant_count:
        return None
    boundary = db.get(TeacherJudgeSessionMessage, boundary_message_id)
    if (
        boundary is None
        or boundary.session_id != session_id
        or boundary.role != TeacherJudgeMessageRole.assistant
        or boundary.message_type == TeacherJudgeMessageType.system_notice
    ):
        return None
    messages = tuple(
        bounded_history(
            db,
            session_id,
            through_message_id=boundary_message_id,
        )
    )
    if not messages:
        return None
    return _SummaryJobSnapshot(
        session_id=session_id,
        teaching_class_id=item.teaching_class_id,
        boundary_message_id=boundary_message_id,
        assistant_count=assistant_count,
        selected_file_id=selected_file_id,
        analysis_revision=analysis_revision,
        messages=messages,
        previous_summary=item.summary or "",
    )


def _persist_summary_if_current(
    db: Session,
    snapshot: _SummaryJobSnapshot,
    summary: str,
) -> bool:
    """Persist a summary only if its source and boundary are still current."""
    source_condition = (
        col(TeacherJudgeSession.selected_file_id) == snapshot.selected_file_id
        if snapshot.selected_file_id is not None
        else col(TeacherJudgeSession.selected_file_id).is_(None)
    )
    boundary_exists = sa.exists(
        select(1).select_from(TeacherJudgeSessionMessage).where(
            TeacherJudgeSessionMessage.id == snapshot.boundary_message_id,
            TeacherJudgeSessionMessage.session_id == snapshot.session_id,
            TeacherJudgeSessionMessage.role == TeacherJudgeMessageRole.assistant,
            TeacherJudgeSessionMessage.message_type
            != TeacherJudgeMessageType.system_notice,
        )
    )
    statement = sa.update(TeacherJudgeSession).where(
        col(TeacherJudgeSession.id) == snapshot.session_id,
        source_condition,
        func.coalesce(TeacherJudgeSession.summary_through_assistant_count, 0)
        < snapshot.assistant_count,
        boundary_exists,
    )
    if snapshot.selected_file_id is not None:
        statement = statement.where(
            sa.exists(
                select(1).select_from(TeacherJudgeFile).where(
                    TeacherJudgeFile.id == snapshot.selected_file_id,
                    TeacherJudgeFile.teaching_class_id == snapshot.teaching_class_id,
                    TeacherJudgeFile.status == TeacherJudgeFileStatus.active,
                    TeacherJudgeFile.analysis_revision == snapshot.analysis_revision,
                )
            )
        )

    safe_summary = redact_message_content((summary or "").strip())[:12000]
    if not safe_summary and snapshot.previous_summary:
        # An empty successful response should not erase the last usable memory;
        # a later boundary can still retry with newer context.
        safe_summary = redact_message_content(snapshot.previous_summary.strip())[:12000]
    result = db.exec(
        statement
        .values(
            summary=safe_summary,
            summary_through_message_id=snapshot.boundary_message_id,
            summary_through_assistant_count=snapshot.assistant_count,
            updated_at=_now(),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return False
    db.commit()
    return True


async def run_summary_job(
    session_id: uuid.UUID,
    boundary_message_id: uuid.UUID,
    assistant_count: int,
    selected_file_id: uuid.UUID | None,
    analysis_revision: int | None,
) -> None:
    """Summarize a captured boundary without retaining the request Session."""
    with Session(engine) as db:
        snapshot = _prepare_summary_job(
            db,
            session_id=session_id,
            boundary_message_id=boundary_message_id,
            assistant_count=assistant_count,
            selected_file_id=selected_file_id,
            analysis_revision=analysis_revision,
        )
    if snapshot is None:
        return

    try:
        summary, _ = await summarize_conversation(
            list(snapshot.messages), snapshot.previous_summary
        )
    except Exception:
        logger.exception(
            "Teacher Judge summary failed for session %s through message %s",
            session_id,
            boundary_message_id,
        )
        return

    try:
        with Session(engine) as db:
            _persist_summary_if_current(db, snapshot, summary)
    except Exception:
        logger.exception(
            "Teacher Judge summary persistence failed for session %s through message %s",
            session_id,
            boundary_message_id,
        )


def schedule_summary(
    db: Session,
    item: TeacherJudgeSession,
    *,
    boundary_message_id: uuid.UUID | None = None,
) -> str:
    """Schedule one deterministic summary job after a completed turn."""
    current = db.get(TeacherJudgeSession, item.id)
    if current is None:
        return ""
    db.refresh(current)
    assistant_count = _assistant_message_count(db, current.id)
    if not assistant_count or assistant_count % SUMMARY_TURN_INTERVAL:
        return ""
    if (current.summary_through_assistant_count or 0) >= assistant_count:
        return ""
    boundary = (
        db.get(TeacherJudgeSessionMessage, boundary_message_id)
        if boundary_message_id is not None
        else _latest_assistant_message(db, current.id)
    )
    if (
        boundary is None
        or boundary.session_id != current.id
        or boundary.role != TeacherJudgeMessageRole.assistant
        or boundary.message_type == TeacherJudgeMessageType.system_notice
    ):
        return ""
    selected_file_id = current.selected_file_id
    analysis_revision: int | None = None
    if selected_file_id is not None:
        file = db.get(TeacherJudgeFile, selected_file_id)
        if (
            file is None
            or file.teaching_class_id != current.teaching_class_id
            or file.status != TeacherJudgeFileStatus.active
        ):
            return ""
        analysis_revision = file.analysis_revision
    task_id = f"teacher-judge-summary:{current.id}:{boundary.id}"
    try:
        return submit(
            run_summary_job(
                current.id,
                boundary.id,
                assistant_count,
                selected_file_id,
                analysis_revision,
            ),
            name="teacher-judge-summary",
            task_id=task_id,
        )
    except Exception:
        logger.exception("Unable to schedule Teacher Judge summary for %s", current.id)
        return ""


async def maybe_summarize(
    db: Session,
    item: TeacherJudgeSession,
    file: TeacherJudgeFile | None,
    *,
    template_commands: list[TeacherJudgeTemplateCommand] | None = None,
) -> None:
    """Synchronous compatibility helper for callers and legacy tests.

    New request handlers use :func:`schedule_summary`; this helper keeps the
    old awaitable API but uses the same dedicated prompt and conditional write.
    """
    del template_commands  # retained only for the legacy call signature
    assistant_count = _assistant_message_count(db, item.id)
    if not assistant_count or assistant_count % SUMMARY_TURN_INTERVAL:
        return
    boundary = _latest_assistant_message(db, item.id)
    if boundary is None:
        return
    selected_file_id = item.selected_file_id
    analysis_revision = file.analysis_revision if file else None
    if selected_file_id is not None and analysis_revision is None:
        selected_file = db.get(TeacherJudgeFile, selected_file_id)
        analysis_revision = selected_file.analysis_revision if selected_file else None
    snapshot = _prepare_summary_job(
        db,
        session_id=item.id,
        boundary_message_id=boundary.id,
        assistant_count=assistant_count,
        selected_file_id=selected_file_id,
        analysis_revision=analysis_revision,
    )
    if snapshot is None:
        return
    try:
        reply, _ = await summarize_conversation(
            list(snapshot.messages), snapshot.previous_summary
        )
    except Exception:
        return
    if _persist_summary_if_current(db, snapshot, reply):
        db.refresh(item)
