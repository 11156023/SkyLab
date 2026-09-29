"""Materialize a course's logical per-student topology as PVE firewall rules."""

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

from sqlmodel import Session, select

from app.infrastructure.proxmox.operations import ResourceType
from app.models import (
    CourseEnvironmentEdge,
    CourseEnvironmentVersion,
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
# 版本上的機器互通策略（見 CourseEnvironmentVersion.peer_policy）
PEER_POLICY_EXPLICIT = "explicit"
PEER_POLICY_SEGMENT = "segment"
# PVE 只接受這些協定帶 dport；any 沒有 proto、icmp 類用 icmp-type，帶 dport 會被 400 拒絕
PORT_PROTOCOLS = frozenset({"tcp", "udp", "sctp"})
logger = logging.getLogger(__name__)


def network_segments(value: str | None) -> set[str]:
    """把節點的 network 欄位（``lab-net / backend-net, mgmt``）拆成網段集合。"""
    return {
        item.strip()
        for item in (value or "lab-net").replace("/", ",").split(",")
        if item.strip()
    }


def peer_policy_for_version(session: Session, version_id: uuid.UUID | None) -> str:
    """版本找不到時視為 explicit：寧可少開，也不要把整段網路打通。"""
    if version_id is None:
        return PEER_POLICY_EXPLICIT
    version = session.get(CourseEnvironmentVersion, version_id)
    if version is None or version.peer_policy != PEER_POLICY_SEGMENT:
        return PEER_POLICY_EXPLICIT
    return PEER_POLICY_SEGMENT


def topology_directions(
    *,
    peer_policy: str,
    edges: Iterable[CourseEnvironmentEdge],
    vmid_by_key: dict[str, int],
    network_by_key: dict[str, str | None],
) -> list[tuple[int, int, str, int | None]]:
    """把版本的互通策略展開成一位學生那組機器之間的單向開通清單。

    回傳 ``(來源 vmid, 目標 vmid, protocol, port)``；正式班級與快速練習共用。

    - explicit：只開老師畫的連線，``bidirectional`` 再補反方向；端點機器不在
      ``vmid_by_key``（還沒建好或建失敗）的連線略過
    - segment：共用邏輯網段的機器兩兩全協定全埠互通；節點不在 ``network_by_key``
      的機器略過
    """
    directions: list[tuple[int, int, str, int | None]] = []
    if peer_policy != PEER_POLICY_SEGMENT:
        for edge in edges:
            source = vmid_by_key.get(edge.source_node_key)
            target = vmid_by_key.get(edge.target_node_key)
            if source is None or target is None:
                continue
            directions.append((source, target, edge.protocol, edge.port))
            if edge.direction == "bidirectional":
                directions.append((target, source, edge.protocol, edge.port))
        return directions
    for source_key, source in vmid_by_key.items():
        if source_key not in network_by_key:
            continue
        source_segments = network_segments(network_by_key[source_key])
        for target_key, target in vmid_by_key.items():
            if source_key == target_key or target_key not in network_by_key:
                continue
            if source_segments & network_segments(network_by_key[target_key]):
                directions.append((source, target, "any", None))
    return directions


def _ip_by_vmid(session: Session, vmid: int) -> str | None:
    return session.exec(
        select(IpAllocation.ip_address).where(IpAllocation.vmid == vmid)
    ).first()


@dataclass(frozen=True)
class PlannedRule:
    """一條拓樸規則該長什麼樣、該掛在哪一台機器上。

    節點與類型不存在這裡：``sync_scope_rules`` 寫入前會依 vmid 重新查一次。
    """

    vmid: int
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
    一樣，先把帶自家前綴、卻不在目標清單內的孤兒刪掉，再補建缺的規則。

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
            # 讀不到就不能當成「沒有規則」：那樣會重複建立、孤兒也不會被刪掉。
            existing = firewall_service.list_vm_firewall_rules_strict(
                node, vmid, resource_type
            )
        except Exception:
            logger.exception("Failed to list firewall rules vmid=%s", vmid)
            errors.append(f"{vmid}: firewall rules unreadable")
            continue

        present = set()
        stale: list[tuple[int, str]] = []
        for row in existing:
            comment = row.get("comment") or ""
            if not comment.startswith(comment_prefix):
                continue
            if comment in wanted:
                present.add(comment)
            elif row.get("pos") is not None:
                stale.append((int(row["pos"]), comment))

        # 先刪孤兒、再建新規則：PVE 新規則一律插在最前面（out 規則還明確帶 pos 0），
        # 先建的話每建一條，剛記下的位置就整批往後位移一格，接著刪到的會是剛建好的
        # 規則、gateway 規則或管理員的 extra-block DROP，而真正該刪的舊白名單留在原地。
        # 由後往前刪，前面的位置才不會因為刪除而位移（同 _apply_extra_block_rules）。
        for pos, comment in sorted(stale, reverse=True):
            try:
                firewall_service.delete_rule_by_pos(node, vmid, resource_type, pos)
            except Exception:
                logger.exception(
                    "Failed to remove stale topology rule vmid=%s pos=%s comment=%s",
                    vmid,
                    pos,
                    comment,
                )
                errors.append(f"{vmid}: stale firewall rule cleanup failed")

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
    # 兩台機器都必須還在：找不到會拋錯，由呼叫端記成防火牆設定失敗
    proxmox_service.find_resource(source_vmid)
    proxmox_service.find_resource(target_vmid)
    # API 已會把 any／icmp 的 port 清掉，但舊版本存下的連線仍可能帶 port：
    # 照「該協定全開」處理，comment 也跟著用無 port 的寫法，才會跟實際規則一致。
    if protocol not in PORT_PROTOCOLS:
        port = None
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
            comment=comment,
            rule={
                "type": "in",
                "action": "ACCEPT",
                "source": source_ip,
                **protocol_fields,
            },
        ),
    ]


