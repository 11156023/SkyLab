"""Coverage tests for app.services.resource.kind."""

from __future__ import annotations

import uuid
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


def test_request_kinds_empty_skips_query() -> None:
    session = _FakeSession()
    assert kind.request_kinds(session, [None, None]) == {}  # type: ignore[arg-type]
    assert session.statements == []


def test_request_kinds_drops_blank_kinds() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    session = _FakeSession([(a, "course"), (b, None)])
    assert kind.request_kinds(session, [a, None, b]) == {a: "course"}  # type: ignore[arg-type]


def test_user_display_names() -> None:
    assert kind.user_display_names(_FakeSession(), [None]) == {}  # type: ignore[arg-type,list-item]
    named, unnamed = uuid.uuid4(), uuid.uuid4()
    session = _FakeSession(
        [(named, "王小明", "ming@example.com"), (unnamed, None, "x@example.com")]
    )
    out = kind.user_display_names(session, [named, unnamed, named])  # type: ignore[arg-type]
    assert out == {named: "王小明", unnamed: "x@example.com"}
