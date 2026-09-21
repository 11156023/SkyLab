"""Regression coverage for legacy artifact read and lifecycle compatibility."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.ai.teacher_judge import script_artifact_service
from app.models import TeacherJudgeScriptArtifact
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptSource,
    TeacherJudgeScriptStatus,
)
from tests.ai.teacher_judge.helpers import make_session


def _historical_artifact(
    *, teaching_class_id: uuid.UUID, status: TeacherJudgeScriptStatus
) -> TeacherJudgeScriptArtifact:
    return TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        name="舊版檢查腳本",
        template_key="linux",
        rubric_snapshot_json={"items": []},
        source_file_snapshot_json={},
        script_content="print('historical artifact')",
        source=TeacherJudgeScriptSource.ai_generated,
        status=status,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )


def test_historical_artifact_remains_readable_approvable_and_archivable() -> None:
    session = make_session()
    class_id = uuid.uuid4()
    artifact = _historical_artifact(
        teaching_class_id=class_id,
        status=TeacherJudgeScriptStatus.reviewed,
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    listed = script_artifact_service.list_artifacts(
        session=session,
        teaching_class_id=class_id,
    )
    assert [row.id for row in listed] == [str(artifact.id)]
    assert listed[0].script_content == "print('historical artifact')"
    assert listed[0].source == "ai_generated"

    approved = script_artifact_service.approve_artifact(
        session=session,
        teaching_class_id=class_id,
        artifact_id=artifact.id,
        approved_by=None,
    )
    assert approved.status == "approved"

    archived = script_artifact_service.archive_artifact(
        session=session,
        teaching_class_id=class_id,
        artifact_id=artifact.id,
    )
    assert archived.status == "archived"
    assert script_artifact_service.get_artifact_public(
        session=session,
        teaching_class_id=class_id,
        artifact_id=artifact.id,
    ).status == "archived"


def test_historical_artifact_can_be_renamed_and_deleted() -> None:
    session = make_session()
    class_id = uuid.uuid4()
    artifact = _historical_artifact(
        teaching_class_id=class_id,
        status=TeacherJudgeScriptStatus.archived,
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    renamed = script_artifact_service.rename_artifact(
        session=session,
        teaching_class_id=class_id,
        artifact_id=artifact.id,
        name="已封存腳本",
    )
    assert renamed.name == "已封存腳本"

    script_artifact_service.delete_artifact(
        session=session,
        teaching_class_id=class_id,
        artifact_id=artifact.id,
    )
    with pytest.raises(HTTPException) as exc_info:
        script_artifact_service.get_artifact(
            session=session,
            teaching_class_id=class_id,
            artifact_id=artifact.id,
        )
    assert exc_info.value.status_code == 404
