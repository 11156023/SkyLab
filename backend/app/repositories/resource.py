import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.models import Resource, ResourceNetwork

logger = logging.getLogger(__name__)


def create_resource(
    *,
    session: Session,
    vmid: int,
    user_id: uuid.UUID,
    environment_type: str,
    os_info: str | None = None,
    expiry_date: date | None = None,
    template_id: int | None = None,
    ssh_private_key_encrypted: str | None = None,
    ssh_public_key: str | None = None,
    login_password_encrypted: str | None = None,
    batch_job_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    teaching_class_id: uuid.UUID | None = None,
    allocation_scope: str = "personal",
    control_policy: str = "owner",
    commit: bool = True,
) -> Resource:
    db_resource = Resource(
        vmid=vmid,
        request_id=request_id,
        user_id=user_id,
        teaching_class_id=teaching_class_id,
        allocation_scope=allocation_scope,
        control_policy=control_policy,
        environment_type=environment_type,
        os_info=os_info,
        expiry_date=expiry_date,
        template_id=template_id,
        ssh_private_key_encrypted=ssh_private_key_encrypted,
        ssh_public_key=ssh_public_key,
        login_password_encrypted=login_password_encrypted,
        batch_job_id=batch_job_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(db_resource)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(db_resource)
    return db_resource


def get_resource_by_vmid(*, session: Session, vmid: int) -> Resource | None:
    return session.exec(select(Resource).where(Resource.vmid == vmid)).first()


def get_all_resources(*, session: Session) -> list[Resource]:
    return list(session.exec(select(Resource)).all())


def get_resources_by_user(*, session: Session, user_id: uuid.UUID) -> list[Resource]:
    return list(
        session.exec(select(Resource).where(Resource.user_id == user_id)).all()
    )


def get_resources_by_teaching_class(
    *, session: Session, teaching_class_id: uuid.UUID
) -> list[Resource]:
    return list(
        session.exec(
            select(Resource).where(
                Resource.teaching_class_id == teaching_class_id
            )
        ).all()
    )


def assign_to_teaching_class(
    *,
    session: Session,
    vmid: int,
    teaching_class_id: uuid.UUID,
    commit: bool = True,
) -> Resource | None:
    resource = get_resource_by_vmid(session=session, vmid=vmid)
    if resource is None:
        return None
    resource.teaching_class_id = teaching_class_id
    resource.allocation_scope = "teaching_class"
    resource.control_policy = "class_member"
    session.add(resource)
    if commit:
        session.commit()
        session.refresh(resource)
    else:
        session.flush()
    return resource


def update_resource(
    *, session: Session, db_resource: Resource, resource_update: dict[str, Any]
) -> Resource:
    for key, value in resource_update.items():
        setattr(db_resource, key, value)
    session.add(db_resource)
    session.commit()
    session.refresh(db_resource)
    return db_resource


def update_ip_address(*, session: Session, vmid: int, ip_address: str) -> None:
    """Update the resource IP cache in resource_networks."""
    resource = get_resource_by_vmid(session=session, vmid=vmid)
    if resource is None:
        return

    now = datetime.now(timezone.utc)
    network = session.exec(
        select(ResourceNetwork).where(ResourceNetwork.resource_vmid == vmid)
    ).first()
    if network is None:
        network = ResourceNetwork(
            resource_vmid=vmid,
            ip_address=ip_address,
            source="proxmox",
            cached_at=now,
            created_at=now,
            updated_at=now,
        )
    else:
        network.ip_address = ip_address
        network.source = "proxmox"
        network.cached_at = now
        network.updated_at = now
    session.add(network)
    session.flush()


def get_resource_network_by_vmid(
    *, session: Session, vmid: int
) -> ResourceNetwork | None:
    if not hasattr(session, "exec"):
        return None
    return session.exec(
        select(ResourceNetwork).where(ResourceNetwork.resource_vmid == vmid)
    ).first()


def get_cached_ip_address(*, session: Session, vmid: int) -> str | None:
    network = get_resource_network_by_vmid(session=session, vmid=vmid)
    return network.ip_address if network else None


def _rollback_quietly(session: Session) -> None:
    rollback = getattr(session, "rollback", None)
    if rollback is not None:
        rollback()


def sync_ip_cache(*, session: Session, vmid: int, live_ip: str | None) -> str | None:
    """有即時 IP 就順手寫回快取並回傳它；沒有就回退 DB 快取。

    快取讀寫是唯讀流程裡的順手動作，失敗一律不往外拋。但 flush／查詢一旦出錯
    （連線中斷、約束衝突），session 會停在無效交易，之後同一個 session 的任何查詢
    都會 PendingRollbackError，所以這裡必須 rollback 讓呼叫端能繼續用同一個 session。
    """
    if live_ip:
        try:
            update_ip_address(session=session, vmid=vmid, ip_address=live_ip)
        except Exception:
            _rollback_quietly(session)
            logger.warning(
                "VMID=%s IP 快取寫入失敗，已 rollback（ip=%s）",
                vmid,
                live_ip,
                exc_info=True,
            )
        return live_ip

    try:
        return get_cached_ip_address(session=session, vmid=vmid)
    except Exception:
        _rollback_quietly(session)
        logger.warning("VMID=%s IP 快取讀取失敗，已 rollback", vmid, exc_info=True)
        return None


def is_ip_address_fresh(*, session: Session, vmid: int, ttl_seconds: int = 3600) -> bool:
    network = get_resource_network_by_vmid(session=session, vmid=vmid)
    cached_at = network.cached_at if network and network.ip_address else None
    if cached_at is None:
        return False
    age = (datetime.now(timezone.utc) - cached_at).total_seconds()
    return age <= ttl_seconds


def delete_resource(*, session: Session, vmid: int, commit: bool = True) -> None:
    resource = get_resource_by_vmid(session=session, vmid=vmid)
    if resource:
        session.delete(resource)
        if commit:
            session.commit()
        else:
            session.flush()


def set_auto_stop(
    *,
    session: Session,
    vmid: int,
    auto_stop_at: datetime | None,
    auto_stop_reason: str | None,
    commit: bool = True,
) -> Resource | None:
    """Update or clear the auto-stop schedule for a resource."""
    resource = get_resource_by_vmid(session=session, vmid=vmid)
    if resource is None:
        return None
    resource.auto_stop_at = auto_stop_at
    resource.auto_stop_reason = auto_stop_reason
    session.add(resource)
    if commit:
        session.commit()
        session.refresh(resource)
    else:
        session.flush()
    return resource


def list_due_auto_stops(*, session: Session, now: datetime) -> list[Resource]:
    stmt = select(Resource).where(
        Resource.auto_stop_at.isnot(None),  # type: ignore[union-attr]
        Resource.auto_stop_at <= now,
    )
    return list(session.exec(stmt).all())


def list_resources_with_expiry(*, session: Session) -> list[Resource]:
    """Personal resources with expiry dates for generic TTL governance.

    Teaching-class resources use the class end time and reclaim workflow as
    their single lifecycle authority.
    """
    stmt = select(Resource).where(
        Resource.expiry_date.isnot(None),  # type: ignore[union-attr]
        Resource.allocation_scope == "personal",
    )
    return list(session.exec(stmt).all())


def list_idle_scan_candidates(
    *,
    session: Session,
    vmids: list[int],
    checked_before: datetime,
    limit: int,
) -> list[Resource]:
    """個人資源閒置掃描候選：running 集合中最久未檢查的前 N 台。

    ``idle_checked_at`` 為 NULL（從未檢查）優先，其次為早於
    ``checked_before`` 者，避免每個 tick 重複打同一批 RRD。
    """
    if not vmids:
        return []
    stmt = (
        select(Resource)
        .where(
            Resource.vmid.in_(vmids),  # type: ignore[attr-defined]
            Resource.allocation_scope == "personal",
            (
                Resource.idle_checked_at.is_(None)  # type: ignore[union-attr]
                | (Resource.idle_checked_at < checked_before)  # type: ignore[operator]
            ),
        )
        .order_by(
            Resource.idle_checked_at.asc().nulls_first()  # type: ignore[union-attr]
        )
        .limit(limit)
    )
    return list(session.exec(stmt).all())


def list_mining_scan_candidates(
    *,
    session: Session,
    vmids: list[int],
    checked_before: datetime,
    limit: int,
) -> list[Resource]:
    """挖礦掃描候選：running 集合中最久未檢查的前 N 台（同閒置掃描模式）。"""
    if not vmids:
        return []
    stmt = (
        select(Resource)
        .where(
            Resource.vmid.in_(vmids),  # type: ignore[attr-defined]
            (
                Resource.mining_checked_at.is_(None)  # type: ignore[union-attr]
                | (Resource.mining_checked_at < checked_before)  # type: ignore[operator]
            ),
        )
        .order_by(
            Resource.mining_checked_at.asc().nulls_first()  # type: ignore[union-attr]
        )
        .limit(limit)
    )
    return list(session.exec(stmt).all())
