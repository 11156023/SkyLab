"""Teacher Judge managed script artifact lifecycle service."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import HTTPException
from sqlmodel import Session, col, desc, select

from app.ai.teacher_judge.automation_support import ensure_script_generation_supported
from app.ai.teacher_judge.deterministic_compiler import (
    CheckPlanContractError,
    canonicalize_check_plan,
    compile_check_plan,
)
from app.ai.teacher_judge.file_service import source_file_snapshot
from app.ai.teacher_judge.machine_context import (
    load_class_machine_nodes,
    peer_node_keys_from_snapshot,
    rubric_item_machine_issues,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeScriptArtifactPublic,
    TeacherJudgeScriptSetPublic,
)
from app.ai.teacher_judge.script_policy import check_peer_runtime_policy
from app.ai.teacher_judge.template_command_service import get_enabled_template_commands
from app.core.i18n import t
from app.models.base import get_datetime_utc as _now
from app.models.teacher_judge_file import TeacherJudgeFile
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptLanguage,
    TeacherJudgeScriptSource,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_session import (
    TeacherJudgeSession,
    TeacherJudgeSessionStatus,
)
from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand


def lock_script_workspace(
    session: Session, session_id: uuid.UUID, teaching_class_id: uuid.UUID
) -> TeacherJudgeSession:
    item = session.exec(
        select(TeacherJudgeSession)
        .where(
            TeacherJudgeSession.id == session_id,
            TeacherJudgeSession.teaching_class_id == teaching_class_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Teacher Judge workspace not found")
    return item


def active_set_children(
    session: Session, workspace: TeacherJudgeSession
) -> list[TeacherJudgeScriptArtifact]:
    """Resolve the explicit active set, including legacy per-node versions.

    Never fall back to another approved set or resurrect an archived child.
    """
    if workspace.active_artifact_set_id is None:
        return []
    rows = session.exec(
        select(TeacherJudgeScriptArtifact).where(
            TeacherJudgeScriptArtifact.session_id == workspace.id,
            TeacherJudgeScriptArtifact.teaching_class_id == workspace.teaching_class_id,
            TeacherJudgeScriptArtifact.artifact_set_id
            == workspace.active_artifact_set_id,
        )
    ).all()
    latest: dict[str, TeacherJudgeScriptArtifact] = {}
    for row in rows:
        key = row.target_node_key or ""
        previous = latest.get(key)
        if previous is None or (row.version, row.created_at) > (
            previous.version,
            previous.created_at,
        ):
            latest[key] = row
    children = list(latest.values())
    if not children or any(
        not child.target_node_key or child.status != TeacherJudgeScriptStatus.approved
        for child in children
    ):
        return []
    if (
        len(
            {
                (child.source_file_id, child.source_analysis_revision)
                for child in children
            }
        )
        != 1
    ):
        return []
    return children


def require_active_artifact(
    session: Session, artifact: TeacherJudgeScriptArtifact, *, lock: bool = True
) -> TeacherJudgeSession:
    from app.ai.teacher_judge.session_service import (
        ensure_active,
        ensure_week_published,
    )

    if artifact.session_id is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "teacher_judge_script_set_not_active",
                "message": "腳本尚未指定為每週生效組，請重新製作。",
            },
        )
    workspace = (
        lock_script_workspace(session, artifact.session_id, artifact.teaching_class_id)
        if lock
        else session.get(TeacherJudgeSession, artifact.session_id)
    )
    if workspace is None or workspace.teaching_class_id != artifact.teaching_class_id:
        raise HTTPException(status_code=404, detail="Teacher Judge workspace not found")
    ensure_active(workspace)
    ensure_week_published(session, workspace)
    if artifact.id not in {
        child.id for child in active_set_children(session, workspace)
    }:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "teacher_judge_script_set_not_active",
                "message": "生效腳本組已變更，請重新載入後執行。",
            },
        )
    return workspace


def _machine_display_names(nodes: list[Any]) -> dict[str, str]:
    return {node.node_key: (node.name or "").strip() or node.node_key for node in nodes}


def _set_publication_fields(
    session: Session,
    workspace: TeacherJudgeSession | None,
    sets: list[TeacherJudgeScriptSetPublic],
) -> None:
    if workspace is None:
        return
    active_ids = {str(child.id) for child in active_set_children(session, workspace)}
    source = (
        session.get(TeacherJudgeFile, workspace.selected_file_id)
        if workspace.selected_file_id
        else None
    )
    for item in sets:
        item.is_active = bool(
            active_ids
            and str(workspace.active_artifact_set_id) == item.artifact_set_id
            and active_ids == {child.id for child in item.children}
        )
        item.source_is_current = bool(
            source
            and item.source_file_id == str(source.id)
            and item.source_analysis_revision == source.analysis_revision
        )


def _class_machine_display_names(
    session: Session, teaching_class_id: uuid.UUID
) -> dict[str, str]:
    return _machine_display_names(load_class_machine_nodes(session, teaching_class_id))


def _artifact_public_name(
    artifact: TeacherJudgeScriptArtifact,
    node_display_names: dict[str, str] | None,
) -> str:
    """Map a legacy `· {node_key}` name suffix to the teacher's machine name."""

    if not node_display_names:
        return artifact.name
    node_key = str(artifact.target_node_key or "")
    if not node_key:
        return artifact.name
    suffix = f" · {node_key}"
    if not artifact.name.endswith(suffix):
        return artifact.name
    machine_name = node_display_names.get(node_key)
    if not machine_name or machine_name == node_key:
        return artifact.name
    return f"{artifact.name[: -len(suffix)]} · {machine_name}"[:255]


