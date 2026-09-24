"""Persistent Teacher Judge session workflow."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypedDict

import sqlalchemy as sa
from fastapi import HTTPException
from sqlmodel import Session, col, desc, func, select

from app.ai.teacher_judge.attachment_service import (
    attachment_compact_context,
    attachment_context,
    attachment_public,
    storage_path,
)
from app.ai.teacher_judge.automation_support import (
    AutomationSupportBlocker,
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
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricChatMessage,
    TeacherJudgeRubricItem,
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

logger = logging.getLogger(__name__)

HISTORY_MESSAGE_LIMIT = 20
HISTORY_CHARACTER_LIMIT = 24000
SUMMARY_TURN_INTERVAL = 10
SUMMARY_CONTEXT_CHARACTER_LIMIT = 8000
WORKFLOW_ITEM_LIMIT = 50
WORKFLOW_FOCUS_LIMIT = 8
WORKFLOW_TEXT_LIMIT = 240
WORKFLOW_CONTENT_LIMIT = 4000
_FOCUS_RESOLVED_STATUSES = frozenset({"ready", "teacher_review", "none", "resolved"})
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


class WorkflowMessage(TypedDict):
    """Safe teacher-facing projection of a workflow result."""

    content: str
    metadata: dict[str, Any]


def _workflow_text(value: Any, limit: int = WORKFLOW_TEXT_LIMIT) -> str:
    """Normalize a value before placing it in a teacher-facing projection."""
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    text = " ".join(str(value or "").split())
    return redact_message_content(text)[:limit]


def _workflow_issue_text(value: Any) -> str:
    """Keep review summaries readable without copying code or raw output."""
    if not isinstance(value, str):
        return ""
    lowered = value.lower()
    if (
        "```" in value
        or "traceback" in lowered
        or "script_content" in lowered
        or "stdout:" in lowered
        or "stderr:" in lowered
    ):
        return "審查回報含詳細內容，已省略原文。"
    return _workflow_text(value, WORKFLOW_TEXT_LIMIT)


def _workflow_source_revision(
    source_file_id: uuid.UUID | str | None,
    analysis_revision: int | None,
) -> tuple[str | None, int | None]:
    source = str(source_file_id) if source_file_id is not None else None
    return source, analysis_revision


def _workflow_item_id(row: dict[str, Any]) -> str | None:
    item_id = row.get("item_id") or row.get("target_item_id") or row.get("id")
    operation = row.get("operation")
    if item_id is None and isinstance(operation, dict):
        item_id = operation.get("id")
    if isinstance(operation, dict) and isinstance(operation.get("item"), dict):
        item_id = item_id or operation["item"].get("id")
    if item_id is None and isinstance(row.get("source_index"), int):
        item_id = f"attachment-item-{row['source_index']}"
    value = _workflow_text(item_id, 120)
    return value or None


def _workflow_status(status: Any) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"missing_info", "partial", "needs_information"}:
        return "needs_information"
    if normalized in {"manual", "unsupported"}:
        return "unsupported"
    if normalized in {"analysis_error", "failed", "error"}:
        return "analysis_error"
    if normalized in {"ready", "teacher_review", "none", "resolved"}:
        return normalized
    return "analysis_error"


def _workflow_item_result(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    status = _workflow_status(row.get("status"))
    title = (
        _workflow_text(row.get("title") or row.get("source_label"))
        or "未命名項目"
    )
    item_id = _workflow_item_id(row)
    missing = row.get("missing_information")
    missing_information = [
        _workflow_text(value, 120)
        for value in (missing if isinstance(missing, list) else [])
        if _workflow_text(value, 120)
    ][:5]
    detail = _workflow_issue_text(row.get("detail"))
    reason_code = _workflow_text(
        row.get("reason_code")
        or (
            "automatic_detection_information_missing"
            if status == "needs_information"
            else "automatic_detection_unsupported"
            if status == "unsupported"
            else "teacher_judge_analysis_failed"
            if status == "analysis_error"
            else ""
        ),
        100,
    )
    known = row.get("known_information")
    known_information = [
        _workflow_text(value, 120)
        for value in (known if isinstance(known, list) else [])
        if _workflow_text(value, 120)
    ][:3]
    description = _workflow_text(row.get("description"), WORKFLOW_TEXT_LIMIT)
    if description and not known_information:
        known_information = [description]

    result: dict[str, Any] = {
        "item_id": item_id,
        "title": title,
        "status": status,
        "missing_information": missing_information,
        "reason_code": reason_code,
    }
    source_index = row.get("source_index")
    if isinstance(source_index, int) and source_index > 0:
        result["source_index"] = source_index
    source_label = _workflow_text(row.get("source_label"), 80)
    if source_label:
        result["source_label"] = source_label
    if known_information:
        result["known_information"] = known_information
    if detail:
        result["detail"] = detail
    # ProposalPanel uses the operation to match a row back to a staged item.
    # Keep only the normalized item identity and operation, never arbitrary model
    # payloads or raw attachment text in the compact workflow projection.
    operation = row.get("operation")
    if isinstance(operation, dict):
        operation_id = _workflow_item_id({"operation": operation})
        if operation_id:
            result["operation"] = {"id": operation_id}
    return result


def normalize_workflow_item_results(
    item_results: list[Any] | None,
) -> list[dict[str, Any]]:
    """Return the bounded, teacher-safe rows shared by UI and history metadata."""
    return [
        result
        for row in (item_results or [])[:WORKFLOW_ITEM_LIMIT]
        if (result := _workflow_item_result(row)) is not None
    ]


def conversation_focus_from_item_results(
    item_results: list[Any] | None,
    *,
    source_file_id: uuid.UUID | str | None = None,
    analysis_revision: int | None = None,
    turn_kind: str = "follow_up",
) -> dict[str, Any]:
    """Project itemwise or server blocker rows into compact next-turn memory."""
    source, revision = _workflow_source_revision(source_file_id, analysis_revision)
    normalized_rows = normalize_workflow_item_results(item_results)
    unresolved: list[dict[str, Any]] = []
    for index, row in enumerate(normalized_rows):
        status = row["status"]
        if status in _FOCUS_RESOLVED_STATUSES:
            continue
        focus_key = row.get("item_id") or f"workflow-item-{index + 1}"
        requirement: dict[str, Any] = {
            "focus_key": _workflow_text(focus_key, 80),
            "status": status,
            "known_information": row.get("known_information", []),
            "missing_information": row.get("missing_information", []),
            "reason_code": row.get("reason_code", ""),
        }
        if row.get("item_id"):
            requirement["target_item_id"] = row["item_id"]
        if row.get("detail"):
            requirement["detail"] = _workflow_text(row["detail"], 240)
        unresolved.append(requirement)
        if len(unresolved) >= WORKFLOW_FOCUS_LIMIT:
            break

    # A resolved snapshot is intentional: it prevents bounded_history from
    # reviving an older unresolved turn for the same source/revision.
    if not unresolved:
        unresolved = [
            {
                "focus_key": "workflow",
                "status": "none",
                "known_information": [],
                "missing_information": [],
                "reason_code": "",
            }
        ]
    safe_turn_kind = turn_kind if turn_kind in {"question", "requirement", "follow_up"} else "follow_up"
    return {
        "turn_kind": safe_turn_kind,
        "source_file_id": source,
        "analysis_revision": revision,
        "requirements": unresolved[:WORKFLOW_FOCUS_LIMIT],
    }


def _workflow_blocker_line(row: dict[str, Any]) -> str:
    """Render one bounded, actionable readiness explanation for a teacher."""
    title = _workflow_text(row.get("title")) or "未命名項目"
    status = _workflow_status(row.get("status"))
    if status == "needs_information":
        missing = "、".join(row.get("missing_information") or [])
        return f"「{title}」已確認檢查目標，但還缺少：{missing or '會影響檢查範圍或判定的資訊'}。"
    if status == "unsupported":
        detail = _workflow_issue_text(row.get("detail"))
        if detail:
            return f"「{title}」目前無法安全自動取得證據：{detail.rstrip('。')}。"
        return (
            f"「{title}」目前沒有可安全執行的取證方式；"
            "請改由導師查看，或補充可讀取的檔案、服務或命令結果。"
        )
    if status == "analysis_error":
        detail = _workflow_issue_text(row.get("detail"))
        if detail:
            return f"「{title}」這次核對未完成：{detail.rstrip('。')}。"
        return f"「{title}」這次核對未完成，請稍後重試。"
    return f"「{title}」目前不需要額外處理。"


_WORKFLOW_RESOLVED_REPLY_MARKERS = (
    "可開始製作",
    "可以開始製作",
    "所有檢查項目",
    "全部檢查項目",
    "狀態良好",
    "已通過",
    "ready",
)


def _workflow_ai_issue_summary(value: Any) -> str:
    """Keep useful AI issue context without letting it override server gates."""
    summary = _workflow_issue_text(value)
    if not summary:
        return ""
    lowered = summary.casefold()
    if any(marker.casefold() in lowered for marker in _WORKFLOW_RESOLVED_REPLY_MARKERS):
        return ""
    return summary


def _workflow_metadata(
    *,
    status: str,
    stage: str,
    source_file_id: uuid.UUID | str | None,
    analysis_revision: int | None,
    item_results: list[dict[str, Any]] | None = None,
    conversation_focus: dict[str, Any] | None = None,
    reason_code: str | None = None,
    script_ready: bool | None = None,
    artifact_id: uuid.UUID | str | None = None,
    issue_summary: list[str] | None = None,
) -> dict[str, Any]:
    source, revision = _workflow_source_revision(source_file_id, analysis_revision)
    metadata: dict[str, Any] = {
        "status": status,
        "stage": stage,
        "source_file_id": source,
        "analysis_revision": revision,
    }
    if item_results is not None:
        metadata["item_results"] = [
            row for row in item_results[:WORKFLOW_ITEM_LIMIT] if isinstance(row, dict)
        ]
    if conversation_focus is not None:
        metadata["conversation_focus"] = conversation_focus
    if reason_code:
        metadata["reason_code"] = _workflow_text(reason_code, 100)
    if script_ready is not None:
        metadata["script_ready"] = script_ready
    if artifact_id is not None:
        metadata["artifact_id"] = str(artifact_id)
    if issue_summary:
        metadata["issue_summary"] = [
            _workflow_text(issue, WORKFLOW_TEXT_LIMIT)
            for issue in issue_summary[:3]
            if _workflow_text(issue, WORKFLOW_TEXT_LIMIT)
        ]
    return metadata


def reanalysis_workflow_message(
    blockers: list[AutomationSupportBlocker] | None,
    *,
    source_file_id: uuid.UUID | str | None,
    analysis_revision: int | None,
    proposal: list[dict[str, Any]] | None = None,
    assistant_reply: str | None = None,
) -> WorkflowMessage:
    """Build the single safe projection used by refine Chat and page notices."""
    blocker_rows = normalize_workflow_item_results(
        [dict(blocker) for blocker in (blockers or [])]
    )
    proposal_rows: list[dict[str, Any]] = []
    for raw in (proposal or [])[:WORKFLOW_ITEM_LIMIT]:
        if not isinstance(raw, dict):
            continue
        candidate = raw.get("item")
        candidate = dict(candidate) if isinstance(candidate, dict) else dict(raw)
        operation = str(
            raw.get("operation") or raw.get("action") or raw.get("op") or ""
        ).lower()
        item_id = _workflow_item_id(candidate)
        if not item_id:
            continue
        candidate["item_id"] = item_id
        candidate["operation"] = {"id": item_id}
        candidate["status"] = "ready" if operation in {"delete", "remove"} else raw.get("status")
        if not candidate["status"]:
            detectable = str(candidate.get("detectable") or "").strip().lower()
            candidate["status"] = (
                "unsupported"
                if detectable == "manual"
                else "needs_information"
                if (
                    detectable == "partial"
                    or not _workflow_text(candidate.get("detection_method"), 1)
                    or not isinstance(candidate.get("check_steps"), list)
                    or not candidate.get("check_steps")
                )
                else "teacher_review"
                if str(candidate.get("judgement_mode") or "ai") == "teacher"
                else "ready"
            )
        if (result := _workflow_item_result(candidate)) is not None:
            proposal_rows.append(result)

    rows_by_item_id = {
        row["item_id"]: row
        for row in proposal_rows
        if row.get("item_id")
    }
    for blocker in blocker_rows:
        item_id = blocker.get("item_id")
        if item_id and item_id in rows_by_item_id:
            proposal_row = rows_by_item_id[item_id]
            proposal_row.update(blocker)
            if "operation" in rows_by_item_id[item_id]:
                proposal_row["operation"] = rows_by_item_id[item_id]["operation"]
        else:
            proposal_rows.append(blocker)
    workflow_rows = proposal_rows
    focus = conversation_focus_from_item_results(
        workflow_rows,
        source_file_id=source_file_id,
        analysis_revision=analysis_revision,
        turn_kind="follow_up",
    )
    if blocker_rows:
        lines = ["重新核對後，以下項目還需要處理："]
        summary = _workflow_ai_issue_summary(assistant_reply)
        if summary and "重新核對後" not in summary:
            lines.append(summary)
        for row in blocker_rows[:WORKFLOW_FOCUS_LIMIT]:
            lines.append(_workflow_blocker_line(row))
        lines.append(
            "檢查表已保留；請依上列缺口補充資訊或調整檢查方式後，再重新製作腳本。"
        )
        status = (
            "analysis_error"
            if any(row["status"] == "analysis_error" for row in blocker_rows)
            else "needs_information"
            if any(row["status"] == "needs_information" for row in blocker_rows)
            else "unsupported"
        )
        return {
            "content": "\n".join(lines)[:WORKFLOW_CONTENT_LIMIT],
            "metadata": _workflow_metadata(
                status=status,
                stage="reanalysis",
                source_file_id=source_file_id,
                analysis_revision=analysis_revision,
                item_results=workflow_rows,
                conversation_focus=focus,
                reason_code=(
                    "check_plan_contract_invalid"
                    if status == "analysis_error"
                    else "automatic_detection_information_missing"
                    if status == "needs_information"
                    else "automatic_detection_unsupported"
                ),
                script_ready=False,
            ),
        }

    return {
        "content": (
            "重新核對已完成，所有檢查項目都具備腳本所需的取證資訊；"
            "目前檢查表已保留，可開始製作檢查腳本。"
        ),
        "metadata": _workflow_metadata(
            status="resolved",
            stage="reanalysis",
            source_file_id=source_file_id,
            analysis_revision=analysis_revision,
            item_results=workflow_rows,
            conversation_focus=focus,
            reason_code="reanalysis_ready",
            script_ready=True,
        ),
    }


def script_blocker_workflow_message(
    blockers: list[AutomationSupportBlocker] | list[dict[str, Any]] | None,
    *,
    source_file_id: uuid.UUID | str | None,
    analysis_revision: int | None,
) -> WorkflowMessage:
    """Format deterministic script preflight blockers for Chat persistence."""
    rows = [
        result
        for blocker in (blockers or [])
        if isinstance(blocker, dict)
        and (result := _workflow_item_result(blocker)) is not None
    ]
    focus = conversation_focus_from_item_results(
        rows,
        source_file_id=source_file_id,
        analysis_revision=analysis_revision,
        turn_kind="follow_up",
    )
    lines = ["目前檢查表尚未具備製作檢查腳本的條件："]
    for row in rows[:WORKFLOW_FOCUS_LIMIT]:
        lines.append(_workflow_blocker_line(row))
    lines.append(
        "檢查表已保留；請依上列缺口補充資訊或調整檢查方式後，再重新製作腳本。"
    )
    status = (
        "needs_information"
        if any(row["status"] == "needs_information" for row in rows)
        else "unsupported"
        if rows
        else "analysis_error"
    )
    return {
        "content": "\n".join(lines)[:WORKFLOW_CONTENT_LIMIT],
        "metadata": _workflow_metadata(
            status=status,
            stage="script_preflight",
            source_file_id=source_file_id,
            analysis_revision=analysis_revision,
            item_results=rows,
            conversation_focus=focus,
            reason_code="teacher_judge_script_not_ready",
            script_ready=False,
        ),
    }


def workflow_error_message(
    *,
    stage: str,
    status_code: int | None = None,
    source_file_id: uuid.UUID | str | None = None,
    analysis_revision: int | None = None,
    reason_code: str | None = None,
) -> WorkflowMessage:
    """Return a sanitized processing failure without blaming the teacher."""
    code = reason_code
    if not code:
        code = (
            f"{stage}_timeout"
            if status_code == 504
            else f"{stage}_unavailable"
            if status_code in {502, 503}
            else f"{stage}_failed"
        )
    stage_labels = {
        "reanalysis": "AI 重新核對",
        "script_generation": "腳本製作",
        "script_review": "腳本審查",
        "persistence": "檢查表保存",
    }
    label = stage_labels.get(stage, "系統處理")
    focus = conversation_focus_from_item_results(
        [
            {
                "item_id": f"workflow-{stage}",
                "title": label,
                "status": "analysis_error",
                "known_information": ["目前檢查表已保留"],
                "missing_information": [],
                "reason_code": code,
            }
        ],
        source_file_id=source_file_id,
        analysis_revision=analysis_revision,
        turn_kind="follow_up",
    )
    if code == "analysis_revision_conflict":
        content = (
            "目前檢查表已有較新的保存版本，這次結果沒有覆蓋它。"
            "現有檢查表已保留，未核准的腳本不會開放執行；請重新載入後再試一次。"
        )
    else:
        content = (
            f"這次{label}在處理階段沒有成功。現有檢查表已保留，"
            "未核准的腳本不會開放執行。這不是缺少你的資料；可以稍後重試，"
            "或在聊天室詢問目前已知的處理階段。"
        )
    return {
        "content": content[:WORKFLOW_CONTENT_LIMIT],
        "metadata": _workflow_metadata(
            status="analysis_error",
            stage=stage,
            source_file_id=source_file_id,
            analysis_revision=analysis_revision,
            conversation_focus=focus,
            reason_code=code,
            script_ready=False,
        ),
    }


def apply_proposal_operations_to_analysis(
    analysis: TeacherJudgeRubricAnalysis,
    proposal: list[dict[str, Any]] | None,
) -> TeacherJudgeRubricAnalysis:
    """Apply server proposal semantics for readiness checks without persistence."""
    by_id: dict[str, dict[str, Any]] = {
        item.id: item.model_dump(mode="python") for item in analysis.items
    }
    for raw in proposal or []:
        if not isinstance(raw, dict):
            continue
        nested = raw.get("item")
        candidate = dict(nested) if isinstance(nested, dict) else dict(raw)
        operation = str(
            raw.get("operation") or raw.get("action") or raw.get("op") or ""
        ).lower()
        item_id = str(candidate.get("id") or "").strip()
        if operation in {"delete", "remove"}:
            if item_id:
                by_id.pop(item_id, None)
            continue
        if not item_id:
            continue
        candidate.pop("operation", None)
        candidate.pop("action", None)
        if item_id in by_id:
            merged = {**by_id[item_id], **candidate}
        else:
            merged = candidate
        try:
            normalized = TeacherJudgeRubricItem.model_validate(merged)
        except Exception:
            # The proposal validator is the source of truth; malformed rows are
            # ignored here and remain blocked by the existing persisted rubric.
            continue
        by_id[item_id] = normalized.model_dump(mode="python")
    items = [TeacherJudgeRubricItem.model_validate(value) for value in by_id.values()]
    return analysis.model_copy(
        update={
            "items": items,
            "total_items": len(items),
            "checked_count": sum(1 for item in items if item.checked),
            "auto_count": sum(1 for item in items if item.detectable == "auto"),
            "partial_count": sum(1 for item in items if item.detectable == "partial"),
            "manual_count": sum(1 for item in items if item.detectable == "manual"),
            "detectability_needs_review": False,
            "pending_review_item_ids": [],
        }
    )


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
    compact_attachments: bool = False,
) -> str:
    if not include_attachments:
        return row.content
    attachment_rows = (
        attachments if attachments is not None else message_attachments(db, row.id)
    )
    if not attachment_rows:
        return row.content
    if compact_attachments:
        return f"{row.content}\n\n{attachment_compact_context(attachment_rows)}"
    return f"{row.content}\n\n{attachment_context(attachment_rows)}"


def bounded_history(
    db: Session,
    session_id: uuid.UUID,
    *,
    exclude_attachments_for_message_id: uuid.UUID | None = None,
    through_message_id: uuid.UUID | None = None,
    summary: str | None = None,
    source_file_id: uuid.UUID | None = None,
    analysis_revision: int | None = None,
    summary_through_message_id: uuid.UUID | None = None,
    compact_history_attachments: bool = True,
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
    if summary_through_message_id is not None:
        # P3: drop messages already covered by the persisted summary so the
        # model does not receive "summary + summarized originals" twice.
        # Invalid/stale boundaries are ignored to keep chat fail-open.
        summary_boundary = db.get(
            TeacherJudgeSessionMessage, summary_through_message_id
        )
        if (
            summary_boundary is not None
            and summary_boundary.session_id == session_id
            and summary_boundary.role == TeacherJudgeMessageRole.assistant
            and summary_boundary.message_type != TeacherJudgeMessageType.system_notice
        ):
            statement = statement.where(
                (TeacherJudgeSessionMessage.created_at > summary_boundary.created_at)
                | (
                    (TeacherJudgeSessionMessage.created_at == summary_boundary.created_at)
                    & (TeacherJudgeSessionMessage.id > summary_boundary.id)
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
            # P3: past attachments are already extracted into item_results;
            # keep full text only for the newest message to avoid re-injecting
            # the same 12k*5 chars on every turn.
            compact = bool(
                compact_history_attachments and row.id != latest_row_id
            )
            content_by_id[row.id] = _message_context(
                db,
                row,
                include_attachments=row.id != exclude_attachments_for_message_id,
                attachments=attachments_by_message_id.get(row.id, []),
                compact_attachments=compact,
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
    # ``kept`` is newest-first.  The first matching focus is the authoritative
    # snapshot for this source; never revive an older unresolved requirement
    # after a newer resolved turn.
    for row in kept:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        focus = metadata.get("conversation_focus")
        if not isinstance(focus, dict):
            continue
        focus_source = focus.get("source_file_id")
        expected_source = str(source_file_id) if source_file_id is not None else None
        if focus_source is not None:
            focus_source = str(focus_source)
        if focus_source != expected_source:
            continue
        focus_revision = focus.get("analysis_revision")
        if (
            analysis_revision is not None
            and focus_revision is not None
            and str(focus_revision) != str(analysis_revision)
        ):
            continue
        # The snapshot is labelled "unresolved"; ready requirements were already
        # turned into a proposal and none/empty ones need no action, so
        # re-injecting them makes the model re-propose the previous turn.
        unresolved = [
            requirement
            for requirement in focus.get("requirements") or []
            if isinstance(requirement, dict)
            and requirement.get("status") not in _FOCUS_RESOLVED_STATUSES
        ]
        if not unresolved:
            break
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
            # Summarization needs the original text, not the P3 compact placeholder.
            compact_history_attachments=False,
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
