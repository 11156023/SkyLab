"""治理設定與告警事件的 DB 存取。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.exceptions import NotFoundError
from app.models import AlertEvent, GovernanceConfig

GOVERNANCE_CONFIG_ID = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_governance_config(*, session: Session) -> GovernanceConfig:
    """取得治理設定 singleton；不存在則以預設值建立。"""
    config = session.get(GovernanceConfig, GOVERNANCE_CONFIG_ID)
    if config is None:
        config = GovernanceConfig(id=GOVERNANCE_CONFIG_ID)
        session.add(config)
        session.commit()
        session.refresh(config)
    return config


def update_governance_config(
    *, session: Session, data: dict[str, Any]
) -> GovernanceConfig:
    config = get_governance_config(session=session)
    for key, value in data.items():
        if value is not None and hasattr(config, key):
            setattr(config, key, value)
    config.updated_at = _utc_now()
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def get_open_alerts(*, session: Session) -> list[AlertEvent]:
    stmt = select(AlertEvent).where(AlertEvent.resolved_at.is_(None))  # type: ignore[union-attr]
    return list(session.exec(stmt).all())


def get_latest_alerts_by_key(*, session: Session) -> list[AlertEvent]:
    """回傳所有告警（供冷卻期判斷用最近事件）。量大時以 limit 控制。"""
    stmt = (
        select(AlertEvent)
        .order_by(AlertEvent.created_at.desc())  # type: ignore[attr-defined]
        .limit(1000)
    )
    return list(session.exec(stmt).all())


def list_alerts(
    *, session: Session, active_only: bool = False, limit: int = 200
) -> list[AlertEvent]:
    stmt = select(AlertEvent)
    if active_only:
        stmt = stmt.where(AlertEvent.resolved_at.is_(None))  # type: ignore[union-attr]
    stmt = stmt.order_by(AlertEvent.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
    return list(session.exec(stmt).all())


def acknowledge_alert(
    *, session: Session, alert_id: uuid.UUID, user_id: uuid.UUID
) -> AlertEvent:
    alert = session.get(AlertEvent, alert_id)
    if alert is None:
        raise NotFoundError(f"Alert {alert_id} not found")
    if alert.acknowledged_at is None:
        alert.acknowledged_by = user_id
        alert.acknowledged_at = _utc_now()
        session.add(alert)
        session.commit()
        session.refresh(alert)
    return alert