def _artifact_to_public(
    artifact: TeacherJudgeScriptArtifact,
    node_display_names: dict[str, str] | None = None,
) -> TeacherJudgeScriptArtifactPublic:
    return TeacherJudgeScriptArtifactPublic(
        id=str(artifact.id),
        artifact_set_id=(
            str(artifact.artifact_set_id) if artifact.artifact_set_id else None
        ),
        target_node_key=artifact.target_node_key,
        source_analysis_revision=artifact.source_analysis_revision,
        teaching_class_id=str(artifact.teaching_class_id),
        session_id=str(artifact.session_id) if artifact.session_id else None,
        name=_artifact_public_name(artifact, node_display_names),
        template_key=artifact.template_key,
        rubric_snapshot_json=artifact.rubric_snapshot_json,
        source_file_id=str(artifact.source_file_id)
        if artifact.source_file_id
        else None,
        source_file_snapshot_json=artifact.source_file_snapshot_json,
        script_language=artifact.script_language.value,
        script_content=artifact.script_content,
        source=artifact.source.value,
        version=artifact.version,
        status=artifact.status.value,
        policy_check_result_json=artifact.policy_check_result_json,
        ai_review_result_json=artifact.ai_review_result_json,
        created_by=str(artifact.created_by) if artifact.created_by else None,
        approved_by=str(artifact.approved_by) if artifact.approved_by else None,
        created_at=artifact.created_at.isoformat(),
        updated_at=artifact.updated_at.isoformat(),
        approved_at=artifact.approved_at.isoformat() if artifact.approved_at else None,
    )


def _template_commands_snapshot(
    commands: list[TeacherJudgeTemplateCommand] | None,
) -> list[dict[str, Any]]:
    if not commands:
        return []
    return [
        {
            "command_key": command.command_key,
            "command_label": command.command_label,
            "category": command.category,
            "command_template": command.command_template,
            "description": command.description,
            "risk_level": command.risk_level,
            "requires_confirmation": command.requires_confirmation,
        }
        for command in commands
    ]


def _snapshot_uses_legacy_command_references(
    rubric_snapshot: dict[str, Any],
) -> bool:
    """Preserve command catalog snapshots only for historical flat steps."""

    raw_items = rubric_snapshot.get("items")
    if not isinstance(raw_items, list):
        return False
    return any(
        isinstance(step, dict) and (step.get("template_key") or step.get("command_key"))
        for item in raw_items
        if isinstance(item, dict)
        for step in (item.get("check_steps") or [])
    )


