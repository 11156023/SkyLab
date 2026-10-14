"""Coverage tests for app.services.resource.kind."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from app.services.resource import kind


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Returns queued result lists in call order."""

    def __init__(self, *results: list[Any]) -> None:
        self.results = list(results)
        self.statements: list[Any] = []

    def exec(self, stmt: Any) -> _Rows:
        self.statements.append(stmt)
        return _Rows(self.results.pop(0))


def _res(**kw: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "vmid": 100,
        "teaching_class_id": None,
        "allocation_scope": "personal",
        "request_id": None,
        "user_id": uuid.uuid4(),
    }
    values.update(kw)
    return SimpleNamespace(**values)


def test_classify_branches() -> None:
    assert kind.classify(None) == "personal"
    assert kind.classify(_res(teaching_class_id=uuid.uuid4())) == "teaching_class"
    assert kind.classify(_res(allocation_scope="teaching_class")) == "teaching_class"
    assert kind.classify(_res(), is_practice=True) == "quick_practice"
    assert kind.classify(_res(), request_kind="quick_template") == "quick_practice"
    assert kind.classify(_res(), request_kind="course") == "course"
    assert kind.classify(_res(), request_kind="vm") == "personal"


def test_class_relation_for() -> None:
    viewer = uuid.uuid4()
    class_id = uuid.uuid4()
    assert (
        kind.class_relation_for(None, viewer_id=viewer, owned_class_ids=set()) is None
    )
    assert (
        kind.class_relation_for(_res(), viewer_id=viewer, owned_class_ids=set()) is None
    )
    assert (
        kind.class_relation_for(
            _res(teaching_class_id=class_id, user_id=viewer),
            viewer_id=viewer,
            owned_class_ids={class_id},
        )
        == "student"
    )
    assert (
        kind.class_relation_for(
            _res(teaching_class_id=class_id),
            viewer_id=viewer,
            owned_class_ids={class_id},
        )
        == "teacher"
    )
    assert (
        kind.class_relation_for(
            _res(teaching_class_id=class_id), viewer_id=viewer, owned_class_ids=set()
        )
        is None
    )


def test_request_kinds_empty_skips_query() -> None:
    session = _FakeSession()
    assert kind.request_kinds(session, [None, None]) == {}  # type: ignore[arg-type]
    assert session.statements == []


def test_request_kinds_drops_blank_kinds() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    session = _FakeSession([(a, "course"), (b, None)])
    assert kind.request_kinds(session, [a, None, b]) == {a: "course"}  # type: ignore[arg-type]


def test_classify_many_uses_batched_lookups() -> None:
    practice_req, course_req, plain_req = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    resources = [
        _res(vmid=1, request_id=practice_req),
        _res(vmid=2, request_id=course_req),
        _res(vmid=3, request_id=plain_req),
        _res(vmid=4, request_id=None),
    ]
    session = _FakeSession([practice_req], [(course_req, "course")])
    out = kind.classify_many(session, resources)  # type: ignore[arg-type]
    assert out == {1: "quick_practice", 2: "course", 3: "personal", 4: "personal"}
    assert len(session.statements) == 2


def test_user_display_names() -> None:
    assert kind.user_display_names(_FakeSession(), [None]) == {}  # type: ignore[arg-type,list-item]
    named, unnamed = uuid.uuid4(), uuid.uuid4()
    session = _FakeSession(
        [(named, "王小明", "ming@example.com"), (unnamed, None, "x@example.com")]
    )
    out = kind.user_display_names(session, [named, unnamed, named])  # type: ignore[arg-type]
    assert out == {named: "王小明", unnamed: "x@example.com"}
