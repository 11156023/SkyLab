"""學生首頁機器卡的標題：班級機、快速練習機顯示依據的課程環境名稱。

清單頁整批查（_course_environment_names），用假 session 驗證查找路徑與查詢次數，不碰資料庫。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.services.resource import resource_service

VERSION_ID = uuid.uuid4()
OTHER_VERSION_ID = uuid.uuid4()


class _FakeSession:
    """依序回傳每次 exec 的結果，並記下查了幾次。"""

    def __init__(self, *results):
        self._results = list(results)
        self.calls = 0

    def exec(self, _stmt):
        self.calls += 1
        rows = self._results.pop(0) if self._results else []
        return SimpleNamespace(all=lambda: rows)


def _class_machine(vmid, class_id):
    return SimpleNamespace(
        vmid=vmid, allocation_scope="teaching_class", teaching_class_id=class_id, request_id=None
    )


def _requested_machine(vmid, request_id):
    return SimpleNamespace(
        vmid=vmid, allocation_scope="personal", teaching_class_id=None, request_id=request_id
    )


def test_class_machine_uses_pinned_version_environment_name():
    class_id = uuid.uuid4()
    session = _FakeSession([(VERSION_ID, "Linux 系統管理")])
    names = resource_service._course_environment_names(
        session,
        [_class_machine(101, class_id)],
        {class_id: SimpleNamespace(course_version_id=VERSION_ID)},
    )
    assert names == {101: "Linux 系統管理"}


def test_quick_practice_machine_uses_session_version_environment_name():
    request_id = uuid.uuid4()
    session = _FakeSession([(request_id, VERSION_ID)], [(VERSION_ID, "Linux 系統管理")])
    names = resource_service._course_environment_names(
        session, [_requested_machine(202, request_id)], {}
    )
    assert names == {202: "Linux 系統管理"}


def test_personal_machine_has_no_environment_name():
    # 個人申請的申請單不在任何練習場次裡：查不到版本，就不再查環境名稱
    session = _FakeSession([])
    names = resource_service._course_environment_names(
        session, [_requested_machine(303, uuid.uuid4())], {}
    )
    assert names == {}
    assert session.calls == 1


def test_blank_or_missing_environment_name_falls_back_to_none():
    class_id = uuid.uuid4()
    classes = {class_id: SimpleNamespace(course_version_id=VERSION_ID)}
    for rows in ([(VERSION_ID, "   ")], [(VERSION_ID, None)], []):
        names = resource_service._course_environment_names(
            _FakeSession(rows), [_class_machine(101, class_id)], classes
        )
        assert names == {}


def test_many_machines_are_resolved_with_at_most_two_queries():
    class_a, class_b = uuid.uuid4(), uuid.uuid4()
    practice_request = uuid.uuid4()
    session = _FakeSession(
        [(practice_request, OTHER_VERSION_ID)],
        [(VERSION_ID, "網路實務"), (OTHER_VERSION_ID, "容器入門")],
    )
    names = resource_service._course_environment_names(
        session,
        [
            _class_machine(1, class_a),
            _class_machine(2, class_a),
            _class_machine(3, class_b),
            _requested_machine(4, practice_request),
            _requested_machine(5, uuid.uuid4()),
        ],
        {
            class_a: SimpleNamespace(course_version_id=VERSION_ID),
            class_b: SimpleNamespace(course_version_id=VERSION_ID),
        },
    )
    assert names == {1: "網路實務", 2: "網路實務", 3: "網路實務", 4: "容器入門"}
    assert session.calls == 2