def _with_template_command_catalog(
    rubric_snapshot: dict[str, Any],
    template_commands: list[TeacherJudgeTemplateCommand] | None,
) -> dict[str, Any]:
    snapshot = dict(rubric_snapshot)
    command_catalog = _template_commands_snapshot(template_commands)
    if command_catalog and _snapshot_uses_legacy_command_references(snapshot):
        snapshot["template_commands"] = command_catalog
    return snapshot


def list_artifacts(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID | None = None,
) -> list[TeacherJudgeScriptArtifactPublic]:
    query = select(TeacherJudgeScriptArtifact).where(
        TeacherJudgeScriptArtifact.teaching_class_id == teaching_class_id
    )
    if session_id is not None:
        query = query.where(TeacherJudgeScriptArtifact.session_id == session_id)
    artifacts = session.exec(
        query.order_by(desc(TeacherJudgeScriptArtifact.created_at))
    ).all()
    node_display_names = _class_machine_display_names(session, teaching_class_id)
    return [_artifact_to_public(artifact, node_display_names) for artifact in artifacts]


def get_artifact(
    *, session: Session, teaching_class_id: uuid.UUID, artifact_id: uuid.UUID
) -> TeacherJudgeScriptArtifact:
    artifact = session.get(TeacherJudgeScriptArtifact, artifact_id)
    if artifact is None or artifact.teaching_class_id != teaching_class_id:
        raise HTTPException(status_code=404, detail="Script artifact not found")
    return artifact


def get_artifact_public(
    *, session: Session, teaching_class_id: uuid.UUID, artifact_id: uuid.UUID
) -> TeacherJudgeScriptArtifactPublic:
    return _artifact_to_public(
        get_artifact(
            session=session,
            teaching_class_id=teaching_class_id,
            artifact_id=artifact_id,
        ),
        _class_machine_display_names(session, teaching_class_id),
    )


def partition_analysis_by_target_node(
    analysis: TeacherJudgeRubricAnalysis,
    *,
    node_order: list[str] | None = None,
) -> list[tuple[str, TeacherJudgeRubricAnalysis]]:
    """Partition executable rubric items by their canonical executor node."""

    grouped: dict[str, list[Any]] = {}
    for item in analysis.items:
        node_key = str(item.target_node_key or "").strip()
        if not node_key:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "teacher_judge_target_node_required",
                    "message": "每個可執行檢查項目都必須指定 target_node_key。",
                    "item_ids": [item.id],
                },
            )
        grouped.setdefault(node_key, []).append(item)
    ordered_keys = [key for key in node_order or [] if key in grouped]
    ordered_keys.extend(sorted(set(grouped) - set(ordered_keys)))
    partitions: list[tuple[str, TeacherJudgeRubricAnalysis]] = []
    for node_key in ordered_keys:
        items = grouped[node_key]
        partitions.append(
            (
                node_key,
                analysis.model_copy(
                    deep=True,
                    update={
                        "items": items,
                        "total_items": len(items),
                        "checked_count": sum(1 for item in items if item.checked),
                        "auto_count": sum(
                            1 for item in items if item.detectable == "auto"
                        ),
                        "partial_count": sum(
                            1 for item in items if item.detectable == "partial"
                        ),
                        "manual_count": sum(
                            1 for item in items if item.detectable == "manual"
                        ),
                        "pending_review_item_ids": [
                            item_id
                            for item_id in analysis.pending_review_item_ids
                            if item_id in {item.id for item in items}
                        ],
                    },
                ),
            )
        )
    return partitions


