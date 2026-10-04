"""Create or clean up the dedicated public AI API deployment smoke credential."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from sqlmodel import Session, col, select

from app.core.db import engine
from app.models import (
    AIAPICredential,
    AIAPIRequest,
    AIAPIRequestStatus,
    User,
    UserRole,
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
        raise RuntimeError(
            "an active administrator is required for the smoke credential"
        )
    return owner


def cleanup_ai_api_smoke_credentials(session: Session) -> None:
    """Invalidate only deployment smoke keys, including leftovers from older runs."""
    credentials = session.exec(
        select(AIAPICredential)
        .join(
            AIAPIRequest,
            col(AIAPIRequest.id) == col(AIAPICredential.request_id),
        )
        .where(AIAPIRequest.api_key_name == SMOKE_KEY_NAME)
        .where(AIAPIRequest.purpose == SMOKE_KEY_PURPOSE)
        .where(col(AIAPICredential.revoked_at).is_(None))
    ).all()
    for credential in credentials:
        owner = session.get(User, credential.user_id)
        if owner is None:
            raise RuntimeError("the smoke credential owner was not found")
        ai_gateway_service.delete_credential(
            session=session,
            credential_id=credential.id,
            current_user=owner,
        )


def ensure_ai_api_smoke_credential(
    session: Session, *, owner: User | None = None
) -> str:
    cleanup_ai_api_smoke_credentials(session)
    owner = owner or _active_admin(session)
    if not owner.is_active or owner.role != UserRole.admin:
        raise RuntimeError("the smoke credential owner must be an active administrator")

    created = ai_gateway_service.create_request(
        session=session,
        request_in=AIAPIRequestCreate(
            purpose=SMOKE_KEY_PURPOSE,
            api_key_name=SMOKE_KEY_NAME,
            duration="1d",
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

    credential = session.exec(
        select(AIAPICredential).where(AIAPICredential.request_id == request.id)
    ).one_or_none()
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
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--cleanup", action="store_true", help="Delete or revoke deployment smoke keys."
    )
    action.add_argument(
        "--output", type=Path, help="Write the key to a new owner-only file, never stdout."
    )
    args = parser.parse_args()
    if args.cleanup:
        with Session(engine) as session:
            cleanup_ai_api_smoke_credentials(session)
    else:
        # Refuse existing files and symlinks; set permissions before writing any secret.
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                with Session(engine) as session:
                    api_key = ensure_ai_api_smoke_credential(session)
                output.write(f"{api_key}\n")
        except BaseException:
            args.output.unlink(missing_ok=True)
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
