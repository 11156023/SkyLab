"""Coverage for app.ai.teacher_judge.machine_context helper branches."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from app.ai.teacher_judge import machine_context as mc
from app.models.teaching_class import TeachingClassMachineNode
from tests.ai.teacher_judge.helpers import make_session


class _ScriptedSession:
    """Return pre-baked rows for each successive ``exec`` call."""

    def __init__(self, *results: list[Any]) -> None:
        self._results = list(results)

    def exec(self, _statement: Any) -> Any:
        rows = self._results.pop(0)
        return SimpleNamespace(all=lambda: rows)


def test_guest_os_by_node_id_skips_duplicate_and_missing_vmid() -> None:
    node_a = uuid.uuid4()
    node_b = uuid.uuid4()
    machines = [
        SimpleNamespace(machine_node_id=node_a, vmid=101),
        SimpleNamespace(machine_node_id=node_a, vmid=102),
        SimpleNamespace(machine_node_id=node_b, vmid=None),
        SimpleNamespace(machine_node_id=node_b, vmid=103),
    ]
    resources = [
        SimpleNamespace(vmid=101, guest_os={"family": "debian"}),
        SimpleNamespace(vmid=102, guest_os={"family": "ubuntu"}),
        SimpleNamespace(vmid=103, guest_os={"family": ""}),
    ]
    session = _ScriptedSession(machines, resources)
    result = mc._guest_os_by_node_id(session, [node_a, node_b])  # type: ignore[arg-type]
    assert result == {node_a: {"family": "debian"}}


def test_rubric_item_machine_issues_peer_requirements() -> None:
    token_without_peer = mc.rubric_item_machine_issues(
        {
            "target_node_key": "web",
            "check_steps": ["not-a-dict", {"argv": ["ping", mc.PEER_IP_TOKEN]}],
        }
    )
    assert f"使用 {mc.PEER_IP_TOKEN} 時必須指定 peer_node_key" in token_without_peer

    typed_without_peer = mc.rubric_item_machine_issues(
        {
            "target_node_key": "web",
            "check_steps": [{"collector": {"type": "peer_ping"}}],
        }
    )
    assert "peer_ping collector 必須指定 peer_node_key" in typed_without_peer


def test_snapshot_key_helpers_reject_non_dicts() -> None:
    assert mc.target_node_keys_from_snapshot(None) == set()
    assert mc.peer_node_keys_from_snapshot(None) == set()
    assert mc.peer_node_keys_from_snapshot(
        {"items": [{"peer_node_key": " db "}, {"peer_node_key": ""}, "x"]}
    ) == {"db"}


def test_resolve_class_machine_node() -> None:
    session = make_session()
    class_id = uuid.uuid4()
    node = TeachingClassMachineNode(
        class_id=class_id,
        node_key="web",
        name="Web",
        role="frontend",
        resource_type="lxc",
        cpu=1,
        memory_mb=512,
        disk_gb=8,
        sort_order=0,
    )
    session.add(node)
    session.commit()

    assert mc.resolve_class_machine_node(session, class_id, "  ") is None
    found = mc.resolve_class_machine_node(session, class_id, " web ")
    assert found is not None and found.node_key == "web"
    assert mc.resolve_class_machine_node(session, uuid.uuid4(), "web") is None
