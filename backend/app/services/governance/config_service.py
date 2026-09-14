"""治理設定更新的協調層：合併 partial 更新後檢查跨欄位規則。

單欄位範圍由 ``GovernanceConfigUpdate`` 的 Field 約束負責；
這裡只放需要同時看多個欄位（含 DB 現值）的規則。
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.core.i18n import t
from app.exceptions import BadRequestError
from app.models import GovernanceConfig
from app.repositories import governance as governance_repo


def validate_idle_timing(*, notify_after_hours: int, grace_hours: int) -> None:
    """閒置通知必須早於自動關機，否則擁有者收到信時機器已被關掉。"""
    if notify_after_hours >= grace_hours:
        raise BadRequestError(
            t(
                "governance.idle_notify_after_must_precede_grace",
                notify_after=notify_after_hours,
                grace=grace_hours,
            )
        )


def update_config(*, session: Session, data: dict[str, Any]) -> GovernanceConfig:
    """partial 更新：未送的欄位以 DB 現值補齊後再做跨欄位檢查。"""
    current = governance_repo.get_governance_config(session=session)
    incoming = {key: value for key, value in data.items() if value is not None}
    validate_idle_timing(
        notify_after_hours=incoming.get(
            "idle_notify_after_hours", current.idle_notify_after_hours
        ),
        grace_hours=incoming.get("idle_grace_hours", current.idle_grace_hours),
    )
    return governance_repo.update_governance_config(session=session, data=data)
