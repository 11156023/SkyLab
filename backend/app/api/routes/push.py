"""Web Push 訂閱 API：任何登入使用者管理自己的瀏覽器推播訂閱。"""

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, SessionDep
from app.core.i18n import get_current_language
from app.repositories import push as push_repo
from app.schemas.push import (
    PushSendResult,
    PushSubscriptionCreate,
    PushSubscriptionDelete,
    PushSubscriptionPublic,
    VapidPublicKeyResponse,
)
from app.services.notification import web_push_service

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/vapid-public-key", response_model=VapidPublicKeyResponse)
def get_vapid_public_key(session: SessionDep, _: CurrentUser) -> VapidPublicKeyResponse:
    """前端訂閱推播所需的 applicationServerKey；後端缺 pywebpush 時回 enabled=false。"""
    if not web_push_service.is_available():
        return VapidPublicKeyResponse(enabled=False, public_key=None)
    config = push_repo.get_web_push_config(session=session)
    return VapidPublicKeyResponse(enabled=True, public_key=config.vapid_public_key)


@router.post("/subscriptions", response_model=PushSubscriptionPublic)
def save_subscription(
    session: SessionDep, current_user: CurrentUser, body: PushSubscriptionCreate
) -> PushSubscriptionPublic:
    """儲存（或更新）這個瀏覽器的推播訂閱；同一 endpoint 換帳號登入會改歸屬。"""
    language = web_push_service.normalize_language(
        body.language or get_current_language()
    )
    subscription = push_repo.upsert_subscription(
        session=session,
        user_id=current_user.id,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        user_agent=body.user_agent,
        language=language,
    )
    return PushSubscriptionPublic(
        id=subscription.id,
        endpoint=subscription.endpoint,
        language=subscription.language,
    )


@router.delete("/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
def remove_subscription(
    session: SessionDep, current_user: CurrentUser, body: PushSubscriptionDelete
) -> Response:
    """退訂：只能刪自己的；別人的或不存在的 endpoint 一律當作已不存在。"""
    subscription = push_repo.get_subscription_by_endpoint(
        session=session, endpoint=body.endpoint
    )
    if subscription is not None and subscription.user_id == current_user.id:
        push_repo.delete_subscription(session=session, subscription=subscription)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/test", response_model=PushSendResult)
def send_test_push(session: SessionDep, current_user: CurrentUser) -> PushSendResult:
    """對目前使用者的所有訂閱發一則測試通知，驗證整條推播鏈。"""
    report = web_push_service.send_test(session=session, user=current_user)
    return PushSendResult(sent=report.sent, removed=report.removed)
