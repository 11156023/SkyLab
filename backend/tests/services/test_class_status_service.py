"""班級狀態改由建機 worker 推進，不再只在老師開著班級頁時才前進。"""

import uuid
from types import SimpleNamespace

import pytest

from app.models import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    ClassCapacityReservation,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
)
from app.services.teaching import class_status_service


class _FakeExec:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, *, teaching_class, nodes, jobs, reservation=None):
        self.teaching_class = teaching_class
        self.nodes = nodes
        self.jobs = {job.id: job for job in jobs}
        self.reservation = reservation
        self.added = []

    def get(self, model, key):
        if model is TeachingClass:
            return self.teaching_class if key == self.teaching_class.id else None
        if model is BatchProvisionJob:
            return self.jobs.get(key)
        raise AssertionError(f"unexpected get({model})")

    def exec(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is TeachingClassMachineNode:
            return _FakeExec(self.nodes)
        if entity is ClassCapacityReservation:
            return _FakeExec([self.reservation] if self.reservation else [])
        raise AssertionError(f"unexpected exec({entity})")

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        return None


def _job(status, *, done=2, total=2, failed=0):
    return SimpleNamespace(
        id=uuid.uuid4(), status=status, done=done, total=total, failed_count=failed
    )


def _session(jobs, *, status=TeachingClassStatus.provisioning, reservation=None):
    item = SimpleNamespace(
        id=uuid.uuid4(), status=status, updated_at=None, name="Linux 實務"
    )
    nodes = [SimpleNamespace(batch_job_id=job.id) for job in jobs]
    return _FakeSession(
        teaching_class=item, nodes=nodes, jobs=jobs, reservation=reservation
    ), item


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    monkeypatch.setattr(
        class_status_service.class_network_service,
        "apply_class_topology",
        lambda _session, class_id: [],
    )
    monkeypatch.setattr(
        class_status_service.course_service,
        "ensure_class_path",
        lambda _session, **_kwargs: None,
    )


def test_all_jobs_completed_makes_the_class_teachable():
    reservation = SimpleNamespace(status="reserved")
    session, item = _session(
        [_job(BatchProvisionJobStatus.completed), _job(BatchProvisionJobStatus.completed)],
        reservation=reservation,
    )

    class_status_service.recompute(session=session, class_id=item.id)

    assert item.status == TeachingClassStatus.active
    assert reservation.status == "consumed"


def test_topology_failure_after_provisioning_needs_attention(monkeypatch):
    monkeypatch.setattr(
        class_status_service.class_network_service,
        "apply_class_topology",
        lambda _session, class_id: ["firewall unreachable"],
    )
    session, item = _session([_job(BatchProvisionJobStatus.completed)])

    class_status_service.recompute(session=session, class_id=item.id)

    assert item.status == TeachingClassStatus.partial_failed


def test_any_failed_task_needs_attention():
    session, item = _session(
        [
            _job(BatchProvisionJobStatus.completed, done=1, total=2, failed=1),
            _job(BatchProvisionJobStatus.completed),
        ]
    )

    class_status_service.recompute(session=session, class_id=item.id)

    assert item.status == TeachingClassStatus.partial_failed


def test_a_node_still_running_keeps_the_class_building():
    session, item = _session(
        [
            _job(BatchProvisionJobStatus.completed),
            _job(BatchProvisionJobStatus.running, done=0, total=2),
        ],
        status=TeachingClassStatus.pending_review,
    )

    class_status_service.recompute(session=session, class_id=item.id)

    assert item.status == TeachingClassStatus.provisioning


def test_unreviewed_jobs_stay_pending_review():
    session, item = _session(
        [_job(BatchProvisionJobStatus.pending_review, done=0, total=2)],
        status=TeachingClassStatus.provisioning,
    )

    class_status_service.recompute(session=session, class_id=item.id)

    assert item.status == TeachingClassStatus.pending_review


def test_a_node_without_a_job_is_not_ready_yet():
    """只有一半的節點送出過工作時，不能因為那一半完成就宣告可上課。"""
    jobs = [_job(BatchProvisionJobStatus.completed)]
    item = SimpleNamespace(
        id=uuid.uuid4(),
        status=TeachingClassStatus.provisioning,
        updated_at=None,
        name="Linux 實務",
    )
    nodes = [SimpleNamespace(batch_job_id=jobs[0].id), SimpleNamespace(batch_job_id=None)]
    session = _FakeSession(teaching_class=item, nodes=nodes, jobs=jobs)

    class_status_service.recompute(session=session, class_id=item.id)

    assert item.status == TeachingClassStatus.provisioning


def test_archived_classes_are_left_alone():
    session, item = _session(
        [_job(BatchProvisionJobStatus.completed)], status=TeachingClassStatus.archived
    )

    class_status_service.recompute(session=session, class_id=item.id)

    assert item.status == TeachingClassStatus.archived


def test_a_class_without_jobs_keeps_its_status():
    item = SimpleNamespace(
        id=uuid.uuid4(),
        status=TeachingClassStatus.planning,
        updated_at=None,
        name="Linux 實務",
    )
    session = _FakeSession(teaching_class=item, nodes=[], jobs=[])

    class_status_service.recompute(session=session, class_id=item.id)

    assert item.status == TeachingClassStatus.planning