def latest_set_children(
    rows: list[TeacherJudgeScriptArtifact],
    *,
    node_order: dict[str, int] | None = None,
) -> list[TeacherJudgeScriptArtifact]:
    """Return one current child per node, excluding archived history.

    Shared by the script-set views and the batch run so the set a teacher sees
    is the set that gets executed.
    """

    latest: dict[str, TeacherJudgeScriptArtifact] = {}
    for row in rows:
        if row.status == TeacherJudgeScriptStatus.archived:
            continue
        node_key = str(row.target_node_key or "")
        current = latest.get(node_key)
        if current is None or (row.version, row.created_at) > (
            current.version,
            current.created_at,
        ):
            latest[node_key] = row
    order = node_order or {}
    return sorted(
        latest.values(),
        key=lambda row: (
            order.get(str(row.target_node_key or ""), 10**9),
            str(row.target_node_key or ""),
        ),
    )


def _script_set_to_public(
    rows: list[TeacherJudgeScriptArtifact],
    *,
    node_order: dict[str, int] | None = None,
    node_display_names: dict[str, str] | None = None,
) -> TeacherJudgeScriptSetPublic:
    children = latest_set_children(rows, node_order=node_order)
    if not children or children[0].artifact_set_id is None:
        raise HTTPException(status_code=404, detail="Script set not found")
    statuses = {child.status.value for child in children}
    status: Literal["approved", "review_failed", "mixed"] = (
        "approved"
        if statuses == {TeacherJudgeScriptStatus.approved.value}
        else "review_failed"
        if statuses == {TeacherJudgeScriptStatus.review_failed.value}
        else "mixed"
    )
    first = children[0]
    return TeacherJudgeScriptSetPublic(
        artifact_set_id=str(first.artifact_set_id),
        teaching_class_id=str(first.teaching_class_id),
        session_id=str(first.session_id) if first.session_id else None,
        source_file_id=str(first.source_file_id) if first.source_file_id else None,
        source_analysis_revision=first.source_analysis_revision,
        status=status,
        children=[_artifact_to_public(child, node_display_names) for child in children],
    )


def get_artifact_set(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_set_id: uuid.UUID,
    session_id: uuid.UUID | None = None,
) -> TeacherJudgeScriptSetPublic:
    statement = select(TeacherJudgeScriptArtifact).where(
        TeacherJudgeScriptArtifact.teaching_class_id == teaching_class_id,
        TeacherJudgeScriptArtifact.artifact_set_id == artifact_set_id,
    )
    if session_id is not None:
        statement = statement.where(TeacherJudgeScriptArtifact.session_id == session_id)
    rows = list(session.exec(statement).all())
    if not rows:
        raise HTTPException(status_code=404, detail="Script set not found")
    nodes = load_class_machine_nodes(session, teaching_class_id)
    result = _script_set_to_public(
        rows,
        node_order={node.node_key: node.sort_order for node in nodes},
        node_display_names=_machine_display_names(nodes),
    )
    workspace = (
        session.get(TeacherJudgeSession, rows[0].session_id)
        if rows[0].session_id
        else None
    )
    _set_publication_fields(session, workspace, [result])
    return result


def list_artifact_sets(
    *, session: Session, teaching_class_id: uuid.UUID, session_id: uuid.UUID
) -> list[TeacherJudgeScriptSetPublic]:
    rows = list(
        session.exec(
            select(TeacherJudgeScriptArtifact)
            .where(
                TeacherJudgeScriptArtifact.teaching_class_id == teaching_class_id,
                TeacherJudgeScriptArtifact.session_id == session_id,
                col(TeacherJudgeScriptArtifact.artifact_set_id).is_not(None),
            )
            .order_by(desc(TeacherJudgeScriptArtifact.created_at))
        ).all()
    )
    nodes = load_class_machine_nodes(session, teaching_class_id)
    node_order = {node.node_key: node.sort_order for node in nodes}
    node_display_names = _machine_display_names(nodes)
    workspace = session.get(TeacherJudgeSession, session_id)
    grouped: dict[uuid.UUID, list[TeacherJudgeScriptArtifact]] = {}
    order: list[uuid.UUID] = []
    for row in rows:
        if row.artifact_set_id is None:
            continue
        if row.artifact_set_id not in grouped:
            grouped[row.artifact_set_id] = []
            order.append(row.artifact_set_id)
        grouped[row.artifact_set_id].append(row)
    result = [
        _script_set_to_public(
            grouped[set_id],
            node_order=node_order,
            node_display_names=node_display_names,
        )
        for set_id in order
        if any(
            row.status != TeacherJudgeScriptStatus.archived for row in grouped[set_id]
        )
    ]
    _set_publication_fields(session, workspace, result)
    return result


