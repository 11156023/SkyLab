"""app.repositories.spec_change_request 的分支覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import SpecChangeRequestStatus
from app.repositories import spec_change_request as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _create(session: Session, *, vmid: int = 101, user_id=None, commit=True):
    return repo.create_spec_change_request(
        session=session,
        user_id=user_id or uuid.uuid4(),
        vmid=vmid,
        change_type="cpu",
        reason="need more",
        current_cpu=1,
        requested_cpu=2,
        commit=commit,
    )


def test_cancel_open_requests_commit_and_flush(session: Session) -> None:
    _create(session, vmid=200)
    assert (
        repo.cancel_open_spec_change_requests_for_vmid(
            session=session, vmid=200, comment="gone", commit=False
        )
        == 1
    )
    assert repo.get_open_spec_change_request_by_vmid(session=session, vmid=200) is None
    _create(session, vmid=201)
    assert (
        repo.cancel_open_spec_change_requests_for_vmid(
            session=session, vmid=201, comment="gone"
        )
        == 1
    )
    # 沒有處理中的申請時不寫入
    assert (
        repo.cancel_open_spec_change_requests_for_vmid(
            session=session, vmid=201, comment="gone"
        )
        == 0
    )


def test_list_by_user_and_all_with_filters(session: Session) -> None:
    user_id = uuid.uuid4()
    first = _create(session, vmid=300, user_id=user_id)
    _create(session, vmid=301, user_id=user_id)
    _create(session, vmid=302)

    rows, count = repo.get_spec_change_requests_by_user(
        session=session, user_id=user_id
    )
    assert count == 2
    assert {r.vmid for r in rows} == {300, 301}

    repo.update_spec_change_request_status(
        session=session,
        request_id=first.id,
        status="approved",
        reviewer_id=uuid.uuid4(),
    )

    rows, count = repo.get_all_spec_change_requests(session=session)
    assert count == 3
    rows, count = repo.get_all_spec_change_requests(session=session, status="approved")
    assert count == 1 and rows[0].id == first.id
    rows, count = repo.get_all_spec_change_requests(
        session=session, status=SpecChangeRequestStatus.pending, vmid=302
    )
    assert count == 1 and rows[0].vmid == 302


def test_update_status_and_specs_flush_and_missing(session: Session) -> None:
    req = _create(session)
    updated = repo.update_spec_change_request_status(
        session=session,
        request_id=req.id,
        status=SpecChangeRequestStatus.rejected,
        reviewer_id=uuid.uuid4(),
        review_comment="no",
        commit=False,
    )
    assert updated.status == SpecChangeRequestStatus.rejected
    with pytest.raises(ValueError):
        repo.update_spec_change_request_status(
            session=session,
            request_id=uuid.uuid4(),
            status="approved",
            reviewer_id=uuid.uuid4(),
        )

    specs = repo.update_spec_change_current_specs(
        session=session,
        request_id=req.id,
        current_cpu=4,
        current_memory=2048,
        current_disk=20,
    )
    assert specs.current_cpu == 4
    specs = repo.update_spec_change_current_specs(
        session=session,
        request_id=req.id,
        current_cpu=8,
        current_memory=None,
        current_disk=None,
        commit=False,
    )
    assert specs.current_cpu == 8
    with pytest.raises(ValueError):
        repo.update_spec_change_current_specs(
            session=session,
            request_id=uuid.uuid4(),
            current_cpu=1,
            current_memory=1,
            current_disk=1,
        )


def test_apply_lifecycle_commit_flush_and_missing(session: Session) -> None:
    req = _create(session)
    for commit in (True, False):
        started = repo.mark_spec_change_apply_started(
            session=session, request_id=req.id, commit=commit
        )
        assert started.apply_started_at is not None
        assert started.apply_error is None

        failed = repo.mark_spec_change_apply_failed(
            session=session, request_id=req.id, error="x" * 3000, commit=commit
        )
        assert len(failed.apply_error) == 2000

        applied = repo.mark_spec_change_applied(
            session=session, request_id=req.id, warning="boot failed", commit=commit
        )
        assert applied.applied_at is not None
        assert applied.apply_error == "boot failed"

    missing = uuid.uuid4()
    with pytest.raises(ValueError):
        repo.mark_spec_change_apply_started(session=session, request_id=missing)
    with pytest.raises(ValueError):
        repo.mark_spec_change_applied(session=session, request_id=missing)
    with pytest.raises(ValueError):
        repo.mark_spec_change_apply_failed(
            session=session, request_id=missing, error="e"
        )
