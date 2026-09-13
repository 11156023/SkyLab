"""Materialize a course's logical per-student topology as PVE firewall rules."""

import logging
import uuid
from dataclasses import dataclass
from typing import Any, cast

from sqlmodel import Session, select

from app.infrastructure.proxmox.operations import ResourceType
from app.models import (
    CourseEnvironmentEdge,
    IpAllocation,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStudent,
    TeachingClassStudentMachine,
    User,
)
from app.services.network import firewall_service
from app.services.proxmox import proxmox_service
from app.services.teaching import course_publication_service

COMMENT_PREFIX = "SkyLab:class-net:"
logger = logging.getLogger(__name__)


def _segments(value: str | None) -> set[str]:
    return {
        item.strip()
        for item in (value or "lab-net").replace("/", ",").split(",")
        if item.strip()
    }


def _ip_by_vmid(session: Session, vmid: int) -> str | None:
    return session.exec(
        select(IpAllocation.ip_address).where(IpAllocation.vmid == vmid)
    ).first()


@dataclass(frozen=True)
class PlannedRule:
    """一條拓樸規則該長什麼樣、該掛在哪一台機器上。"""

    vmid: int
    node: str
    resource_type: str
    comment: str
    rule: dict[str, Any]


def sync_scope_rules(
    *,
    comment_prefix: str,
    scope_vmids: set[int],
    planned: list[PlannedRule],
) -> list[str]:
    """把每台機器上帶 ``comment_prefix`` 的規則同步成 ``planned``。

    只做「建立缺的」不夠：重試時機器會換一個新的 vmid 與新的 IP，舊機器上
    指向舊 IP 的白名單會留在原地；那個 IP 回到池子後被分配給別的學生，就變成
    一條意外的跨學生連通。所以要跟 ``firewall_service`` 的 extra-block 規則
    一樣，upsert 之後把帶自家前綴、卻不在目標清單內的孤兒刪掉。

    只碰自己前綴的規則，gateway、extra-block、反向代理等其他來源不受影響。
    """
    desired: dict[int, dict[str, PlannedRule]] = {vmid: {} for vmid in scope_vmids}
    for item in planned:
        desired.setdefault(item.vmid, {})[item.comment] = item

    errors: list[str] = []
    for vmid, wanted in desired.items():
        try:
            info = proxmox_service.find_resource(vmid)
        except Exception:
            # 機器已經不在了，規則跟著它一起消失，不是問題。
            continue
        node = info["node"]
        resource_type = cast(ResourceType, info["type"])
        try:
            existing = firewall_service.get_vm_firewall_rules(node, vmid, resource_type)
        except Exception:
            logger.exception("Failed to list firewall rules vmid=%s", vmid)
            errors.append(f"{vmid}: firewall rules unreadable")
            continue

        present = set()
        stale_positions = []
        for row in existing:
            comment = row.get("comment") or ""
            if not comment.startswith(comment_prefix):
                continue
            if comment in wanted:
                present.add(comment)
            elif row.get("pos") is not None:
                stale_positions.append(int(row["pos"]))

        for comment, item in wanted.items():
            if comment in present:
                continue
            try:
                firewall_service.create_rule(
                    node,
                    vmid,
                    resource_type,
                    {**item.rule, "enable": 1, "comment": comment},
                )
            except Exception:
                logger.exception("Failed to create firewall rule vmid=%s", vmid)
                errors.append(f"{vmid}: firewall configuration failed")

        # 由後往前刪，位置才不會在刪除過程中位移
        for pos in sorted(stale_positions, reverse=True):
            try:
                firewall_service.delete_rule_by_pos(node, vmid, resource_type, pos)
            except Exception:
                logger.exception(
                    "Failed to remove stale topology rule vmid=%s pos=%s", vmid, pos
                )
                errors.append(f"{vmid}: stale firewall rule cleanup failed")
    return errors


def plan_one_way(
    session: Session,
    *,
    scope_id: uuid.UUID,
    comment_prefix: str,
    source_vmid: int,
    target_vmid: int,
    protocol: str = "any",
    port: int | None = None,
) -> list[PlannedRule]:
    """單向開通需要的兩條規則：來源出站、目標入站。"""
    source_ip = _ip_by_vmid(session, source_vmid)
    target_ip = _ip_by_vmid(session, target_vmid)
    if not source_ip or not target_ip:
        raise RuntimeError("課程機器缺少已預留 IP，無法套用隔離網路")
    source = proxmox_service.find_resource(source_vmid)
    target = proxmox_service.find_resource(target_vmid)
    service = protocol if port is None else f"{protocol}/{port}"
    comment = (
        f"{comment_prefix}{str(scope_id)[:8]}:{source_vmid}>{target_vmid}:{service}"
    )
    protocol_fields: dict[str, Any] = {}
    if protocol != "any":
        protocol_fields["proto"] = protocol
    if port is not None:
        protocol_fields["dport"] = str(port)
    return [
        PlannedRule(
            vmid=source_vmid,
            node=source["node"],
            resource_type=source["type"],
            comment=comment,
            # pos 0：管理員設定的額外封鎖網段是 out-DROP，白名單得排在它前面
            rule={
                "type": "out",
                "action": "ACCEPT",
                "pos": 0,
                "dest": target_ip,
                **protocol_fields,
            },
        ),
        PlannedRule(
            vmid=target_vmid,
            node=target["node"],
            resource_type=target["type"],
            comment=comment,
            rule={
                "type": "in",
                "action": "ACCEPT",
                "source": source_ip,
                **protocol_fields,
            },
        ),
    ]