def apply_class_topology(session: Session, *, class_id: uuid.UUID) -> list[str]:
    """把版本的機器互通策略實體化成每位學生自己那組機器上的規則。

    explicit：只開老師畫的連線，沒畫就完全隔離。
    segment：共用邏輯網段的機器全協定全埠互通（舊行為）。

    不同學生的機器之間靠各自的 policy_in=DROP 隔離，這裡不會跨學生開通。
    """
    nodes = {
        row.id: row
        for row in session.exec(
            select(TeachingClassMachineNode).where(
                TeachingClassMachineNode.class_id == class_id
            )
        ).all()
    }
    network_by_key = {node.node_key: node.network for node in nodes.values()}
    teaching_class = session.get(TeachingClass, class_id)
    version_id = teaching_class.course_version_id if teaching_class else None
    edges = (
        list(
            session.exec(
                select(CourseEnvironmentEdge).where(
                    CourseEnvironmentEdge.version_id == version_id
                )
            ).all()
        )
        if version_id
        else []
    )
    peer_policy = peer_policy_for_version(session, version_id)
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
        vmid_by_key = {
            nodes[row.machine_node_id].node_key: row.vmid
            for row in machines
            if row.machine_node_id in nodes and row.vmid is not None
        }
        scope_vmids.update(row.vmid for row in machines if row.vmid is not None)
        student = session.get(User, enrollment.user_id)
        if student is not None:
            errors.extend(
                course_publication_service.apply_for_machines(
                    session,
                    version_id=teaching_class.course_version_id,
                    vmid_by_key=vmid_by_key,
                    owner=student,
                    scope=f"{teaching_class.code[:12]}-{teaching_class.id.hex[:6]}",
                )
            )
        for source_vmid, target_vmid, protocol, port in topology_directions(
            peer_policy=peer_policy,
            edges=edges,
            vmid_by_key=vmid_by_key,
            network_by_key=network_by_key,
        ):
            plan(source_vmid, target_vmid, protocol, port)

    errors.extend(
        sync_scope_rules(
            comment_prefix=COMMENT_PREFIX,
            scope_vmids=scope_vmids,
            planned=planned,
        )
    )
    return errors
