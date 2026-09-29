"""整理回歸：防火牆版面只存現存資源的節點、套件匯出面。"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.models import FirewallLayout, Resource
from app.repositories import firewall_layout as firewall_layout_repo


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


class _LayoutSession:
    def __init__(self, existing_vmids: set[int]) -> None:
        self.existing_vmids = existing_vmids
        self.added: list[Any] = []
        self.get_calls: list[int] = []
        self.committed = False

    def get(self, model: type, key: int) -> Any:
        assert model is Resource
        self.get_calls.append(key)
        return object() if key in self.existing_vmids else None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.committed = True


def test_upsert_layout_batch_skips_vm_nodes_without_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    existing = FirewallLayout(
        user_id=user_id, vmid=101, node_type="vm", position_x=0, position_y=0
    )
    lookup = {(101, "vm"): existing}
    monkeypatch.setattr(
        firewall_layout_repo,
        "get_node",
        lambda *, session, user_id, vmid, node_type: lookup.get((vmid, node_type)),
    )
    session = _LayoutSession(existing_vmids={101})

    firewall_layout_repo.upsert_layout_batch(
        session=session,  # type: ignore[arg-type]
        user_id=user_id,
        nodes=[
            {"vmid": 101, "node_type": "vm", "position_x": 1.5, "position_y": 2.5},
            {"vmid": 202, "node_type": "vm", "position_x": 3, "position_y": 4},
            {"vmid": None, "node_type": "gateway", "position_x": 5, "position_y": 6},
        ],
    )

    assert session.committed
    assert existing.vmid == 101
    assert (existing.position_x, existing.position_y) == (1.5, 2.5)
    inserted = [obj for obj in session.added if obj is not existing]
    # vmid 是 resources 的外鍵：找不到 Resource 的 202 直接略過，只新增 gateway
    assert [(n.vmid, n.node_type) for n in inserted] == [(None, "gateway")]
    # 每個 VM 節點只查一次 Resource，gateway 不查
    assert session.get_calls == [101, 202]


def test_repositories_package_has_no_reexports() -> None:
    import app.repositories as repositories

    assert not hasattr(repositories, "update_resource")
    assert not hasattr(repositories, "get_audit_logs_by_user")
