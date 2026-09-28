"""課程／教學整理後抽出的共用 helper：拓樸方向展開、建機就緒判斷、評分項目正規化、對外網址。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models import BatchProvisionJobStatus
from app.services.course import ai_assignment_service, weekly_task_service
from app.services.resource import resource_service
from app.services.teaching import (
    class_network_service,
    class_status_service,
    course_publication_service,
)

# ---------------------------------------------------------------------------
# topology_directions
# ---------------------------------------------------------------------------


def _edge(source: str, target: str, *, direction: str = "one_way", port: int | None = 22):
    return SimpleNamespace(
        source_node_key=source,
        target_node_key=target,
        protocol="tcp",
        port=port,
        direction=direction,
    )


def test_explicit_edges_add_reverse_only_when_bidirectional() -> None:
    directions = class_network_service.topology_directions(
        peer_policy="explicit",
        edges=[
            _edge("web", "db", port=5432),
            _edge("web", "cache", direction="bidirectional", port=6379),
        ],
        vmid_by_key={"web": 101, "db": 102, "cache": 103},
        network_by_key={"web": "a", "db": "a", "cache": "a"},
    )

    assert directions == [
        (101, 102, "tcp", 5432),
        (101, 103, "tcp", 6379),
        (103, 101, "tcp", 6379),
    ]


def test_explicit_edges_with_missing_endpoint_are_skipped() -> None:
    directions = class_network_service.topology_directions(
        peer_policy="explicit",
        edges=[_edge("web", "db", direction="bidirectional")],
        vmid_by_key={"web": 101},
        network_by_key={"web": "a", "db": "a"},
    )

    assert directions == []


def test_segment_policy_meshes_machines_that_share_a_segment() -> None:
    directions = class_network_service.topology_directions(
        peer_policy=class_network_service.PEER_POLICY_SEGMENT,
        edges=[_edge("web", "db")],  # segment 模式不看畫出來的連線
        vmid_by_key={"web": 101, "db": 102, "lone": 103, "orphan": 104},
        network_by_key={"web": "front,back", "db": "back", "lone": "other"},
    )

    assert sorted(directions) == [(101, 102, "any", None), (102, 101, "any", None)]


# ---------------------------------------------------------------------------
# jobs_all_ready
# ---------------------------------------------------------------------------


def _job(**overrides) -> SimpleNamespace:
    values = {
        "id": uuid.uuid4(),
        "status": BatchProvisionJobStatus.completed,
        "done": 2,
        "total": 2,
        "failed_count": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("node_count", "jobs", "expected"),
    [
        (0, [], False),
        (2, [_job()], False),  # 有節點還沒有工作（或工作已不存在）
        (2, [_job(), _job()], True),
        (1, [_job(failed_count=1)], False),
        (1, [_job(done=1)], False),
        (1, [_job(status=BatchProvisionJobStatus.running)], False),
    ],
)
def test_jobs_all_ready(node_count: int, jobs: list, expected: bool) -> None:
    nodes = [SimpleNamespace() for _ in range(node_count)]
    assert class_status_service.jobs_all_ready(nodes, jobs) is expected


# ---------------------------------------------------------------------------
# student_task_items
# ---------------------------------------------------------------------------


def test_student_task_items_normalization_is_shared_by_weekly_tasks() -> None:
    snapshot = {
        "items": [
            {"id": "perm", "title": " 權限 ", "detectable": " AUTO "},
            "not-a-dict",
            {"title": "", "detectable": "auto"},
            {"title": "日誌", "detectable": "unknown", "description": " 看 log "},
        ]
    }

    items = ai_assignment_service.student_task_items(snapshot)

    assert [(i.id, i.title, i.detectable, i.order) for i in items] == [
        ("perm", "權限", "auto", 0),
        ("item-4", "日誌", "manual", 3),
    ]
    assert items[1].description == "看 log"
    source_file = SimpleNamespace(analysis_json=snapshot)
    assert weekly_task_service._source_items(source_file) == items  # type: ignore[arg-type]
    assert weekly_task_service._source_items(None) == []


def test_student_task_items_ignores_missing_item_list() -> None:
    assert ai_assignment_service.student_task_items({}) == []
    assert ai_assignment_service.student_task_items({"items": "x"}) == []


# ---------------------------------------------------------------------------
# course_publication_service.public_urls_by_vmid
# ---------------------------------------------------------------------------


def test_public_urls_by_vmid_takes_first_sorted_url(monkeypatch) -> None:
    seen: list[list[int]] = []

    def fake_public_urls(_session, vmids):
        seen.append(list(vmids))
        return {101: ["https://a.example", "http://b.example"], 102: []}

    monkeypatch.setattr(resource_service, "public_urls_by_vmid", fake_public_urls)

    urls = course_publication_service.public_urls_by_vmid(object(), [101, 102, 103])  # type: ignore[arg-type]

    assert urls == {101: "https://a.example"}
    assert seen == [[101, 102, 103]]
