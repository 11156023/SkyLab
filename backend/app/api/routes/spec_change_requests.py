import uuid

from fastapi import APIRouter

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.models import SpecChangeRequestStatus
from app.schemas import (
    SpecChangeApplyAccepted,
    SpecChangeRequestCreate,
    SpecChangeRequestPublic,
    SpecChangeRequestReview,
    SpecChangeRequestsPublic,
)
from app.services.vm import spec_change_service

router = APIRouter(prefix="/spec-change-requests", tags=["spec-change-requests"])


@router.post("/", response_model=SpecChangeRequestPublic)
def create_spec_change_request(
    request_in: SpecChangeRequestCreate,
    session: SessionDep,
    current_user: CurrentUser,
):
    return spec_change_service.create(
        session=session, request_in=request_in, user=current_user
    )


@router.get("/my", response_model=SpecChangeRequestsPublic)
def get_my_spec_change_requests(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
):
    return spec_change_service.list_by_user(
        session=session, user_id=current_user.id, skip=skip, limit=limit
    )


@router.get("/", response_model=SpecChangeRequestsPublic)
def get_all_spec_change_requests(
    session: SessionDep,
    current_user: AdminUser,
    skip: int = 0,
    limit: int = 100,
    status: SpecChangeRequestStatus | None = None,
    vmid: int | None = None,
):
    return spec_change_service.list_all(
        session=session, skip=skip, limit=limit, status=status, vmid=vmid
    )


@router.post("/{request_id}/review", response_model=SpecChangeRequestPublic)
def review_spec_change_request(
    request_id: uuid.UUID,
    review: SpecChangeRequestReview,
    session: SessionDep,
    current_user: AdminUser,
):
    """核准只做配額檢查與狀態變更；規格由申請人之後按「套用」才寫進 Proxmox。"""
    return spec_change_service.review(
        session=session,
        request_id=request_id,
        review_data=review,
        reviewer=current_user,
    )


@router.post(
    "/{request_id}/apply",
    response_model=SpecChangeApplyAccepted,
    status_code=202,
)
def apply_spec_change_request(
    request_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    """申請人套用已核准的規格（202，背景任務）。

    執行中的虛擬機改 CPU／記憶體需要重開機才生效：任務會先關機、套用、
    再自動開機。容器可線上生效，不會重開。
    """
    return spec_change_service.apply(
        session=session, request_id=request_id, user=current_user
    )


@router.post("/{request_id}/cancel", response_model=SpecChangeRequestPublic)
def cancel_spec_change_request(
    request_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    """申請人撤銷待審核、或已核准但尚未套用的申請。"""
    return spec_change_service.cancel(
        session=session, request_id=request_id, user=current_user
    )
