"""Resource class exposure repository."""

import uuid
from collections.abc import Iterable
from typing import Any

from sqlmodel import Session, col, select

from app.models.class_exposure import ResourceClassExposure


def list_for_resource(*, session: Session, vmid: int) -> list[ResourceClassExposure]:
    statement = (
        select(ResourceClassExposure)
        .where(ResourceClassExposure.resource_vmid == vmid)
        .order_by(col(ResourceClassExposure.created_at))
    )
    return list(session.exec(statement).all())


def list_for_classes(
    *, session: Session, class_ids: Iterable[uuid.UUID]
) -> list[ResourceClassExposure]:
    ids = list(class_ids)
    if not ids:
        return []
    statement = select(ResourceClassExposure).where(
        col(ResourceClassExposure.class_id).in_(ids)
    )
    return list(session.exec(statement).all())


def get(
    *, session: Session, exposure_id: uuid.UUID
) -> ResourceClassExposure | None:
    return session.get(ResourceClassExposure, exposure_id)


def get_for_resource_class(
    *, session: Session, vmid: int, class_id: uuid.UUID
) -> ResourceClassExposure | None:
    statement = select(ResourceClassExposure).where(
        ResourceClassExposure.resource_vmid == vmid,
        ResourceClassExposure.class_id == class_id,
    )
    return session.exec(statement).first()


def create(
    *,
    session: Session,
    vmid: int,
    class_id: uuid.UUID,
    ports: list[dict[str, Any]],
    created_by: uuid.UUID | None,
) -> ResourceClassExposure:
    exposure = ResourceClassExposure(
        resource_vmid=vmid, class_id=class_id, ports=ports, created_by=created_by
    )
    session.add(exposure)
    session.commit()
    session.refresh(exposure)
    return exposure


def update_ports(
    *, session: Session, exposure: ResourceClassExposure, ports: list[dict[str, Any]]
) -> ResourceClassExposure:
    exposure.ports = ports
    session.add(exposure)
    session.commit()
    session.refresh(exposure)
    return exposure


def delete(*, session: Session, exposure: ResourceClassExposure) -> None:
    session.delete(exposure)
    session.commit()


__all__ = [
    "create",
    "delete",
    "get",
    "get_for_resource_class",
    "list_for_classes",
    "list_for_resource",
    "update_ports",
]
