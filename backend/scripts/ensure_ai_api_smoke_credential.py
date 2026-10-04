"""Create or recover the dedicated public AI API deployment smoke credential."""

from __future__ import annotations

import sys

from sqlalchemy import or_
from sqlmodel import Session, col, select

from app.core.db import engine
from app.models import (
    AIAPICredential,
    AIAPIRequest,
    AIAPIRequestStatus,
    User,
    UserRole,
    get_datetime_utc,
)
from app.schemas import AIAPIRequestCreate, AIAPIRequestReview
from app.services.llm_gateway import ai_gateway_service

SMOKE_KEY_NAME = "pve-deploy-smoke"
SMOKE_KEY_PURPOSE = "Automated post-deployment public AI model smoke test."
SMOKE_KEY_RATE_LIMIT = 100


def _active_admin(session: Session) -> User:
    owner = session.exec(
        select(User)
        .where(User.role == UserRole.admin)
        .where(col(User.is_active).is_(True))
        .order_by(col(User.created_at), col(User.id))
    ).first()
    if owner is None:
        raise RuntimeError("an active administrator is required for the smoke credential")
    return owner


def _active_smoke_credential(
    session: Session, *, owner: User
) -> AIAPICredential | None:
    now = get_datetime_utc()
    return session.exec(
        select(AIAPICredential)
        .join(
            AIAPIRequest,
            col(AIAPIRequest.id) == col(AIAPICredential.request_id),
        )
        .where(AIAPICredential.user_id == owner.id)
        .where(AIAPICredential.api_key_name == SMOKE_KEY_NAME)
        .where(AIAPIRequest.purpose == SMOKE_KEY_PURPOSE)
        .where(col(AIAPICredential.revoked_at).is_(None))
        .where(
            or_(
                col(AIAPICredential.expires_at).is_(None),
                col(AIAPICredential.expires_at) > now,
            )
        )
        .order_by(col(AIAPICredential.created_at).desc())
    ).first()


def _pending_smoke_request(session: Session, *, owner: User) -> AIAPIRequest | None:
    return session.exec(
        select(AIAPIRequest)
        .where(AIAPIRequest.user_id == owner.id)
        .where(AIAPIRequest.api_key_name == SMOKE_KEY_NAME)
        .where(AIAPIRequest.purpose == SMOKE_KEY_PURPOSE)
        .where(AIAPIRequest.status == AIAPIRequestStatus.pending)
        .order_by(col(AIAPIRequest.created_at).desc())
    ).first()


def ensure_ai_api_smoke_credential(
    session: Session, *, owner: User | None = None
) -> str:
    owner = owner or _active_admin(session)
    if not owner.is_active or owner.role != UserRole.admin:
        raise RuntimeError("the smoke credential owner must be an active administrator")

    credential = _active_smoke_credential(session, owner=owner)
    if credential is not None:
        if (credential.rate_limit or 0) < SMOKE_KEY_RATE_LIMIT:
            raise RuntimeError("the existing smoke credential has an unexpected rate limit")
        detail = ai_gateway_service.get_credential(
            session=session,
            credential_id=credential.id,
            current_user=owner,
        )
        if detail.api_key is None or not detail.api_key.startswith("ccai_"):
            raise RuntimeError("the existing smoke credential cannot be recovered")
        return detail.api_key

    request = _pending_smoke_request(session, owner=owner)
    if request is None:
        created = ai_gateway_service.create_request(
            session=session,
            request_in=AIAPIRequestCreate(
                purpose=SMOKE_KEY_PURPOSE,
                api_key_name=SMOKE_KEY_NAME,
                duration="never",
            ),
            user=owner,
        )
        request = session.get(AIAPIRequest, created.id)
        if request is None:
            raise RuntimeError("the smoke credential request was not saved")

    request.rate_limit = SMOKE_KEY_RATE_LIMIT
    session.add(request)
    session.commit()
    ai_gateway_service.review_request(
        session=session,
        request_id=request.id,
        review_data=AIAPIRequestReview(status=AIAPIRequestStatus.approved),
        reviewer=owner,
    )

    credential = _active_smoke_credential(session, owner=owner)
    if credential is None:
        raise RuntimeError("the approved smoke credential was not created")
    detail = ai_gateway_service.get_credential(
        session=session,
        credential_id=credential.id,
        current_user=owner,
    )
    if detail.api_key is None or not detail.api_key.startswith("ccai_"):
        raise RuntimeError("the new smoke credential cannot be recovered")
    return detail.api_key


def main() -> int:
    with Session(engine) as session:
        api_key = ensure_ai_api_smoke_credential(session)
    sys.stdout.write(f"{api_key}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
