import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.models import Resource, ResourceNetwork


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
    batch_job_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    commit: bool = True,
) -> Resource:
    db_resource = Resource(
        vmid=vmid,
        request_id=request_id,
        user_id=user_id,
        environment_type=environment_type,
        os_info=os_info,
        expiry_date=expiry_date,
        template_id=template_id,
        ssh_private_key_encrypted=ssh_private_key_encrypted,
        ssh_public_key=ssh_public_key,
        service_template_slug=service_template_slug,
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
    if network and network.ip_address:
        return network.ip_address

    resource = get_resource_by_vmid(session=session, vmid=vmid)
    return getattr(resource, "ip_address", None) if resource else None


def is_ip_address_fresh(*, session: Session, vmid: int, ttl_seconds: int = 3600) -> bool:
    network = get_resource_network_by_vmid(session=session, vmid=vmid)
    cached_at = network.cached_at if network and network.ip_address else None
    if cached_at is None:
        resource = get_resource_by_vmid(session=session, vmid=vmid)
        cached_at = getattr(resource, "ip_address_cached_at", None) if resource else None
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
