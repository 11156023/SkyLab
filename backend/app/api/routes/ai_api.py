import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query

from app.api.deps import AIAPIReviewerUser, AIAPIViewAllUser, CurrentUser, SessionDep
from app.models import AIAPIRequestStatus
from app.schemas import (
    AIAPICredentialPublic,
    AIAPICredentialsAdminPublic,
    AIAPICredentialsPublic,
    AIAPICredentialUpdate,
    AIAPIRequestCreate,
    AIAPIRequestPublic,
    AIAPIRequestReview,
    AIAPIRequestsPublic,
    Message,
    UsageStatsResponse,
)
from app.services.llm_gateway import ai_gateway_service

router = APIRouter(prefix="/ai-api", tags=["ai-api"])


@router.post("/requests", response_model=AIAPIRequestPublic)
def create_ai_api_request(
    request_in: AIAPIRequestCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> Any:
    return ai_gateway_service.create_request(
        session=session, request_in=request_in, user=current_user
    )


@router.get("/requests/my", response_model=AIAPIRequestsPublic)
def list_my_ai_api_requests(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
) -> Any:
    return ai_gateway_service.list_requests_by_user(
        session=session, user_id=current_user.id, skip=skip, limit=limit
    )


@router.get("/requests", response_model=AIAPIRequestsPublic)
def list_all_ai_api_requests(
    session: SessionDep,
    _current_user: AIAPIReviewerUser,
    status: AIAPIRequestStatus | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
) -> Any:
    return ai_gateway_service.list_all_requests(
        session=session, status=status, skip=skip, limit=limit
    )


@router.get("/requests/{request_id}", response_model=AIAPIRequestPublic)
def get_ai_api_request(
    request_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Any:
    return ai_gateway_service.get_request(
        session=session, request_id=request_id, current_user=current_user
    )


@router.post("/requests/{request_id}/review", response_model=AIAPIRequestPublic)
def review_ai_api_request(
    request_id: uuid.UUID,
    review: AIAPIRequestReview,
    session: SessionDep,
    current_user: AIAPIReviewerUser,
) -> Any:
    return ai_gateway_service.review_request(
        session=session,
        request_id=request_id,
        review_data=review,
        reviewer=current_user,
    )


@router.get("/credentials/my", response_model=AIAPICredentialsPublic)
def list_my_ai_api_credentials(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
) -> Any:
    return ai_gateway_service.list_credentials_by_user(
        session=session, user_id=current_user.id, skip=skip, limit=limit
    )


@router.get("/usage/proxy/my", response_model=UsageStatsResponse)
def get_my_proxy_usage(
    session: SessionDep,
    current_user: CurrentUser,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> Any:
    if not end_date:
        end_date = datetime.utcnow()
    if not start_date:
        start_date = end_date - timedelta(days=30)

    return ai_gateway_service.get_user_usage_stats(
        session=session,
        user_id=current_user.id,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/credentials", response_model=AIAPICredentialsAdminPublic)
def list_all_ai_api_credentials(
    session: SessionDep,
    _current_user: AIAPIViewAllUser,
    status: Literal["active", "inactive"] | None = None,
    user_email: str | None = Query(default=None, max_length=255),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
) -> Any:
    return ai_gateway_service.list_all_credentials(
        session=session,
        status=status,
        user_email=user_email,
        skip=skip,
        limit=limit,
    )


@router.post("/credentials/{credential_id}/rotate", response_model=AIAPICredentialPublic)
def rotate_my_ai_api_credential(
    credential_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Any:
    return ai_gateway_service.rotate_credential(
        session=session, credential_id=credential_id, current_user=current_user
    )

@router.delete("/credentials/{credential_id}", response_model=Message)
def delete_my_ai_api_credential(
    credential_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Any:
    return ai_gateway_service.delete_credential(
        session=session, credential_id=credential_id, current_user=current_user
    )


@router.patch("/credentials/{credential_id}", response_model=AIAPICredentialPublic)
def update_my_ai_api_credential(
    credential_id: uuid.UUID,
    update_data: AIAPICredentialUpdate,
    session: SessionDep,
    current_user: CurrentUser,
) -> Any:
    return ai_gateway_service.update_credential_name(
        session=session,
        credential_id=credential_id,
        name=update_data.api_key_name,
        current_user=current_user,
    )