def create_artifact_set(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    name: str,
    template_key: str,
    rubric_analysis: TeacherJudgeRubricAnalysis,
    source_analysis_revision: int,
    created_by: uuid.UUID | None,
    source_file_id: uuid.UUID | None,
    artifact_set_id: uuid.UUID | None = None,
) -> TeacherJudgeScriptSetPublic:
    artifact_name = name.strip()
    if not artifact_name:
        raise HTTPException(status_code=400, detail=t("artifact.name_blank"))
    workspace = session.get(TeacherJudgeSession, session_id)
    if workspace is None or workspace.teaching_class_id != teaching_class_id:
        raise HTTPException(status_code=404, detail="Teacher Judge workspace not found")
    expected_active_set_id = workspace.active_artifact_set_id
    commands = get_enabled_template_commands(
        session, template_key, include_cross_template=True
    )
    nodes = load_class_machine_nodes(session, teaching_class_id)
    ensure_script_generation_supported(
        rubric_analysis,
        commands,
        require_target_node=bool(nodes),
    )
    valid_node_keys = {node.node_key for node in nodes}
    node_display_names = _machine_display_names(nodes)
    partitions = partition_analysis_by_target_node(
        rubric_analysis, node_order=[node.node_key for node in nodes]
    )
    invalid_node_keys = {key for key, _ in partitions if key not in valid_node_keys}
    invalid_node_keys.update(
        peer_node_key
        for peer_node_key in peer_node_keys_from_snapshot(
            rubric_analysis.model_dump(mode="json")
        )
        if peer_node_key not in valid_node_keys
    )
    if invalid_node_keys:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_target_node_not_in_class",
                "message": "檢查項目的 target_node_key 不屬於目前班級。",
                "target_node_keys": sorted(invalid_node_keys),
            },
        )
    machine_contract_issues = {
        item.id: issues
        for item in rubric_analysis.items
        if (issues := rubric_item_machine_issues(item.model_dump(mode="json")))
    }
    if machine_contract_issues:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_machine_contract_invalid",
                "message": "檢查項目的執行節點、觀察節點或 peer token 不一致。",
                "items": machine_contract_issues,
            },
        )
    try:
        canonicalize_check_plan(rubric_analysis)
    except CheckPlanContractError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_check_plan_invalid",
                "message": "完整 Check Plan 未通過契約驗證。",
                "issues": exc.issues,
            },
        ) from exc

    # Every failure path below raises, so each built child is approved.
    build_results: list[
        tuple[str, dict[str, Any], str, dict[str, Any], dict[str, Any]]
    ] = []
    for node_key, partition in partitions:
        partition_dump = partition.model_dump(mode="json")
        rubric_base: dict[str, Any] = {
            **partition_dump,
            "target_node_key": node_key,
        }
        if _snapshot_uses_legacy_command_references(partition_dump):
            rubric_base["template_key"] = template_key
        rubric_snapshot = _with_template_command_catalog(rubric_base, commands)
        try:
            script_content, policy, review, _compiled_plan = compile_check_plan(
                partition, target_node_key=node_key
            )
            if policy.get("approved") is not True or review.get("approved") is not True:
                raise CheckPlanContractError(
                    [{"message": "compiled script validation failed"}]
                )
            peer_policy = check_peer_runtime_policy(script_content, rubric_snapshot)
            if peer_policy.get("approved") is not True:
                raise CheckPlanContractError(
                    [
                        {
                            "message": "peer runtime contract validation failed",
                            "issues": peer_policy.get("issues", []),
                        }
                    ]
                )
            policy = {
                **policy,
                "safety_approved": True,
                "safety_issues": [],
                "quality_approved": True,
                "quality_issues": [],
                "peer_runtime_policy": peer_policy,
                "approved": True,
            }
        except CheckPlanContractError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "teacher_judge_check_plan_invalid",
                    "message": f"節點 {node_key} 的 Check Plan 未通過契約驗證。",
                    "target_node_key": node_key,
                    "issues": exc.issues,
                },
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "teacher_judge_node_compile_failed",
                    "message": f"節點 {node_key} 的 deterministic 腳本編譯失敗。",
                    "target_node_key": node_key,
                    "item_ids": [item.id for item in partition.items],
                },
            ) from exc
        build_results.append(
            (node_key, rubric_snapshot, script_content, policy, review)
        )

    if not build_results:
        raise HTTPException(status_code=422, detail="沒有可製作的檢查項目。")
    set_id = uuid.uuid4()
    version_by_node: dict[str, int] = {}
    if artifact_set_id is not None:
        previous_rows = list(
            session.exec(
                select(TeacherJudgeScriptArtifact).where(
                    TeacherJudgeScriptArtifact.teaching_class_id == teaching_class_id,
                    TeacherJudgeScriptArtifact.artifact_set_id == artifact_set_id,
                )
            ).all()
        )
        if not previous_rows:
            raise HTTPException(status_code=404, detail="Script set not found")
        if any(
            previous.session_id != session_id
            or previous.source_file_id != source_file_id
            for previous in previous_rows
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "teacher_judge_script_set_context_mismatch",
                    "message": "script set 與目前 session 或檢查表來源不一致。",
                },
            )
        for previous in previous_rows:
            node_key = str(previous.target_node_key or "")
            version_by_node[node_key] = max(
                version_by_node.get(node_key, 0), previous.version
            )
    # Compile before taking locks. Re-read both authorities at publication so
    # concurrent edits/generations cannot publish a stale snapshot.
    workspace = lock_script_workspace(session, session_id, teaching_class_id)
    if (
        workspace.status != TeacherJudgeSessionStatus.active
        or workspace.active_artifact_set_id != expected_active_set_id
        or workspace.selected_file_id != source_file_id
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "teacher_judge_script_generation_conflict",
                "message": "工作區或生效組已變更，請重新載入後製作。",
            },
        )
    if source_file_id is not None:
        file = session.exec(
            select(TeacherJudgeFile)
            .where(
                TeacherJudgeFile.id == source_file_id,
                TeacherJudgeFile.teaching_class_id == teaching_class_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if file is None or file.analysis_revision != source_analysis_revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "teacher_judge_analysis_revision_conflict",
                    "message": "檢查表已更新，請重新載入後製作。",
                },
            )
    _, source_snapshot = source_file_snapshot(
        session=session,
        teaching_class_id=teaching_class_id,
        file_id=source_file_id,
    )
    artifacts: list[TeacherJudgeScriptArtifact] = []
    for node_key, snapshot, content, policy, review in build_results:
        artifact = TeacherJudgeScriptArtifact(
            artifact_set_id=set_id,
            target_node_key=node_key,
            source_analysis_revision=source_analysis_revision,
            teaching_class_id=teaching_class_id,
            session_id=session_id,
            name=f"{artifact_name} · {node_display_names.get(node_key, node_key)}"[
                :255
            ],
            template_key=template_key,
            rubric_snapshot_json=snapshot,
            source_file_id=source_file_id,
            source_file_snapshot_json=source_snapshot,
            script_language=TeacherJudgeScriptLanguage.python,
            script_content=content,
            source=(
                TeacherJudgeScriptSource.regenerated
                if artifact_set_id is not None
                else TeacherJudgeScriptSource.ai_generated
            ),
            version=version_by_node.get(node_key, 0) + 1,
            status=TeacherJudgeScriptStatus.approved,
            policy_check_result_json=policy,
            ai_review_result_json=review,
            created_by=created_by,
            approved_at=_now(),
            updated_at=_now(),
        )
        session.add(artifact)
        artifacts.append(artifact)
    workspace.active_artifact_set_id = set_id
    workspace.updated_at = workspace.last_activity_at = _now()
    session.add(workspace)
    try:
        session.flush()
        result = _script_set_to_public(
            artifacts,
            node_order={node.node_key: node.sort_order for node in nodes},
            node_display_names=node_display_names,
        )
        result.is_active = True
        result.source_is_current = True
        session.commit()
    except Exception:
        session.rollback()
        raise
    return result


