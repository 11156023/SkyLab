"""Teacher Judge 對話訊息流程中與 HTTP 無關的步驟。

``POST /teaching-classes/{id}/judge/sessions/{id}/messages`` 的路由只負責
存取檢查、使用者訊息落地、交易邊界與呼叫模型；這裡放它用到的判斷與組裝：

- 舊版檢查表是否仍靠範本指令（決定要不要把指令清單餵給模型）
- 模型提案的執行節點／觀察節點是否屬於本班機器
- refine 回合的腳本就緒檢查
- 助理訊息的 metadata（含附件逐項核查的整體狀態）
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session

from app.ai.teacher_judge.automation_support import get_script_generation_blockers
from app.ai.teacher_judge.machine_context import (
    load_class_machine_nodes,
    rubric_item_machine_issues,
)
from app.ai.teacher_judge.schemas import TeacherJudgeRubricAnalysis
from app.ai.teacher_judge.session_service import (
    WorkflowMessage,
    apply_proposal_operations_to_analysis,
    conversation_focus_from_item_results,
    normalize_workflow_item_results,
    reanalysis_workflow_message,
)
from app.core.i18n import t
from app.models.teacher_judge_file import TeacherJudgeFile
from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand

logger = logging.getLogger(__name__)

_UNNAMED_ITEM = "未命名項目"


def uses_legacy_command_context(analysis: dict[str, Any] | None) -> bool:
    """舊版檢查表的 check_steps 會帶 template_key／command_key，需要範本指令清單。"""
    return any(
        isinstance(step, dict)
        and (step.get("template_key") or step.get("command_key"))
        for raw_item in ((analysis or {}).get("items") or [])
        if isinstance(raw_item, dict)
        for step in (raw_item.get("check_steps") or [])
    )


def prompt_template_scope(
    file: TeacherJudgeFile | None, legacy_command_context: bool
) -> tuple[str, list[str] | None]:
    """回傳餵給模型的 (template_key, environment_keys)。

    只有舊版指令情境才沿用檢查表自己的範本與環境；其餘一律視為 linux、無環境限制。
    """
    if file is None or not legacy_command_context:
        return "linux", None
    return file.template_key, file.environment_keys


def _proposal_item_label(candidate: dict[str, Any]) -> str:
    return str(candidate.get("id") or candidate.get("title") or _UNNAMED_ITEM)


def validate_proposal_machine_nodes(
    session: Session,
    teaching_class_id: uuid.UUID,
    proposal: list[dict[str, Any]],
) -> None:
    """模型提案裡的 target／peer 節點必須屬於本班，且自動檢查項目要指定執行節點。

    違反時丟 422，依序檢查：節點不屬於本班 → 缺執行節點 → 節點／peer token 契約不一致。
    """
    class_nodes = load_class_machine_nodes(session, teaching_class_id)
    valid_node_keys = {node.node_key for node in class_nodes}
    invalid_node_keys: set[str] = set()
    missing_target_item_ids: list[str] = []
    machine_contract_issues: dict[str, list[str]] = {}
    for raw in proposal:
        if not isinstance(raw, dict):
            continue
        candidate = raw.get("item")
        candidate = candidate if isinstance(candidate, dict) else raw
        node_key = str(candidate.get("target_node_key") or "").strip()
        if node_key and node_key not in valid_node_keys:
            invalid_node_keys.add(node_key)
        peer_node_key = str(candidate.get("peer_node_key") or "").strip()
        if peer_node_key and peer_node_key not in valid_node_keys:
            invalid_node_keys.add(peer_node_key)
        item_issues = rubric_item_machine_issues(candidate)
        if item_issues:
            machine_contract_issues[_proposal_item_label(candidate)] = item_issues
        if (
            class_nodes
            and str(candidate.get("detectable") or "").strip().lower() == "auto"
            and not node_key
        ):
            missing_target_item_ids.append(_proposal_item_label(candidate))
    if invalid_node_keys:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_target_node_not_in_class",
                "message": t("teacherJudgeSessions.proposalTargetNodeNotInClass"),
                "target_node_keys": sorted(invalid_node_keys),
            },
        )
    if missing_target_item_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_target_node_required",
                "message": t("teacherJudgeSessions.proposalTargetNodeRequired"),
                "item_ids": list(dict.fromkeys(missing_target_item_ids)),
            },
        )
    if machine_contract_issues:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_machine_contract_invalid",
                "message": t("teacherJudgeSessions.proposalMachineContractInvalid"),
                "items": machine_contract_issues,
            },
        )


def refine_readiness_workflow(
    session: Session,
    *,
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    file: TeacherJudgeFile,
    proposal: list[dict[str, Any]] | None,
    template_commands: list[TeacherJudgeTemplateCommand],
    reply: str,
    analysis_revision: int | None,
) -> WorkflowMessage:
    """refine 回合：以「套用提案後」的伺服器端檢查表判斷能否進入腳本製作。

    就緒與否不看模型的文字，也不靠前端各自推算。
    """
    base_analysis = TeacherJudgeRubricAnalysis.model_validate(file.analysis_json)
    candidate_analysis = apply_proposal_operations_to_analysis(base_analysis, proposal)
    readiness_nodes = load_class_machine_nodes(session, teaching_class_id)
    blockers = get_script_generation_blockers(
        candidate_analysis,
        template_commands,
        require_target_node=bool(readiness_nodes),
        require_typed_plan=True,
    )
    if blockers:
        logger.warning(
            "Teacher Judge script readiness blocked: session=%s source_file=%s "
            "revision=%s blockers=%s",
            session_id,
            file.id,
            analysis_revision,
            [
                {
                    "item_id": blocker.get("item_id"),
                    "status": blocker.get("status"),
                    "reason_code": blocker.get("reason_code"),
                }
                for blocker in blockers
            ],
        )
    return reanalysis_workflow_message(
        blockers,
        source_file_id=file.id,
        analysis_revision=analysis_revision,
        proposal=proposal,
        assistant_reply=reply,
    )


def attachment_analysis_status(item_results: list[dict[str, Any]]) -> str:
    """附件逐項核查的整體狀態：analysis_error > needs_information > unsupported > resolved。"""
    statuses = {row.get("status") for row in item_results}
    for status in ("analysis_error", "needs_information", "unsupported"):
        if status in statuses:
            return status
    return "resolved"


def build_assistant_metadata(
    *,
    metrics: Mapping[str, object],
    workflow: WorkflowMessage | None,
    item_results: list[dict[str, Any]] | None,
    itemwise_error: str | None,
    conversation_focus: dict[str, Any] | None,
    tool_calls: Any,
    source_file_id: uuid.UUID | None,
    analysis_revision: int | None,
) -> dict[str, Any]:
    """組出助理訊息的 metadata_json。

    優先序：refine 的 workflow 結果 → 附件逐項核查結果 → 一般對話的 focus。
    """
    metadata: dict[str, Any] = {"metrics": metrics}
    source_file = str(source_file_id) if source_file_id else None
    if workflow is not None:
        metadata.update(workflow["metadata"])
    elif item_results is not None:
        item_results = normalize_workflow_item_results(item_results)
        if itemwise_error:
            item_results = [
                {
                    "item_id": "attachment-analysis",
                    "title": "附件逐項核查",
                    "status": "analysis_error",
                    "missing_information": [],
                    "reason_code": "teacher_judge_attachment_analysis_failed",
                    "detail": itemwise_error,
                }
            ]
        metadata["item_results"] = item_results
        metadata["conversation_focus"] = conversation_focus_from_item_results(
            item_results,
            source_file_id=source_file_id,
            analysis_revision=analysis_revision,
            turn_kind="follow_up",
        )
        metadata.update(
            {
                "status": attachment_analysis_status(item_results),
                "stage": "attachment_analysis",
                "source_file_id": source_file,
                "analysis_revision": analysis_revision,
            }
        )
    elif conversation_focus is not None:
        metadata["conversation_focus"] = {
            **conversation_focus,
            "source_file_id": source_file,
            "analysis_revision": analysis_revision,
        }
    if tool_calls:
        metadata["tool_calls"] = tool_calls
    return metadata
