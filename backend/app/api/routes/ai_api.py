import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.concurrency import run_in_threadpool

from app.api.deps import (
    AIAPIReviewerUser,
    AIAPIViewAllUser,
    CurrentUser,
    SessionDep,
    rate_limit_by_user,
)
from app.api.request_body import limited_json_openapi, parse_limited_json
from app.core.config import settings
from app.models import AIAPIRequestStatus, UserRole
from app.schemas import (
    AIAPICredentialPublic,
    AIAPICredentialsAdminPublic,
    AIAPICredentialsPublic,
    AIAPICredentialUpdate,
    AIAPICredentialWithSecret,
    AIAPIRequestBulkReject,
    AIAPIRequestCreate,
    AIAPIRequestPublic,
    AIAPIRequestReview,
    AIAPIRequestsPublic,
    Message,
    UsageRecordsPublic,
    UsageStatsResponse,
)
from app.services.llm_gateway import ai_gateway_service

router = APIRouter(prefix="/ai-api", tags=["ai-api"])

_AI_API_REQUEST_RATE_LIMIT = Depends(
    rate_limit_by_user(
        scope="ai-api-request",
        limit=settings.AI_API_CONTROL_RATE_LIMIT_PER_USER,
        window_seconds=60,
    )
)
_AI_API_ROTATE_RATE_LIMIT = Depends(
    rate_limit_by_user(
        scope="ai-api-rotate",
        limit=settings.AI_API_CONTROL_RATE_LIMIT_PER_USER,
        window_seconds=60,
    )
)


@router.post(
    "/requests",
    response_model=AIAPIRequestPublic,
    dependencies=[_AI_API_REQUEST_RATE_LIMIT],
    openapi_extra=limited_json_openapi(AIAPIRequestCreate),
)
async def create_ai_api_request(
    request: Request,
    session: SessionDep,
    current_user: CurrentUser,
) -> Any:
    # 不宣告 Pydantic body parameter：先完成 auth／限流，再串流讀取有界 JSON。
    request_in = await parse_limited_json(request, AIAPIRequestCreate)
    return await run_in_threadpool(
        ai_gateway_service.create_request,
        session=session,
        request_in=request_in,
        user=current_user,
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


@router.post(
    "/requests/bulk-reject",
    response_model=AIAPIRequestsPublic,
    openapi_extra=limited_json_openapi(AIAPIRequestBulkReject),
)
async def bulk_reject_ai_api_requests(
    request: Request,
    session: SessionDep,
    current_user: AIAPIReviewerUser,
) -> Any:
    """以同一理由原子駁回多筆仍在待審核狀態的申請。"""
    review = await parse_limited_json(request, AIAPIRequestBulkReject)
    return await run_in_threadpool(
        ai_gateway_service.bulk_reject_requests,
        session=session,
        request_ids=review.request_ids,
        review_comment=review.review_comment,
        reviewer=current_user,
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


@router.post(
    "/requests/{request_id}/review",
    response_model=AIAPIRequestPublic,
    openapi_extra=limited_json_openapi(AIAPIRequestReview),
)
async def review_ai_api_request(
    request_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    current_user: AIAPIReviewerUser,
) -> Any:
    review = await parse_limited_json(request, AIAPIRequestReview)
    return await run_in_threadpool(
        ai_gateway_service.review_request,
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


@router.get("/usage/my", response_model=UsageStatsResponse)
def get_my_usage(
    session: SessionDep,
    current_user: CurrentUser,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    tz: str | None = Query(default=None, max_length=64),
) -> Any:
    """申請金鑰的 API 用量統計；不包含平台 Template 功能用量。"""
    start_date, end_date = ai_gateway_service.default_usage_window(
        start_date, end_date
    )
    return ai_gateway_service.get_user_usage_stats(
        session=session,
        user_id=current_user.id,
        start_date=start_date,
        end_date=end_date,
        tz=tz,
    )


@router.get("/usage/records/my", response_model=UsageRecordsPublic)
def get_my_usage_records(
    session: SessionDep,
    current_user: CurrentUser,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> Any:
    """申請金鑰的逐筆 API 呼叫紀錄（依時間新→舊排序）。"""
    start_date, end_date = ai_gateway_service.default_usage_window(
        start_date, end_date
    )
    return ai_gateway_service.list_user_usage_records(
        session=session,
        user_id=current_user.id,
        start_date=start_date,
        end_date=end_date,
        skip=skip,
        limit=limit,
    )


@router.get("/credentials", response_model=AIAPICredentialsAdminPublic)
def list_all_ai_api_credentials(
    session: SessionDep,
    _current_user: AIAPIViewAllUser,
    status: Literal["active", "inactive"] | None = None,
    user_email: str | None = Query(default=None, max_length=255),
    query: str | None = Query(default=None, max_length=255),
    user_role: list[UserRole] | None = Query(default=None),
    created_after: datetime | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
) -> Any:
    return ai_gateway_service.list_all_credentials(
        session=session,
        status=status,
        user_email=user_email,
        query=query,
        user_roles=[role.value for role in user_role] if user_role else None,
        created_after=created_after,
        skip=skip,
        limit=limit,
    )


@router.get("/credentials/{credential_id}", response_model=AIAPICredentialWithSecret)
def get_my_ai_api_credential(
    credential_id: uuid.UUID,
    response: Response,
    session: SessionDep,
    current_user: CurrentUser,
) -> Any:
    """擁有者查看單把金鑰詳細資料；含明文回應不得快取。"""
    response.headers["Cache-Control"] = "no-store"
    return ai_gateway_service.get_credential(
        session=session, credential_id=credential_id, current_user=current_user
    )


@router.post(
    "/credentials/{credential_id}/rotate",
    response_model=AIAPICredentialWithSecret,
    dependencies=[_AI_API_ROTATE_RATE_LIMIT],
)
def rotate_my_ai_api_credential(
    credential_id: uuid.UUID,
    response: Response,
    session: SessionDep,
    current_user: CurrentUser,
) -> Any:
    """輪替金鑰；僅擁有者本人取得新金鑰明文。"""
    response.headers["Cache-Control"] = "no-store"
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


@router.patch(
    "/credentials/{credential_id}",
    response_model=AIAPICredentialPublic,
    openapi_extra=limited_json_openapi(AIAPICredentialUpdate),
)
async def update_my_ai_api_credential(
    credential_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    current_user: CurrentUser,
) -> Any:
    update_data = await parse_limited_json(request, AIAPICredentialUpdate)
    return await run_in_threadpool(
        ai_gateway_service.update_credential_name,
        session=session,
        credential_id=credential_id,
        name=update_data.api_key_name,
        current_user=current_user,
    )