def approve_artifact(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_id: uuid.UUID,
    approved_by: uuid.UUID | None,
) -> TeacherJudgeScriptArtifactPublic:
    artifact = get_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact_id,
    )
    if artifact.status != TeacherJudgeScriptStatus.reviewed:
        raise HTTPException(status_code=400, detail=t("artifact.not_reviewed"))
    if artifact.policy_check_result_json.get("approved") is not True:
        raise HTTPException(status_code=400, detail=t("artifact.policy_check_failed"))
    if artifact.ai_review_result_json.get("approved") is not True:
        raise HTTPException(status_code=400, detail=t("artifact.ai_review_failed"))
    artifact.status = TeacherJudgeScriptStatus.approved
    artifact.approved_by = approved_by
    artifact.approved_at = _now()
    artifact.updated_at = _now()
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return _artifact_to_public(
        artifact, _class_machine_display_names(session, teaching_class_id)
    )


def archive_artifact(
    *, session: Session, teaching_class_id: uuid.UUID, artifact_id: uuid.UUID
) -> TeacherJudgeScriptArtifactPublic:
    artifact = get_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact_id,
    )
    _ensure_artifact_can_retire(session, artifact)
    artifact.status = TeacherJudgeScriptStatus.archived
    artifact.updated_at = _now()
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return _artifact_to_public(
        artifact, _class_machine_display_names(session, teaching_class_id)
    )


