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

PEER_IP_TOKEN = "{{peer.ip}}"


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
                col(TeachingClassMachineNode.sort_order),
                col(TeachingClassMachineNode.node_key),
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


def canonicalize_machine_node_key(
    raw_value: str | None,
    entries: list[dict[str, Any]] | None,
) -> str | None:
    """Resolve a node key or derived P label to the stable class-local key."""

    normalized = str(raw_value or "").strip()
    if not normalized:
        return None
    available = entries or []
    exact_keys = {
        str(entry.get("node_key") or "").strip()
        for entry in available
        if str(entry.get("node_key") or "").strip()
    }
    if normalized in exact_keys:
        return normalized
    matching_labels = {
        str(entry.get("node_key") or "").strip()
        for entry in available
        if str(entry.get("display_label") or "").strip().casefold()
        == normalized.casefold()
        and str(entry.get("node_key") or "").strip()
    }
    if len(matching_labels) == 1:
        return next(iter(matching_labels))
    raise ValueError(f"無法將機器標籤「{normalized}」解析為目前班級唯一的 node_key。")


def rubric_item_machine_issues(item: dict[str, Any]) -> list[str]:
    """Validate the executor/peer identity and reserved peer token contract."""

    target_node_key = str(item.get("target_node_key") or "").strip() or None
    peer_node_key = str(item.get("peer_node_key") or "").strip() or None
    argv_parts: list[str] = []
    for step in item.get("check_steps") or []:
        if not isinstance(step, dict):
            continue
        raw_argv = step.get("argv")
        if not isinstance(raw_argv, list):
            parameters = step.get("parameters")
            raw_argv = parameters.get("argv") if isinstance(parameters, dict) else []
        if isinstance(raw_argv, list):
            argv_parts.extend(part for part in raw_argv if isinstance(part, str))
    token_parts = [part for part in argv_parts if PEER_IP_TOKEN in part]
    issues: list[str] = []
    if peer_node_key and peer_node_key == target_node_key:
        issues.append("peer_node_key 不得與 target_node_key 相同")
    if any(part != PEER_IP_TOKEN for part in token_parts):
        issues.append(f"{PEER_IP_TOKEN} 只能作為完整 argv element")
    if token_parts and not peer_node_key:
        issues.append(f"使用 {PEER_IP_TOKEN} 時必須指定 peer_node_key")
    if peer_node_key and item.get("detectable") == "auto" and PEER_IP_TOKEN not in argv_parts:
        issues.append(f"指定 peer_node_key 的 auto 項目必須在 argv 使用 {PEER_IP_TOKEN}")
    return issues


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


def peer_node_keys_from_snapshot(snapshot: dict[str, Any] | None) -> set[str]:
    """Collect explicitly declared observed peer keys from rubric items."""

    if not isinstance(snapshot, dict):
        return set()
    return {
        str(item.get("peer_node_key") or "").strip()
        for item in snapshot.get("items") or []
        if isinstance(item, dict) and str(item.get("peer_node_key") or "").strip()
    }


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
    "PEER_IP_TOKEN",
    "canonicalize_machine_node_key",
    "format_machine_context",
    "load_class_machine_nodes",
    "machine_context_entries",
    "machine_node_display_label",
    "peer_node_keys_from_snapshot",
    "resolve_class_machine_node",
    "rubric_item_machine_issues",
    "target_node_keys_from_snapshot",
]
