"""Logical machine context and target helpers for Teacher Judge.

Teacher Judge must reason about the class topology, not provider-specific VM
identifiers. This module keeps that boundary in one place so prompt builders,
artifact validation, and run resolution use the same node identity rules.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlmodel import Session, col, select

from app.infrastructure.proxmox.os_detection import format_os_token
from app.models.resource import Resource
from app.models.teaching_class import (
    TeachingClassMachineNode,
    TeachingClassStudentMachine,
)


def load_class_machine_nodes(
    session: Session,
    teaching_class_id: uuid.UUID,
) -> list[TeachingClassMachineNode]:
    """Return class machine nodes in their teacher-defined display order."""

    return list(
        session.exec(
            select(TeachingClassMachineNode)
            .where(TeachingClassMachineNode.class_id == teaching_class_id)
            .order_by(
                TeachingClassMachineNode.sort_order,
                TeachingClassMachineNode.node_key,
            )
        ).all()
    )


def machine_node_display_label(node: TeachingClassMachineNode) -> str:
    """Return the derived P label; it is never used as an identity."""

    return f"P{int(node.sort_order) + 1}"


def _guest_os_by_node_id(
    session: Session,
    node_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict[str, Any]]:
    """從班級學生機的 Resource 讀結構化 guest_os（純 DB 讀取，不做 PVE I/O）。

    同一 node 的學生機都克隆自同一範本，取第一個有身份的 Resource 為準。
    """
    if not node_ids:
        return {}
    machines = list(
        session.exec(
            select(TeachingClassStudentMachine).where(
                col(TeachingClassStudentMachine.machine_node_id).in_(node_ids)
            )
        ).all()
    )
    vmids = [machine.vmid for machine in machines if machine.vmid is not None]
    if not vmids:
        return {}
    resources_by_vmid = {
        resource.vmid: resource
        for resource in session.exec(
            select(Resource).where(col(Resource.vmid).in_(vmids))
        ).all()
    }
    result: dict[uuid.UUID, dict[str, Any]] = {}
    for machine in machines:
        if machine.vmid is None or machine.machine_node_id in result:
            continue
        resource = resources_by_vmid.get(machine.vmid)
        identity = getattr(resource, "guest_os", None) if resource else None
        if isinstance(identity, dict) and identity.get("family"):
            result[machine.machine_node_id] = identity
    return result


def machine_context_entries(
    session: Session,
    teaching_class_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Build the provider-independent machine context exposed to the model."""

    nodes = load_class_machine_nodes(session, teaching_class_id)
    os_by_node = _guest_os_by_node_id(session, [node.id for node in nodes])
    return [
        {
            "display_label": machine_node_display_label(node),
            "node_key": node.node_key,
            "name": node.name,
            "role": node.role,
            "resource_type": node.resource_type,
            "executor_capability": "linux_ssh_sftp_python3",
            "os": format_os_token(os_by_node.get(node.id)),
        }
        for node in nodes
    ]


def format_machine_context(entries: list[dict[str, Any]] | None) -> str:
    """Format logical nodes for a prompt without leaking VM/provider details."""

    if not entries:
        return "目前沒有可用的班級機器拓撲；不要猜測 target_node_key。"
    lines = [
        "P 標籤只供顯示；後續目標身份一律使用 node_key。",
        *(
            " | ".join(
                [
                    str(entry.get("display_label") or ""),
                    f"node_key={entry.get('node_key') or ''}",
                    f"name={entry.get('name') or ''}",
                    f"role={entry.get('role') or ''}",
                    f"resource_type={entry.get('resource_type') or ''}",
                    f"os={entry.get('os') or 'unknown'}",
                    f"executor={entry.get('executor_capability') or 'linux_ssh_sftp_python3'}",
                ]
            )
            for entry in entries
        ),
    ]
    return "\n".join(lines)


def target_node_keys_from_snapshot(snapshot: dict[str, Any] | None) -> set[str]:
    """Collect logical target keys from a rubric/artifact snapshot."""

    if not isinstance(snapshot, dict):
        return set()
    raw_items = snapshot.get("items")
    keys: set[str] = set()
    if isinstance(raw_items, list):
        keys = {
            str(item.get("target_node_key") or "").strip()
            for item in raw_items
            if isinstance(item, dict) and str(item.get("target_node_key") or "").strip()
        }
    top_level_key = str(snapshot.get("target_node_key") or "").strip()
    if top_level_key:
        keys.add(top_level_key)
    return keys


def resolve_class_machine_node(
    session: Session,
    teaching_class_id: uuid.UUID,
    node_key: str,
) -> TeachingClassMachineNode | None:
    """Resolve a node key within a class; never resolve globally by key alone."""

    normalized = str(node_key or "").strip()
    if not normalized:
        return None
    return session.exec(
        select(TeachingClassMachineNode).where(
            TeachingClassMachineNode.class_id == teaching_class_id,
            TeachingClassMachineNode.node_key == normalized,
        )
    ).first()


__all__ = [
    "format_machine_context",
    "load_class_machine_nodes",
    "machine_context_entries",
    "machine_node_display_label",
    "resolve_class_machine_node",
    "target_node_keys_from_snapshot",
]