def _ensure_rule(
    *,
    node: str,
    vmid: int,
    resource_type: ResourceType,
    comment: str,
    rule: dict[str, Any],
) -> None:
    existing = firewall_service.get_vm_firewall_rules(node, vmid, resource_type)
    if any(row.get("comment") == comment for row in existing):
        return
    firewall_service.create_rule(
        node,
        vmid,
        resource_type,
        {**rule, "enable": 1, "comment": comment},
    )


def allow_one_way(
    session: Session,
    *,
    scope_id: uuid.UUID,
    comment_prefix: str,
    source_vmid: int,
    target_vmid: int,
    protocol: str = "any",
    port: int | None = None,
) -> None:
    """只建立、不清理。需要同步語意的呼叫端請改用 plan_one_way + sync_scope_rules。"""
    for item in plan_one_way(
        session,
        scope_id=scope_id,
        comment_prefix=comment_prefix,
        source_vmid=source_vmid,
        target_vmid=target_vmid,
        protocol=protocol,
        port=port,
    ):
        _ensure_rule(
            node=item.node,
            vmid=item.vmid,
            resource_type=cast(ResourceType, item.resource_type),
            comment=item.comment,
            rule=item.rule,
        )


def _allow_one_way(
    session: Session,
    *,
    class_id: uuid.UUID,
    source_vmid: int,
    target_vmid: int,
    protocol: str = "any",
    port: int | None = None,
) -> None:
    """Backward-compatible wrapper for class topology and existing tests."""
    allow_one_way(
        session,
        scope_id=class_id,
        comment_prefix=COMMENT_PREFIX,
        source_vmid=source_vmid,
        target_vmid=target_vmid,
        protocol=protocol,
        port=port,
    )


def apply_class_topology(session: Session, *, class_id: uuid.UUID) -> list[str]:
    """Allow peers sharing a logical segment inside each student environment.

    Default VM firewall rules continue to block the managed local subnet, so
    machines owned by different students remain isolated.
    """
    nodes = {
        row.id: row
        for row in session.exec(
            select(TeachingClassMachineNode).where(
                TeachingClassMachineNode.class_id == class_id
            )
        ).all()
    }
    teaching_class = session.get(TeachingClass, class_id)
    edges = (
        list(
            session.exec(
                select(CourseEnvironmentEdge).where(
                    CourseEnvironmentEdge.version_id == teaching_class.course_version_id
                )
            ).all()
        )
        if teaching_class and teaching_class.course_version_id
        else []
    )
    enrollments = session.exec(
        select(TeachingClassStudent).where(TeachingClassStudent.class_id == class_id)
    ).all()
    errors: list[str] = []
    planned: list[PlannedRule] = []
    scope_vmids: set[int] = set()

    def plan(source_vmid: int, target_vmid: int, protocol: str, port: int | None) -> None:
        try:
            planned.extend(
                plan_one_way(
                    session,
                    scope_id=class_id,
                    comment_prefix=COMMENT_PREFIX,
                    source_vmid=source_vmid,
                    target_vmid=target_vmid,
                    protocol=protocol,
                    port=port,
                )
            )
        except Exception:
            logger.exception(
                "Failed to plan class firewall rule class_id=%s source_vmid=%s target_vmid=%s",
                class_id,
                source_vmid,
                target_vmid,
            )
            errors.append(
                f"{source_vmid} → {target_vmid}: firewall configuration failed"
            )

    for enrollment in enrollments:
        machines = [
            row
            for row in session.exec(
                select(TeachingClassStudentMachine).where(
                    TeachingClassStudentMachine.class_student_id == enrollment.id
                )
            ).all()
            if row.vmid is not None and row.status == "completed"
        ]
        machines_by_key = {
            nodes[row.machine_node_id].node_key: row
            for row in machines
            if row.machine_node_id in nodes
        }
        scope_vmids.update(row.vmid for row in machines if row.vmid is not None)
        student = session.get(User, enrollment.user_id)
        if student is not None:
            errors.extend(
                course_publication_service.apply_for_machines(
                    session,
                    version_id=teaching_class.course_version_id,
                    vmid_by_key={
                        key: row.vmid
                        for key, row in machines_by_key.items()
                        if row.vmid is not None
                    },
                    owner=student,
                    scope=f"{teaching_class.code[:12]}-{teaching_class.id.hex[:6]}",
                )
            )
        if edges:
            for edge in edges:
                source = machines_by_key.get(edge.source_node_key)
                target = machines_by_key.get(edge.target_node_key)
                if source is None or target is None:
                    continue
                directions = [(source, target)]
                if edge.direction == "bidirectional":
                    directions.append((target, source))
                for direction_source, direction_target in directions:
                    if direction_source.vmid is None or direction_target.vmid is None:
                        continue
                    plan(
                        direction_source.vmid,
                        direction_target.vmid,
                        edge.protocol,
                        edge.port,
                    )
            continue
        for source in machines:
            source_node = nodes.get(source.machine_node_id)
            if source_node is None:
                continue
            for target in machines:
                if source.id == target.id:
                    continue
                target_node = nodes.get(target.machine_node_id)
                if target_node is None or not (
                    _segments(source_node.network) & _segments(target_node.network)
                ):
                    continue
                if source.vmid is None or target.vmid is None:
                    continue
                plan(source.vmid, target.vmid, "any", None)

    errors.extend(
        sync_scope_rules(
            comment_prefix=COMMENT_PREFIX,
            scope_vmids=scope_vmids,
            planned=planned,
        )
    )
    return errors
