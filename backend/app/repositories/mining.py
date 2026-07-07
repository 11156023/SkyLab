"""MiningIncident repository。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlmodel import Session, select

from app.exceptions import NotFoundError
from app.models import MiningIncident, MiningIncidentStatus

_OPEN_STATUSES = (MiningIncidentStatus.detected, MiningIncidentStatus.suspended)


def create_incident(
    *,
    session: Session,
    vmid: int,
    user_id: uuid.UUID,
    node: str,
    resource_type: str,
    avg_cpu: float,
    window_hours: int,
    now: datetime,
) -> MiningIncident:
    incident = MiningIncident(
        vmid=vmid,
        user_id=user_id,
        node=node,
        resource_type=resource_type,
        avg_cpu=avg_cpu,
        window_hours=window_hours,
        status=MiningIncidentStatus.detected,
        detected_at=now,
    )
    session.add(incident)
    session.flush()
    return incident


def get_incident(
    *, session: Session, incident_id: uuid.UUID
) -> MiningIncident:
    incident = session.get(MiningIncident, incident_id)
    if incident is None:
        raise NotFoundError("Mining incident not found")
    return incident


def has_open_incident(*, session: Session, vmid: int) -> bool:
    stmt = (
        select(MiningIncident.id)
        .where(
            MiningIncident.vmid == vmid,
            MiningIncident.status.in_(_OPEN_STATUSES),  # type: ignore[attr-defined]
        )
        .limit(1)
    )
    return session.exec(stmt).first() is not None


def list_incidents(
    *,
    session: Session,
    status: MiningIncidentStatus | None = None,
    limit: int = 200,
) -> list[MiningIncident]:
    stmt = select(MiningIncident)
    if status is not None:
        stmt = stmt.where(MiningIncident.status == status)
    stmt = stmt.order_by(
        MiningIncident.detected_at.desc()  # type: ignore[attr-defined]
    ).limit(limit)
    return list(session.exec(stmt).all())
