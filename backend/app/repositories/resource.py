import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.models import Resource


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
    service_template_slug: str | None = None,
    commit: bool = True,
) -> Resource:
    db_resource = Resource(
        vmid=vmid,
        user_id=user_id,
        environment_type=environment_type,
        os_info=os_info,
        expiry_date=expiry_date,
        template_id=template_id,
        ssh_private_key_encrypted=ssh_private_key_encrypted,
        ssh_public_key=ssh_public_key,
        service_template_slug=service_template_slug,
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
    """更新 VM 的快取 IP 位址（不存在則忽略）"""
    from datetime import datetime, timezone

    resource = get_resource_by_vmid(session=session, vmid=vmid)
    if resource and resource.ip_address != ip_address:
        resource.ip_address = ip_address
        resource.ip_address_cached_at = datetime.now(timezone.utc)
        session.add(resource)
        session.flush()


def is_ip_address_fresh(*, session: Session, vmid: int, ttl_seconds: int = 3600) -> bool:
    """檢查快取的 IP 位址是否仍在有效期內"""
    from datetime import datetime, timezone

    resource = get_resource_by_vmid(session=session, vmid=vmid)
    if not resource or not resource.ip_address or not resource.ip_address_cached_at:
        return False
    age = (datetime.now(timezone.utc) - resource.ip_address_cached_at).total_seconds()
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
    """Update or clear the auto-stop schedule for a resource.

    Pass ``auto_stop_at=None`` to clear the schedule (e.g. when the student
    manually stops the VM).
    """
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
    """Resources whose ``auto_stop_at`` has elapsed; the scheduler shuts these
    down on the next tick."""
    stmt = select(Resource).where(
        Resource.auto_stop_at.isnot(None),  # type: ignore[union-attr]
        Resource.auto_stop_at <= now,
    )
    return list(session.exec(stmt).all())
