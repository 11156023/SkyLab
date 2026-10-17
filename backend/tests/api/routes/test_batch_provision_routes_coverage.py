"""app/api/routes/batch_provision.py 路由層的單元測試（直接呼叫路由函式，service 全部替換）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.routes import batch_provision as bp
from app.exceptions import NotFoundError
from app.models import BatchProvisionJobStatus, TeachingClass
from app.schemas.batch_provision import BatchProvisionReviewRequest
from app.services.scheduling import recurrence

CLASS_ID = uuid.uuid4()
OWNER = uuid.uuid4()


class _FakeSession:
    def __init__(self, classes: dict[uuid.UUID, Any] | None = None) -> None:
        self.classes = classes or {}

    def get(self, model: type, key: Any) -> Any:
        assert model is TeachingClass
        return self.classes.get(key)


def _user() -> Any:
    return SimpleNamespace(id=uuid.uuid4())


def _job(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "teaching_class_id": CLASS_ID,
        "recurrence_rule": None,
        "recurrence_duration_minutes": None,
        "schedule_timezone": "Asia/Taipei",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def to_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bp.batch_provision_service, "to_public", lambda session, job: ("public", job.id)
    )


def _get_job(monkeypatch: pytest.MonkeyPatch, job: Any) -> None:
    monkeypatch.setattr(bp.bp_repo, "get_job", lambda session, job_id: job)


# ─── 狀態 ─────────────────────────────────────────────────────────────────────


def test_get_batch_status_job_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _get_job(monkeypatch, None)
    with pytest.raises(NotFoundError):
        bp.get_batch_status(
            job_id=uuid.uuid4(), session=_FakeSession(), current_user=_user()
        )


def test_get_batch_status_class_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _get_job(monkeypatch, _job())
    with pytest.raises(NotFoundError):
        bp.get_batch_status(
            job_id=uuid.uuid4(), session=_FakeSession(), current_user=_user()
        )


def test_get_batch_status_success(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job()
    _get_job(monkeypatch, job)
    checked: list[Any] = []
    monkeypatch.setattr(
        bp, "require_teaching_access", lambda user, owner_id: checked.append(owner_id)
    )
    session = _FakeSession({CLASS_ID: SimpleNamespace(owner_id=OWNER)})
    assert bp.get_batch_status(
        job_id=job.id, session=session, current_user=_user()
    ) == (
        "public",
        job.id,
    )
    assert checked == [OWNER]


# ─── 審核清單 ─────────────────────────────────────────────────────────────────


def test_list_pending_and_review_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [_job(), _job()]
    monkeypatch.setattr(bp.bp_repo, "list_pending_review_jobs", lambda session: jobs)
    captured: dict[str, Any] = {}

    def _list(session: Any, status: Any, limit: int) -> list[Any]:
        captured.update(status=status, limit=limit)
        return jobs[:1]

    monkeypatch.setattr(bp.bp_repo, "list_review_jobs", _list)
    assert bp.list_pending_review(session=_FakeSession(), _=_user()) == [
        ("public", j.id) for j in jobs
    ]
    assert bp.list_review_jobs(
        session=_FakeSession(),
        _=_user(),
        status=BatchProvisionJobStatus.approved,
        limit=10,
    ) == [("public", jobs[0].id)]
    assert captured == {"status": BatchProvisionJobStatus.approved, "limit": 10}


# ─── 週期預覽 ─────────────────────────────────────────────────────────────────


def test_recurrence_preview_job_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _get_job(monkeypatch, None)
    with pytest.raises(NotFoundError):
        bp.get_recurrence_preview(
            job_id=uuid.uuid4(), session=_FakeSession(), _=_user()
        )


def test_recurrence_preview_stops_when_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _get_job(
        monkeypatch,
        _job(recurrence_rule="FREQ=DAILY;COUNT=1", recurrence_duration_minutes=30),
    )
    start = datetime(2030, 1, 1, tzinfo=UTC)
    results = iter([(start, start + timedelta(minutes=30)), None])
    monkeypatch.setattr(recurrence, "compute_next_window", lambda **kw: next(results))
    preview = bp.get_recurrence_preview(
        job_id=uuid.uuid4(), session=_FakeSession(), _=_user(), count=5
    )
    assert len(preview.windows) == 1


# ─── 審核 ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_review_batch_job(monkeypatch: pytest.MonkeyPatch, decision: str) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        bp.batch_provision_service,
        "approve_batch_job",
        lambda **kw: calls.append("approve"),
    )
    monkeypatch.setattr(
        bp.batch_provision_service,
        "reject_batch_job",
        lambda **kw: calls.append("reject"),
    )
    job = _job()
    _get_job(monkeypatch, job)
    body = BatchProvisionReviewRequest(decision=decision, review_comment="ok")
    assert bp.review_batch_job(
        job_id=job.id, body=body, session=_FakeSession(), current_user=_user()
    ) == ("public", job.id)
    assert calls == ["approve" if decision == "approved" else "reject"]


def test_review_batch_job_missing_after_review(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bp.batch_provision_service, "approve_batch_job", lambda **kw: None
    )
    _get_job(monkeypatch, None)
    with pytest.raises(NotFoundError):
        bp.review_batch_job(
            job_id=uuid.uuid4(),
            body=BatchProvisionReviewRequest(decision="approved"),
            session=_FakeSession(),
            current_user=_user(),
        )


def test_review_teaching_class_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(NotFoundError):
        bp.review_teaching_class_jobs(
            class_id=CLASS_ID,
            body=BatchProvisionReviewRequest(decision="approved"),
            session=_FakeSession(),
            current_user=_user(),
        )

    item = SimpleNamespace(owner_id=OWNER)
    jobs = [_job(), _job()]
    captured: dict[str, Any] = {}

    def _review(session: Any, **kwargs: Any) -> list[Any]:
        captured.update(kwargs)
        return jobs

    monkeypatch.setattr(bp.class_provision_service, "review_teaching_class", _review)
    user = _user()
    result = bp.review_teaching_class_jobs(
        class_id=CLASS_ID,
        body=BatchProvisionReviewRequest(decision="rejected", review_comment="no"),
        session=_FakeSession({CLASS_ID: item}),
        current_user=user,
    )
    assert result == [("public", j.id) for j in jobs]
    assert captured == {
        "item": item,
        "reviewer_id": user.id,
        "decision": BatchProvisionJobStatus.rejected,
        "review_comment": "no",
    }