def rename_artifact(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_id: uuid.UUID,
    name: str,
) -> TeacherJudgeScriptArtifactPublic:
    artifact = get_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact_id,
    )
    new_name = name.strip()
    if not new_name or len(new_name) > 255:
        raise HTTPException(status_code=400, detail=t("artifact.name_blank"))
    artifact.name = new_name
    artifact.updated_at = _now()
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return _artifact_to_public(
        artifact, _class_machine_display_names(session, teaching_class_id)
    )


def delete_artifact(
    *, session: Session, teaching_class_id: uuid.UUID, artifact_id: uuid.UUID
) -> None:
    artifact = get_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact_id,
    )
    _ensure_artifact_can_retire(session, artifact)
    session.delete(artifact)
    session.commit()


def _ensure_artifact_can_retire(
    session: Session, artifact: TeacherJudgeScriptArtifact
) -> None:
    from app.models.teacher_judge_run_batch import TeacherJudgeRunBatch
    from app.models.teacher_judge_script_run import TeacherJudgeScriptRun

    workspace = (
        lock_script_workspace(session, artifact.session_id, artifact.teaching_class_id)
        if artifact.session_id
        else None
    )
    if (
        workspace
        and artifact.artifact_set_id is not None
        and workspace.active_artifact_set_id == artifact.artifact_set_id
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "teacher_judge_active_script_protected",
                "message": "生效組的腳本無法個別封存或刪除，請先成功製作新組。",
            },
        )
    referenced = session.exec(
        select(TeacherJudgeScriptRun.id)
        .where(TeacherJudgeScriptRun.artifact_id == artifact.id)
        .limit(1)
    ).first()
    accepted = (
        session.exec(
            select(TeacherJudgeRunBatch.id)
            .where(
                TeacherJudgeRunBatch.artifact_set_id == artifact.artifact_set_id,
                TeacherJudgeRunBatch.session_id == artifact.session_id,
                TeacherJudgeRunBatch.teaching_class_id == artifact.teaching_class_id,
            )
            .limit(1)
        ).first()
        if artifact.artifact_set_id
        else None
    )
    if referenced or accepted:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "teacher_judge_script_in_use",
                "message": "腳本已有執行紀錄或已受理批次，無法封存或刪除。",
            },
        )
