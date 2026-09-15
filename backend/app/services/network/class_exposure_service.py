"""老師把機器開放給班級：學生發起「自己的機器 → 老師的機器」連線的授權依據。

連線會同時在兩台機器寫規則（來源出站 ACCEPT、目標入站 ACCEPT），所以建立
連線原本要求兩端都有管理權。學生對老師的機器沒有管理權，這裡把授權拆成
非對稱的兩半：

1. 老師在自己的機器上建立 ResourceClassExposure（班級 + 允許的埠），
   這是老師事先給的同意。
2. 學生建立連線時，來源仍要有管理權（自己的機器）；目標只要「開放給我
   所屬的班級」且埠在允許範圍內、方向單向，就放行——後端替學生在老師
   機器上寫入站規則，憑的是第 1 步的同意。
3. 老師縮小埠清單或關閉開放時，一併清掉學生連過來的規則。

只在這裡查 exposure 表；firewall_service 只負責寫規則，路由只做編排。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, col, select

from app.core.authorizers import (
    can_bypass_resource_ownership,
    require_teaching_access,
)
from app.core.i18n import t
from app.exceptions import BadRequestError, NotFoundError, PermissionDeniedError
from app.models import (
    Resource,
    TeachingClass,
    TeachingClassStatus,
    TeachingClassStudent,
    User,
)
from app.models.class_exposure import ResourceClassExposure
from app.repositories import class_exposure as exposure_repo
from app.schemas.firewall import ClassExposurePublic, PortSpec
from app.services.resource.access import require_resource_management

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PeerTarget:
    """學生拓撲上「可連線、不可管理」的老師機器。"""

    resource: Resource
    allowed_ports: list[PortSpec]
    class_names: list[str]
    owner_name: str | None


# ─── 埠比對（純函式） ──────────────────────────────────────────────────────────


def port_specs(raw: list[dict[str, Any]] | None) -> list[PortSpec]:
    return [PortSpec(port=int(p["port"]), protocol=str(p["protocol"])) for p in raw or []]


def normalize_ports(ports: list[PortSpec]) -> list[dict[str, Any]]:
    """去重、排序，存進 JSON 欄位；只保留 port/protocol，其他入站欄位無意義。"""
    seen: set[tuple[int, str]] = set()
    out: list[dict[str, Any]] = []
    for p in sorted(ports, key=lambda x: (x.protocol, x.port)):
        key = (p.port, p.protocol)
        if key in seen:
            continue
        seen.add(key)
        out.append({"port": p.port, "protocol": p.protocol})
    return out


def merge_ports(*groups: list[PortSpec]) -> list[PortSpec]:
    return port_specs(normalize_ports([p for g in groups for p in g]))


def port_allowed(port: PortSpec, allowed: list[PortSpec]) -> bool:
    return any(a.port == port.port and a.protocol == port.protocol for a in allowed)


def disallowed_ports(ports: list[PortSpec], allowed: list[PortSpec]) -> list[PortSpec]:
    return [p for p in ports if not port_allowed(p, allowed)]


def _fmt_ports(ports: list[PortSpec]) -> str:
    return ", ".join(f"{p.port}/{p.protocol}" for p in ports)


# ─── 老師端：開放設定 ──────────────────────────────────────────────────────────


def _to_public(
    exposure: ResourceClassExposure, class_name: str | None
) -> ClassExposurePublic:
    return ClassExposurePublic(
        id=exposure.id,
        vmid=exposure.resource_vmid,
        class_id=exposure.class_id,
        class_name=class_name,
        ports=port_specs(exposure.ports),
        created_at=exposure.created_at,
    )


def _class_names(session: Session, class_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not class_ids:
        return {}
    stmt = select(TeachingClass.id, TeachingClass.name).where(
        col(TeachingClass.id).in_(list(class_ids))
    )
    return dict(session.exec(stmt).all())


def list_exposures(*, session: Session, vmid: int) -> list[ClassExposurePublic]:
    rows = exposure_repo.list_for_resource(session=session, vmid=vmid)
    names = _class_names(session, {r.class_id for r in rows})
    return [_to_public(r, names.get(r.class_id)) for r in rows]


def _require_own_class(session: Session, user: User, class_id: uuid.UUID) -> TeachingClass:
    """只能開放給自己擔任老師的班級（admin 不限）。"""
    teaching_class = session.get(TeachingClass, class_id)
    if teaching_class is None:
        raise NotFoundError(t("firewall.exposure_class_not_found"))
    require_teaching_access(
        user,
        teaching_class.owner_id,
        detail=t("firewall.exposure_not_class_owner"),
    )
    return teaching_class


def create_exposure(
    *,
    session: Session,
    user: User,
    vmid: int,
    class_id: uuid.UUID,
    ports: list[PortSpec],
) -> ClassExposurePublic:
    require_resource_management(session=session, user=user, vmid=vmid)
    teaching_class = _require_own_class(session, user, class_id)
    normalized = normalize_ports(ports)
    if not normalized:
        raise BadRequestError(t("firewall.exposure_ports_required"))
    if exposure_repo.get_for_resource_class(session=session, vmid=vmid, class_id=class_id):
        raise BadRequestError(t("firewall.exposure_exists"))
    exposure = exposure_repo.create(
        session=session,
        vmid=vmid,
        class_id=class_id,
        ports=normalized,
        created_by=user.id,
    )
    return _to_public(exposure, teaching_class.name)


def _get_exposure_for_manager(
    session: Session, user: User, vmid: int, exposure_id: uuid.UUID
) -> ResourceClassExposure:
    require_resource_management(session=session, user=user, vmid=vmid)
    exposure = exposure_repo.get(session=session, exposure_id=exposure_id)
    if exposure is None or exposure.resource_vmid != vmid:
        raise NotFoundError(t("firewall.exposure_not_found"))
    return exposure


def update_exposure(
    *,
    session: Session,
    user: User,
    vmid: int,
    exposure_id: uuid.UUID,
    ports: list[PortSpec],
) -> ClassExposurePublic:
    """改埠清單；被拿掉的埠，學生已連上的規則一併清掉。"""
    exposure = _get_exposure_for_manager(session, user, vmid, exposure_id)
    normalized = normalize_ports(ports)
    if not normalized:
        raise BadRequestError(t("firewall.exposure_ports_required"))
    keep = port_specs(normalized)
    prune_student_connections(
        session=session, vmid=vmid, class_id=exposure.class_id, keep_ports=keep
    )
    exposure = exposure_repo.update_ports(session=session, exposure=exposure, ports=normalized)
    names = _class_names(session, {exposure.class_id})
    return _to_public(exposure, names.get(exposure.class_id))


def delete_exposure(
    *, session: Session, user: User, vmid: int, exposure_id: uuid.UUID
) -> int:
    """關閉開放，回傳清掉的學生連線數。"""
    exposure = _get_exposure_for_manager(session, user, vmid, exposure_id)
    removed = prune_student_connections(
        session=session, vmid=vmid, class_id=exposure.class_id, keep_ports=[]
    )
    exposure_repo.delete(session=session, exposure=exposure)
    return removed


def _student_vmids_of_class(session: Session, class_id: uuid.UUID) -> set[int]:
    """班上每位學生名下所有機器（個人機與課堂機都算，學生從哪台連過來都可能）。"""
    student_ids = set(
        session.exec(
            select(TeachingClassStudent.user_id).where(
                TeachingClassStudent.class_id == class_id
            )
        ).all()
    )
    if not student_ids:
        return set()
    stmt = select(Resource.vmid).where(col(Resource.user_id).in_(list(student_ids)))
    return set(session.exec(stmt).all())


def prune_student_connections(
    *,
    session: Session,
    vmid: int,
    class_id: uuid.UUID,
    keep_ports: list[PortSpec],
) -> int:
    """把班上學生連進 vmid、且埠不在 keep_ports 內的連線拆掉。回傳拆掉的連線數。"""
    from app.services.network import firewall_service  # noqa: PLC0415  循環匯入

    student_vmids = _student_vmids_of_class(session, class_id)
    if not student_vmids:
        return 0
    edges = firewall_service.get_connections_from_rules([vmid])
    removed = 0
    for edge in edges:
        if edge.target_vmid != vmid or edge.source_vmid not in student_vmids:
            continue
        stale = disallowed_ports(edge.ports, keep_ports)
        if not stale:
            continue
        try:
            firewall_service.delete_connection(
                source_vmid=edge.source_vmid,
                target_vmid=vmid,
                ports=stale,
                session=session,
            )
            removed += 1
        except Exception:  # noqa: BLE001  一條拆不掉不該擋住其他的
            logger.exception(
                "關閉班級開放時拆學生連線失敗 src=%s dst=%s ports=%s",
                edge.source_vmid, vmid, _fmt_ports(stale),
            )
    return removed


# ─── 學生端：可連線的老師機器 ──────────────────────────────────────────────────


def _active_class_ids_of_student(session: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    """學生目前在籍、且班級進行中的班級。退選或結班後開放自然失效。"""
    stmt = (
        select(TeachingClassStudent.class_id)
        .join(TeachingClass, col(TeachingClass.id) == col(TeachingClassStudent.class_id))
        .where(
            TeachingClassStudent.user_id == user_id,
            TeachingClassStudent.status == "active",
            TeachingClass.status == TeachingClassStatus.active,
        )
    )
    return set(session.exec(stmt).all())


def _exposures_for_student(
    session: Session, user: User
) -> dict[int, tuple[list[PortSpec], set[uuid.UUID]]]:
    """vmid → (允許的埠聯集, 經由哪些班級)。同一台機器開放給多個我在的班級時取聯集。"""
    class_ids = _active_class_ids_of_student(session, user.id)
    rows = exposure_repo.list_for_classes(session=session, class_ids=class_ids)
    out: dict[int, tuple[list[PortSpec], set[uuid.UUID]]] = {}
    for row in rows:
        ports, classes = out.get(row.resource_vmid, ([], set()))
        out[row.resource_vmid] = (merge_ports(ports, port_specs(row.ports)), classes | {row.class_id})
    return out


def list_peer_targets(
    *, session: Session, user: User, exclude_vmids: set[int]
) -> list[PeerTarget]:
    """學生拓撲上要多出來的老師機器；自己本來就看得到的機器不重複列。"""
    if can_bypass_resource_ownership(user):
        return []
    exposures = _exposures_for_student(session, user)
    vmids = [v for v in exposures if v not in exclude_vmids]
    if not vmids:
        return []
    resources = {
        r.vmid: r
        for r in session.exec(select(Resource).where(col(Resource.vmid).in_(vmids))).all()
    }
    class_names = _class_names(session, {c for _, cs in exposures.values() for c in cs})
    owner_ids = {r.user_id for r in resources.values()}
    owner_names: dict[uuid.UUID, str] = {}
    if owner_ids:
        stmt = select(User.id, User.full_name, User.email).where(
            col(User.id).in_(list(owner_ids))
        )
        owner_names = {
            uid: (full_name or email) for uid, full_name, email in session.exec(stmt).all()
        }
    targets: list[PeerTarget] = []
    for vmid in vmids:
        resource = resources.get(vmid)
        if resource is None:
            continue
        ports, classes = exposures[vmid]
        targets.append(
            PeerTarget(
                resource=resource,
                allowed_ports=ports,
                class_names=sorted(class_names[c] for c in classes if c in class_names),
                owner_name=owner_names.get(resource.user_id),
            )
        )
    return targets


def allowed_peer_ports(
    *, session: Session, user: User, target_vmid: int
) -> list[PortSpec] | None:
    """目標機器開放給我的埠；沒開放回 None。"""
    entry = _exposures_for_student(session, user).get(target_vmid)
    return entry[0] if entry else None


def require_peer_connection(
    *,
    session: Session,
    user: User,
    target_vmid: int,
    ports: list[PortSpec],
    direction: str,
) -> None:
    """學生連到老師機器的放行條件：有開放、埠都在清單內、單向。"""
    allowed = allowed_peer_ports(session=session, user=user, target_vmid=target_vmid)
    if allowed is None:
        raise PermissionDeniedError(t("firewall.peer_not_allowed"))
    if direction != "one_way":
        raise BadRequestError(t("firewall.peer_one_way_only"))
    stale = disallowed_ports(ports, allowed)
    if stale:
        raise BadRequestError(
            t("firewall.peer_port_not_allowed", ports=_fmt_ports(stale))
        )


def require_connection_target(
    *,
    session: Session,
    user: User,
    target_vmid: int,
    ports: list[PortSpec],
    direction: str,
) -> None:
    """連線目標：有管理權直接過；沒有就看是不是開放給我班級的老師機器。"""
    try:
        require_resource_management(session=session, user=user, vmid=target_vmid)
    except PermissionDeniedError:
        require_peer_connection(
            session=session,
            user=user,
            target_vmid=target_vmid,
            ports=ports,
            direction=direction,
        )


def _manages(session: Session, user: User, vmid: int) -> bool:
    try:
        require_resource_management(session=session, user=user, vmid=vmid)
        return True
    except PermissionDeniedError:
        return False


def require_connection_delete(
    *, session: Session, user: User, source_vmid: int, target_vmid: int
) -> None:
    """拆連線：管理任一端就可以。

    拆掉的是兩邊的 ACCEPT，結果只會更嚴；老師要能拆學生連進來的線，
    學生也要能拆自己連到老師機器的線，兩種情況都只管得到其中一端。
    """
    if _manages(session, user, source_vmid) or _manages(session, user, target_vmid):
        return
    raise PermissionDeniedError(t("firewall.connection_delete_denied"))


__all__ = [
    "PeerTarget",
    "allowed_peer_ports",
    "create_exposure",
    "delete_exposure",
    "disallowed_ports",
    "list_exposures",
    "list_peer_targets",
    "merge_ports",
    "normalize_ports",
    "port_allowed",
    "port_specs",
    "prune_student_connections",
    "require_connection_delete",
    "require_connection_target",
    "require_peer_connection",
    "update_exposure",
]
