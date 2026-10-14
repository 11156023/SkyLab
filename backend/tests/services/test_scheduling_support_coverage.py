"""app.services.scheduling.support 的範本 VMID 收集與申請單錯誤標記測試（假 session）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import VMProvisioningStatus, VMTemplate
from app.services.scheduling import support


class _FakeSession:
    def __init__(self, rows: dict[type, list] | None = None) -> None:
        self.rows = rows or {}
        self.added: list = []
        self.commits = 0

    def exec(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        rows = list(self.rows.get(entity, []))
        return SimpleNamespace(all=lambda: rows)

    def add(self, obj) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1


def test_template_vmids_collects_template_and_source_vmids() -> None:
    session = _FakeSession(
        {
            VMTemplate: [
                SimpleNamespace(pve_vmid=9000, source_vmid=None),
                SimpleNamespace(pve_vmid="9001", source_vmid=301),
                SimpleNamespace(),
            ]
        }
    )
    assert support._template_vmids(session) == {9000, 9001, 301}


def test_mark_runtime_error_missing_request_is_noop(monkeypatch) -> None:
    monkeypatch.setattr(
        support.vm_request_repo, "get_vm_request_by_id", lambda **kw: None
    )
    monkeypatch.setattr(
        support.vm_request_repo,
        "update_vm_request_provisioning",
        lambda **kw: (_ for _ in ()).throw(AssertionError("no update")),
    )
    session = _FakeSession()
    support.mark_request_runtime_error(
        session=session, request_id=uuid.uuid4(), message="x"
    )
    assert session.commits == 0


@pytest.mark.parametrize(
    ("message", "keeps_warning"),
    [
        ("No feasible node for request", True),
        ("Not enough CAPACITY left", True),
        ("disk image missing", False),
    ],
)
def test_mark_runtime_error_marks_failed_and_capacity_warning(
    monkeypatch, message: str, keeps_warning: bool
) -> None:
    request = SimpleNamespace(
        vmid=10,
        assigned_node="pve1",
        desired_node="pve1",
        actual_node="pve1",
        placement_strategy_used="balanced",
        resource_warning=None,
    )
    monkeypatch.setattr(
        support.vm_request_repo, "get_vm_request_by_id", lambda **kw: request
    )
    updates: list[dict[str, Any]] = []
    monkeypatch.setattr(
        support.vm_request_repo,
        "update_vm_request_provisioning",
        lambda **kw: updates.append(kw),
    )
    session = _FakeSession()
    long_message = message + " " + "x" * 600

    support.mark_request_runtime_error(
        session=session, request_id=uuid.uuid4(), message=long_message
    )

    assert updates[0]["provisioning_status"] == VMProvisioningStatus.failed
    assert updates[0]["provisioning_error"] == long_message[:500]
    assert updates[0]["commit"] is False
    assert session.commits == 1
    if keeps_warning:
        assert request.resource_warning == long_message[:500]
        assert session.added == [request]
    else:
        assert request.resource_warning is None
        assert session.added == []
