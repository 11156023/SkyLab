"""機器來源判定：Resource 沒有 source 欄位，四種來源要用組合推導，這裡鎖住規則。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.services.resource import kind

ME = uuid.uuid4()
OTHER = uuid.uuid4()
CLASS = uuid.uuid4()


def _res(**kw):
    base = {
        "vmid": 100,
        "user_id": ME,
        "teaching_class_id": None,
        "allocation_scope": "personal",
        "request_id": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_class_machine_wins_over_everything() -> None:
    r = _res(teaching_class_id=CLASS, allocation_scope="teaching_class", request_id=uuid.uuid4())
    assert kind.classify(r, is_practice=True, request_kind="course") == "teaching_class"


def test_orphaned_class_scope_still_counts_as_class() -> None:
    r = _res(teaching_class_id=None, allocation_scope="teaching_class")
    assert kind.classify(r) == "teaching_class"


def test_quick_practice_by_session_membership_or_request_kind() -> None:
    r = _res(request_id=uuid.uuid4())
    assert kind.classify(r, is_practice=True) == "quick_practice"
    assert kind.classify(r, request_kind="quick_template") == "quick_practice"


def test_course_lab_by_request_kind() -> None:
    assert kind.classify(_res(request_id=uuid.uuid4()), request_kind="course") == "course"


def test_everything_else_is_personal() -> None:
    assert kind.classify(_res()) == "personal"
    assert kind.classify(_res(request_id=uuid.uuid4()), request_kind="research") == "personal"
    assert kind.classify(None) == "personal"


def test_class_relation_ownership_beats_class_owner() -> None:
    r = _res(teaching_class_id=CLASS, allocation_scope="teaching_class", user_id=OTHER)
    assert kind.class_relation_for(r, viewer_id=ME, owned_class_ids={CLASS}) == "teacher"
    mine = _res(teaching_class_id=CLASS, allocation_scope="teaching_class", user_id=ME)
    assert kind.class_relation_for(mine, viewer_id=ME, owned_class_ids=set()) == "student"
    # 老師擁有自己班上的機器：是「分配給我的班級機」，不是學生機器
    assert kind.class_relation_for(mine, viewer_id=ME, owned_class_ids={CLASS}) == "student"
    assert kind.class_relation_for(mine, viewer_id=OTHER, owned_class_ids=set()) is None
    assert kind.class_relation_for(_res(), viewer_id=ME, owned_class_ids={CLASS}) is None


def test_classify_many_batches_lookups(monkeypatch) -> None:
    practice_req = uuid.uuid4()
    course_req = uuid.uuid4()
    monkeypatch.setattr(kind, "practice_request_ids", lambda session: {practice_req})
    monkeypatch.setattr(
        kind, "request_kinds", lambda session, ids: {course_req: "course"}
    )
    rows = [
        _res(vmid=1, request_id=practice_req),
        _res(vmid=2, request_id=course_req),
        _res(vmid=3),
        _res(vmid=4, teaching_class_id=CLASS, allocation_scope="teaching_class"),
    ]
    assert kind.classify_many(None, rows) == {  # type: ignore[arg-type]
        1: "quick_practice",
        2: "course",
        3: "personal",
        4: "teaching_class",
    }
