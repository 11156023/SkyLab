"""Deletion request REST endpoints."""

import uuid

from fastapi import APIRouter

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.infrastructure.worker import submit_sync
from app.models.deletion_request import DeletionRequestStatus
from app.schemas.deletion_request import (
    DeletionRequestPublic,
    DeletionRequestsPublic,
)
from app.services.resource import deletion_service

router = APIRouter(prefix="/deletion-requests", tags=["deletion-requests"])


@router.get("/my", response_model=DeletionRequestsPublic)
def list_my_deletion_requests(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
):
    rows, total = deletion_service.list_for_user(
        session=session, user_id=current_user.id, skip=skip, limit=limit
    )
    return DeletionRequestsPublic(
        data=[
            DeletionRequestPublic(
                **deletion_service.to_public_with_user(session=session, req=r)
            )
            for r in rows
        ],
        count=total,
    )


@router.get("/", response_model=DeletionRequestsPublic)
def list_all_deletion_requests(
    session: SessionDep,
    _admin: AdminUser,
    status: DeletionRequestStatus | None = None,
    skip: int = 0,
    limit: int = 100,
):
    rows, total = deletion_service.list_all(
        session=session, status=status, skip=skip, limit=limit
    )
    return DeletionRequestsPublic(
        data=[
            DeletionRequestPublic(
                **deletion_service.to_public_with_user(session=session, req=r)
            )
            for r in rows
        ],
        count=total,
    )


@router.post("/{request_id}/cancel", response_model=DeletionRequestPublic)
def cancel_deletion_request(
    request_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    req = deletion_service.cancel_deletion_request(
        session=session,
        request_id=request_id,
        user_id=current_user.id,
        is_admin=current_user.is_superuser,
    )
    return DeletionRequestPublic(
        **deletion_service.to_public_with_user(session=session, req=req)
    )


@router.post("/{request_id}/retry", response_model=DeletionRequestPublic)
def retry_deletion_request(
    request_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    """Re-queue a failed deletion request and immediately fire the background task."""
    req = deletion_service.retry_failed_request(
        session=session,
        request_id=request_id,
        user_id=current_user.id,
        is_admin=current_user.is_superuser,
    )
    submit_sync(
        deletion_service.process_one_request,
        req.id,
        name=f"delete_resource:{req.vmid}",
        task_id=str(req.id),
        max_retries=0,
    )
    return DeletionRequestPublic(
        **deletion_service.to_public_with_user(session=session, req=req)
    )
