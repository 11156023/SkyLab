"""拓樸規則同步的刪建順序、班級重新上線不再重新發布課程殼。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    ClassCapacityReservation,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
)
from app.services.teaching import class_network_service, class_status_service

PREFIX = class_network_service.COMMENT_PREFIX


# ---------------------------------------------------------------------------
# PVE 行為的假防火牆（新規則插在 pos，預設最前面；刪除依 index）
# ---------------------------------------------------------------------------


class _FakePveFirewall:
    def __init__(self, rules: list[dict[str, Any]]) -> None:
        self.rules = [dict(rule) for rule in rules]

    def _listing(self) -> list[dict[str, Any]]:
        return [{**rule, "pos": index} for index, rule in enumerate(self.rules)]

    def list_vm_firewall_rules_strict(self, _node: str, _vmid: int, _type: str) -> list[dict]:
        return self._listing()

    def create_rule(self, _node: str, _vmid: int, _type: str, rule: dict) -> None:
        body = {key: value for key, value in rule.items() if key != "pos"}
        self.rules.insert(int(rule.get("pos", 0)), body)

    def delete_rule_by_pos(self, _node: str, _vmid: int, _type: str, pos: int) -> None:
        self.rules.pop(pos)

    def comments(self) -> list[str]:
        return [rule.get("comment", "") for rule in self.rules]


@pytest.fixture
def fake_pve(monkeypatch: pytest.MonkeyPatch):
    def install(rules: list[dict[str, Any]]) -> _FakePveFirewall:
        fake = _FakePveFirewall(rules)
        monkeypatch.setattr(
            class_network_service.proxmox_service,
            "find_resource",
            lambda vmid: {"node": "pve1", "type": "qemu", "vmid": vmid},
        )
        for name in ("list_vm_firewall_rules_strict", "create_rule", "delete_rule_by_pos"):
            monkeypatch.setattr(
                class_network_service.firewall_service, name, getattr(fake, name)
            )
        return fake

    return install


def _planned(comment: str) -> class_network_service.PlannedRule:
    return class_network_service.PlannedRule(
        vmid=101,
        comment=comment,
        rule={"type": "out", "action": "ACCEPT", "pos": 0, "dest": "10.0.0.9"},
    )


def test_stale_rule_is_removed_and_block_rules_survive(fake_pve) -> None:
    """重試換了 vmid：舊白名單要刪掉，新白名單要留下，extra-block DROP 不能被誤刪。"""
    stale = f"{PREFIX}abc12345:101>999:any"
    new = f"{PREFIX}abc12345:101>102:any"
    fake = fake_pve(
        [
            {"comment": stale},
            {"comment": "gateway:default"},
            {"comment": "campus-cloud:block-extra:10.0.0.0/8"},
        ]
    )

    errors = class_network_service.sync_scope_rules(
        comment_prefix=PREFIX, scope_vmids={101}, planned=[_planned(new)]
    )

    assert errors == []
    assert fake.comments() == [
        new,
        "gateway:default",
        "campus-cloud:block-extra:10.0.0.0/8",
    ]


def test_several_new_rules_never_delete_foreign_rules(fake_pve) -> None:
    stale = f"{PREFIX}abc12345:101>998:any"
    wanted = [f"{PREFIX}abc12345:101>102:any", f"{PREFIX}abc12345:101>103:any"]
    fake = fake_pve(
        [
            {"comment": "gateway:default"},
            {"comment": "proxy:web"},
            {"comment": stale},
            {"comment": "campus-cloud:block-extra:10.0.0.0/8"},
        ]
    )

    errors = class_network_service.sync_scope_rules(
        comment_prefix=PREFIX,
        scope_vmids={101},
        planned=[_planned(comment) for comment in wanted],
    )

    assert errors == []
    remaining = fake.comments()
    assert stale not in remaining
    assert set(wanted) <= set(remaining)
    assert [c for c in remaining if not c.startswith(PREFIX)] == [
        "gateway:default",
        "proxy:web",
        "campus-cloud:block-extra:10.0.0.0/8",
    ]


def test_resync_is_stable(fake_pve) -> None:
    """同一份計畫連跑兩次，第二次什麼都不動（舊版會每次建了又刪）。"""
    new = f"{PREFIX}abc12345:101>102:any"
    fake = fake_pve([{"comment": f"{PREFIX}abc12345:101>999:any"}])
    for _ in range(2):
        class_network_service.sync_scope_rules(
            comment_prefix=PREFIX, scope_vmids={101}, planned=[_planned(new)]
        )
    assert fake.comments() == [new]


# ---------------------------------------------------------------------------
# 課程殼只在第一次上線時自動發布
# ---------------------------------------------------------------------------


class _FakeExec:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, *, teaching_class, nodes, jobs, reservation=None) -> None:
        self.teaching_class = teaching_class
        self.nodes = nodes
        self.jobs = {job.id: job for job in jobs}
        self.reservation = reservation

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

    def add(self, _obj) -> None:
        return None

    def flush(self) -> None:
        return None


def _completed_job() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=BatchProvisionJobStatus.completed,
        done=2,
        total=2,
        failed_count=0,
    )


@pytest.fixture
def published_calls(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    calls: list[bool] = []
    monkeypatch.setattr(
        class_status_service.class_network_service,
        "apply_class_topology",
        lambda _session, class_id: [],
    )
    monkeypatch.setattr(
        class_status_service.course_service,
        "ensure_class_path",
        lambda _session, *, teaching_class, published=False: calls.append(published),
    )
    return calls


def _class_session(*, status, reservation):
    job = _completed_job()
    item = SimpleNamespace(id=uuid.uuid4(), status=status, updated_at=None, name="C")
    session = _FakeSession(
        teaching_class=item,
        nodes=[SimpleNamespace(batch_job_id=job.id)],
        jobs=[job],
        reservation=reservation,
    )
    return session, item


def test_first_activation_publishes_the_course_shell(published_calls) -> None:
    reservation = SimpleNamespace(status="reserved")
    session, item = _class_session(
        status=TeachingClassStatus.provisioning, reservation=reservation
    )

    class_status_service.recompute(session=session, class_id=item.id)

    assert item.status == TeachingClassStatus.active
    assert reservation.status == "consumed"
    assert published_calls == [True]


def test_reactivation_after_restart_keeps_teacher_unpublish(published_calls) -> None:
    """重啟後快取清空、或補學生後再上線：不可把老師取消發布的課程重新公開。"""
    reservation = SimpleNamespace(status="consumed")
    session, item = _class_session(
        status=TeachingClassStatus.provisioning, reservation=reservation
    )
    class_status_service._APPLIED_TOPOLOGY.pop(item.id, None)

    class_status_service.recompute(session=session, class_id=item.id)

    assert item.status == TeachingClassStatus.active
    assert published_calls == [False]


def test_legacy_class_without_reservation_only_publishes_on_transition(
    published_calls,
) -> None:
    session, item = _class_session(
        status=TeachingClassStatus.active, reservation=None
    )
    class_status_service._APPLIED_TOPOLOGY.pop(item.id, None)

    class_status_service.recompute(session=session, class_id=item.id)

    assert published_calls == [False]
