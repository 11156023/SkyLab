import uuid

from fastapi import APIRouter, Depends, Query

from app.api.deps import AdminUser, CurrentUser, SessionDep, rate_limit_by_user
from app.exceptions import BadRequestError
from app.models import VMRequestStatus
from app.repositories import governance as governance_repo
from app.schemas import (
    VMRequestAvailabilityRequest,
    VMRequestAvailabilityResponse,
    VMRequestCreate,
    VMRequestPublic,
    VMRequestReview,
    VMRequestReviewContext,
    VMRequestsPublic,
    VMRequestWindowAvailabilityRequest,
    VMRequestWindowAvailabilityResponse,
    WorkloadAdviceResponse,
    WorkloadAdviseRequest,
)
from app.services.vm import (
    vm_request_availability_service,
    vm_request_service,
    workload_advisor,
)

router = APIRouter(prefix="/vm-requests", tags=["vm-requests"])

# Limit to 20 VM-request creations per user per minute (anti-abuse).
_CREATE_RATE_LIMIT = Depends(
    rate_limit_by_user(scope="vm-request-create", limit=20, window_seconds=60)
)


@router.post("/", response_model=VMRequestPublic, dependencies=[_CREATE_RATE_LIMIT])
def create_vm_request(
    request_in: VMRequestCreate, session: SessionDep, current_user: CurrentUser
):
    return vm_request_service.create(
        session=session, request_in=request_in, user=current_user
    )


@router.post("/advise", response_model=WorkloadAdviceResponse)
def advise_workload(
    request_in: WorkloadAdviseRequest,
    session: SessionDep,
    _: CurrentUser,
) -> WorkloadAdviceResponse:
    """VM vs LXC 自動判斷（規則引擎，回傳建議型別與理由）。"""
    config = governance_repo.get_governance_config(session=session)
    if not config.workload_advisor_enabled:
        raise BadRequestError("Auto mode is disabled by administrator")
    advice = workload_advisor.advise(
        environment_type=request_in.environment_type,
        os_info=request_in.os_info,
        reason=request_in.reason,
        cores=request_in.cores,
        memory=request_in.memory,
        gpu_mapping_id=request_in.gpu_mapping_id,
        service_template_slug=request_in.service_template_slug,
    )
    return WorkloadAdviceResponse(
        resource_type=advice.resource_type,
        confidence=advice.confidence,
        reasons=advice.reasons,
    )


@router.post("/availability", response_model=VMRequestAvailabilityResponse)
def get_vm_request_availability(
    request_in: VMRequestAvailabilityRequest,
    session: SessionDep,
    current_user: CurrentUser,
):
    return vm_request_availability_service.assess_request(
        session=session,
        current_user=current_user,
        request_in=request_in,
    )


@router.post(
    "/window-availability",
    response_model=VMRequestWindowAvailabilityResponse,
)
def get_vm_request_window_availability(
    request_in: VMRequestWindowAvailabilityRequest,
    session: SessionDep,
    current_user: CurrentUser,
):
    return vm_request_availability_service.assess_request_window(
        session=session,
        current_user=current_user,
        request_in=request_in,
    )


@router.get("/my", response_model=VMRequestsPublic)
def list_my_vm_requests(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
):
    return vm_request_service.list_by_user(
        session=session, user_id=current_user.id, skip=skip, limit=limit
    )


@router.get("/", response_model=VMRequestsPublic)
def list_all_vm_requests(
    session: SessionDep,
    current_user: AdminUser,
    status: VMRequestStatus | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
):
    return vm_request_service.list_all(
        session=session, status=status, skip=skip, limit=limit
    )


@router.get("/{request_id}", response_model=VMRequestPublic)
def get_vm_request(
    request_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
):
    return vm_request_service.get(
        session=session, request_id=request_id, current_user=current_user
    )


@router.post("/{request_id}/cancel", response_model=VMRequestPublic)
def cancel_vm_request(
    request_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    return vm_request_service.cancel(
        session=session,
        request_id=request_id,
        current_user=current_user,
    )


@router.post("/{request_id}/retry", response_model=VMRequestPublic)
def retry_vm_request(
    request_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    """Re-fire provisioning for an approved VM request whose previous attempt failed."""
    return vm_request_service.retry(
        session=session,
        request_id=request_id,
        current_user=current_user,
    )


@router.get("/{request_id}/review-context", response_model=VMRequestReviewContext)
def get_vm_request_review_context(
    request_id: uuid.UUID,
    session: SessionDep,
    current_user: AdminUser,
):
    return vm_request_service.get_review_context(
        session=session,
        request_id=request_id,
        current_user=current_user,
    )


@router.get("/{request_id}/availability", response_model=VMRequestAvailabilityResponse)
def get_existing_vm_request_availability(
    request_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    days: int = Query(default=7, ge=1, le=14),
    timezone: str = Query(default="Asia/Taipei", min_length=1, max_length=64),
):
    return vm_request_availability_service.assess_existing_request(
        session=session,
        request_id=request_id,
        current_user=current_user,
        days=days,
        timezone=timezone,
    )


@router.post("/{request_id}/review", response_model=VMRequestPublic)
def review_vm_request(
    request_id: uuid.UUID,
    review: VMRequestReview,
    session: SessionDep,
    current_user: AdminUser,
):
    return vm_request_service.review(
        session=session,
        request_id=request_id,
        review_data=review,
        reviewer=current_user,
    )
