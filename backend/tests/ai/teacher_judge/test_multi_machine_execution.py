"""Focused contracts for multi-machine artifact, peer and result projection."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlmodel import select

from app.ai.teacher_judge import script_artifact_service
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricCheckStep,
    TeacherJudgeRubricItem,
)
from app.ai.teacher_judge.script_artifact_service import (
    partition_analysis_by_target_node,
)
from app.ai.teacher_judge.script_policy import check_peer_runtime_policy
from app.ai.teacher_judge.script_run_service import (
    _peer_resolution_for_target,
    project_run_items,
)
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptLanguage,
    TeacherJudgeScriptStatus,
)
from app.models.teaching_class import TeachingClassMachineNode
from tests.ai.teacher_judge.helpers import make_session


def _item(
    item_id: str,
    node_key: str,
    *,
    peer_node_key: str | None = None,
) -> TeacherJudgeRubricItem:
    return TeacherJudgeRubricItem(
        id=item_id,
        title=item_id,
        checked=False,
        detectable="auto",
        judgement_mode="ai",
        detection_method="受控檢查",
        target_node_key=node_key,
        peer_node_key=peer_node_key,
        check_steps=[
            TeacherJudgeRubricCheckStep(
                argv=(
                    ["ping", "-c", "1", "{{peer.ip}}"]
                    if peer_node_key
                    else ["systemctl", "is-active", "nginx"]
                ),
                timeout_seconds=30,
            )
        ],
    )


def test_partition_keeps_peer_item_with_executor_and_respects_node_order() -> None:
    analysis = TeacherJudgeRubricAnalysis(
        items=[
            _item("web-health", "web"),
            _item("db-to-web", "db", peer_node_key="web"),
            _item("db-health", "db"),
        ],
        pending_review_item_ids=["db-to-web"],
    )

    partitions = partition_analysis_by_target_node(
        analysis,
        node_order=["db", "web"],
    )

    assert [node_key for node_key, _ in partitions] == ["db", "web"]
    db_items = partitions[0][1].items
    web_items = partitions[1][1].items
    assert [item.id for item in db_items] == ["db-to-web", "db-health"]
    assert [item.id for item in web_items] == ["web-health"]
    assert db_items[0].peer_node_key == "web"
    assert partitions[0][1].pending_review_item_ids == ["db-to-web"]


@pytest.mark.asyncio
async def test_create_artifact_set_writes_one_child_per_executor_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    class_id = uuid.uuid4()
    session.add_all(
        [
            TeachingClassMachineNode(
                class_id=class_id,
                node_key="web",
                name="Web",
                role="frontend",
                resource_type="lxc",
                cpu=1,
                memory_mb=512,
                disk_gb=8,
                sort_order=0,
            ),
            TeachingClassMachineNode(
                class_id=class_id,
                node_key="db",
                name="Database",
                role="database",
                resource_type="lxc",
                cpu=1,
                memory_mb=512,
                disk_gb=8,
                sort_order=1,
            ),
        ]
    )
    session.commit()

    monkeypatch.setattr(
        script_artifact_service,
        "get_enabled_template_commands",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        script_artifact_service,
        "ensure_script_generation_supported",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        script_artifact_service,
        "source_file_snapshot",
        lambda **_kwargs: (None, {}),
    )

    async def fake_build(
        *, rubric_snapshot: dict[str, Any], template_key: str
    ) -> tuple[Any, ...]:
        assert template_key == "linux"
        assert rubric_snapshot["target_node_key"] in {"web", "db"}
        return (
            "print('ok')",
            {"approved": True, "issues": []},
            {"approved": True, "issues": []},
            TeacherJudgeScriptStatus.reviewed,
            [],
        )

    monkeypatch.setattr(
        script_artifact_service,
        "_build_reviewed_script_for_artifact",
        fake_build,
    )

    result = await script_artifact_service.create_artifact_set(
        session=session,
        teaching_class_id=class_id,
        session_id=uuid.uuid4(),
        name="network rubric",
        template_key="linux",
        rubric_analysis=TeacherJudgeRubricAnalysis(
            items=[
                _item("web-health", "web"),
                _item("db-to-web", "db", peer_node_key="web"),
            ]
        ),
        source_analysis_revision=3,
        created_by=uuid.uuid4(),
        source_file_id=None,
    )

    assert result.status == "approved"
    assert [child.target_node_key for child in result.children] == ["web", "db"]
    assert len({child.artifact_set_id for child in result.children}) == 1
    assert all(child.source_analysis_revision == 3 for child in result.children)
    assert [
        item["id"]
        for item in result.children[1].rubric_snapshot_json["items"]
    ] == ["db-to-web"]
    rows = list(session.exec(select(TeacherJudgeScriptArtifact)).all())
    assert len(rows) == 2


def _peer_script(command: str = "ping") -> str:
    return f'''
import json
import subprocess
from pathlib import Path

context = json.loads(Path("runtime_context.json").read_text())
peer = context["peers"]["web"]
resolution_status = peer["resolution_status"]
ip_address = peer["ip_address"]
if resolution_status != "ready" or not ip_address:
    print("peer_unavailable")
else:
    argv = ["{command}", "-c", "1", ip_address]
    subprocess.run(argv, capture_output=True, text=True, timeout=30)
'''.strip()


def _peer_snapshot() -> dict[str, object]:
    return {
        "items": [
            _item("db-to-web", "db", peer_node_key="web").model_dump(mode="json")
        ]
    }


def test_peer_policy_requires_declared_peer_context_to_reach_ping_only() -> None:
    approved = check_peer_runtime_policy(_peer_script(), _peer_snapshot())
    assert approved["approved"] is True

    rejected = check_peer_runtime_policy(_peer_script("curl"), _peer_snapshot())
    assert rejected["approved"] is False
    assert any("ping" in issue for issue in rejected["issues"])

    hardcoded_destination = _peer_script().replace(
        '["ping", "-c", "1", ip_address]',
        '["ping", "-c", "1", ip_address, "8.8.8.8"]',
    )
    rejected_hardcoded = check_peer_runtime_policy(
        hardcoded_destination,
        _peer_snapshot(),
    )
    assert rejected_hardcoded["approved"] is False
    assert any("IP" in issue or "CIDR" in issue for issue in rejected_hardcoded["issues"])


def test_item_projection_redacts_peer_ip_and_marks_unavailable_as_unknown() -> None:
    artifact = TeacherJudgeScriptArtifact(
        teaching_class_id=uuid.uuid4(),
        target_node_key="db",
        rubric_snapshot_json=_peer_snapshot(),
        policy_check_result_json={
            "coverage": {
                "mappings": [
                    {"check_id": "db-to-web", "rubric_item_ids": ["db-to-web"]}
                ]
            }
        },
        script_language=TeacherJudgeScriptLanguage.python,
        script_content="print('{}')",
        status=TeacherJudgeScriptStatus.approved,
        name="db",
        template_key="linux",
    )
    target_result = {
        "student_id": "student-1",
        "vmid": 101,
        "status": "completed",
        "parsed_result": {
            "checks": [
                {
                    "id": "db-to-web",
                    "status": "fail",
                    "evidence": "192.0.2.10 unreachable",
                }
            ]
        },
    }

    projected = project_run_items(
        artifact=artifact,
        target_result=target_result,
        display_labels={"db": "P2", "web": "P1"},
        peer_ips={"192.0.2.10"},
        peer_resolution={
            "web": {
                "resolution_status": "unavailable",
                "reason_code": "peer_ip_unavailable",
            }
        },
    )

    item = projected["items"][0]
    assert item["status"] == "unknown"
    assert item["evidence_state"] == "unavailable"
    assert item["peer_display_label"] == "P1"
    assert item["reason_code"] == "peer_unavailable"
    assert "192.0.2.10" not in json.dumps(projected, ensure_ascii=False)
    assert projected["display_label"] == "P2"


def test_peer_resolution_matches_vmid_across_json_number_types() -> None:
    snapshot = {
        "targets": [
            {
                "student_id": "student-1",
                "vmid": 101,
                "runtime_context": {
                    "peers": {
                        "web": {
                            "resolution_status": "ready",
                            "ip_address": "192.0.2.10",
                        }
                    }
                },
            }
        ]
    }

    result = _peer_resolution_for_target(
        snapshot,
        {"student_id": "student-1", "vmid": "101"},
    )

    assert result["web"]["resolution_status"] == "ready"


def test_item_projection_keeps_vmid_and_teacher_review() -> None:
    artifact = TeacherJudgeScriptArtifact(
        teaching_class_id=uuid.uuid4(),
        target_node_key="db",
        rubric_snapshot_json=_peer_snapshot(),
        policy_check_result_json={
            "coverage": {
                "mappings": [
                    {"check_id": "db-to-web", "rubric_item_ids": ["db-to-web"]}
                ]
            }
        },
        script_language=TeacherJudgeScriptLanguage.python,
        script_content="print('{}')",
        status=TeacherJudgeScriptStatus.approved,
        name="db",
        template_key="linux",
    )
    target_result = {
        "student_id": "student-1",
        "vmid": 101,
        "status": "completed",
        "teacher_review": {
            "feedback": "請確認 192.0.2.10 連通後重跑",
            "decisions": {"db-to-web": "pass"},
            "reviewed_by": "teacher-1",
            "updated_at": "2026-09-20T00:00:00+00:00",
        },
        "parsed_result": {
            "checks": [
                {
                    "id": "db-to-web",
                    "status": "warning",
                    "evidence": "192.0.2.10 latency high",
                }
            ]
        },
    }

    projected = project_run_items(
        artifact=artifact,
        target_result=target_result,
        display_labels={"db": "P2", "web": "P1"},
        peer_ips={"192.0.2.10"},
        peer_resolution={
            "web": {
                "resolution_status": "ready",
                "reason_code": None,
            }
        },
    )

    assert projected["vmid"] == 101
    review = projected["teacher_review"]
    assert isinstance(review, dict)
    assert review["decisions"] == {"db-to-web": "pass"}
    assert "192.0.2.10" not in json.dumps(projected, ensure_ascii=False)

    absent = project_run_items(
        artifact=artifact,
        target_result={"student_id": "student-1", "status": "completed"},
        display_labels={"db": "P2", "web": "P1"},
    )
    assert absent["vmid"] is None
    assert absent["teacher_review"] is None
