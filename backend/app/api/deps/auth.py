import logging
from typing import Annotated

import jwt
from fastapi import Depends, Query, Request, WebSocket, WebSocketException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from sqlmodel import Session

from app.api.deps.database import SessionDep
from app.core import security
from app.core.authorizers import (
    require_admin_access,
    require_instructor_or_admin_access,
)
from app.core.config import settings
from app.core.db import end_read_transaction, engine
from app.core.i18n import t
from app.core.permissions import Permission, require_permission
from app.exceptions import AuthenticationError, PermissionDeniedError
from app.infrastructure.redis import get_redis, is_jti_revoked
from app.models import User
from app.schemas import TokenPayload

logger = logging.getLogger(__name__)

reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token"
)

TokenDep = Annotated[str, Depends(reusable_oauth2)]


# 帳號被管理員要求啟用 2FA 但尚未綁定時仍可用的路徑前綴：看自己的資料、綁定
# 兩步驟驗證、登出／續期。其餘 API 一律 403，直到綁定完成。
_TOTP_ENROLLMENT_ALLOWED_PREFIXES = (
    f"{settings.API_V1_STR}/users/me",
    f"{settings.API_V1_STR}/login/",
)


def _totp_enrollment_allowed(path: str) -> bool:
    return path.startswith(_TOTP_ENROLLMENT_ALLOWED_PREFIXES)


async def _validate_access_token(token: str) -> TokenPayload:
    """Decode a JWT and check that it is a live access token.

    HTTP 與 WebSocket 認證共用這一段：簽章／格式、只收 access token、
    Redis jti 黑名單。失敗一律丟 AuthenticationError（HTTP 401），
    WebSocket 端再轉成 1008 關閉。
    """
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except (InvalidTokenError, ValidationError):
        raise AuthenticationError(t("auth.invalid_credentials"))
    # Only access tokens may call the API — this also rejects refresh tokens
    # and any other JWT signed with the same key (e.g. password-reset tokens).
    if token_data.type != "access":
        raise AuthenticationError(t("auth.access_token_only"))
    # Per-token revocation via Redis blacklist (in addition to the
    # token_version global kill switch enforced in _check_token_user).
    if token_data.jti:
        redis = await get_redis()
        if await is_jti_revoked(redis, token_data.jti):
            raise AuthenticationError(t("auth.token_revoked"))
    return token_data


def _check_token_user(user: User | None, token_data: TokenPayload) -> User:
    """The token's user must exist, be active and match the token_version."""
    if not user:
        raise AuthenticationError(t("auth.user_not_found"))
    if not user.is_active:
        raise AuthenticationError(t("auth.user_inactive"))
    if user.token_version != token_data.ver:
        raise AuthenticationError(t("auth.token_revoked"))
    return user


def _load_user_and_release(session: Session, user_id: str | None) -> User | None:
    """讀出 token 的使用者後立刻結束讀取交易、歸還連線。

    每個已登入請求都先經過這裡；若沿用 session.get 開出的交易，連線會一路
    被佔到回應送出為止——端點在等 threadpool 空位或 await PVE／LLM 時也一樣，
    整班同時登入就會把連線池耗盡（QueuePool limit ... reached）。端點之後需要
    DB 時會自動再取一條。
    """
    user = session.get(User, user_id)
    end_read_transaction(session)
    return user


async def get_current_user(
    session: SessionDep, token: TokenDep, request: Request
) -> User:
    # All failures here are authentication problems (bad/expired/revoked token,
    # missing or inactive user), so they must return 401 to trigger the
    # frontend refresh-token flow. Never raise 403 from this function — that
    # would incorrectly signal "authenticated but forbidden". The frontend
    # treats 403 as forbidden without logging the user out; 401 is what drives
    # token refresh and eventual logout if refresh fails.
    token_data = await _validate_access_token(token)
    # 同步 DB 查詢不可直接在 event loop 上執行：連線池耗盡時會凍結整個
    # loop，使已完成的請求無法歸還連線而形成死結（見 tests/performance）。
    user = _check_token_user(
        await run_in_threadpool(_load_user_and_release, session, token_data.sub),
        token_data,
    )
    # 管理員在使用者資料勾了「強制兩步驟驗證」：尚未綁定前只能走綁定相關端點
    # （403 不會觸發前端登出流程；前端依 /users/me 的 totp_setup_required 顯示綁定畫面）。
    if (
        user.totp_required
        and not user.totp_enabled
        and not _totp_enrollment_allowed(request.url.path)
    ):
        raise PermissionDeniedError(t("auth.totpSetupRequired"))
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_active_superuser(current_user: CurrentUser) -> User:
    require_admin_access(current_user)
    return current_user


AdminUser = Annotated[User, Depends(get_current_active_superuser)]


def get_current_instructor_or_admin(current_user: CurrentUser) -> User:
    require_instructor_or_admin_access(current_user)
    return current_user


InstructorUser = Annotated[User, Depends(get_current_instructor_or_admin)]


def get_current_ai_api_reviewer(current_user: CurrentUser) -> User:
    require_permission(current_user, Permission.AI_API_REVIEW)
    return current_user


AIAPIReviewerUser = Annotated[User, Depends(get_current_ai_api_reviewer)]


def get_current_ai_api_view_all(current_user: CurrentUser) -> User:
    require_permission(current_user, Permission.AI_API_VIEW_ALL)
    return current_user


AIAPIViewAllUser = Annotated[User, Depends(get_current_ai_api_view_all)]


async def get_ws_current_user(
    websocket: WebSocket,
    token: str = Query(...),
) -> tuple[User, Session]:
    """Authenticate WebSocket connections via query-string token.
    Returns (user, session) so the caller can also check ownership."""
    # Reject empty or oversized tokens
    if not token or not token.strip():
        logger.warning("WebSocket connection attempted with empty token")
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    if len(token) > 4096:
        logger.warning("WebSocket connection attempted with oversized token")
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    # Same token and user checks as get_current_user (shared helpers); any
    # failure closes the socket with 1008 instead of answering 401.
    try:
        token_data = await _validate_access_token(token)
    except AuthenticationError as exc:
        logger.warning("WebSocket auth failed: %s", exc.message)
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    session = Session(engine)
    try:
        try:
            user = _check_token_user(
                await run_in_threadpool(session.get, User, token_data.sub),
                token_data,
            )
        except AuthenticationError as exc:
            logger.warning(
                "WebSocket auth failed (sub=%s): %s", token_data.sub, exc.message
            )
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
        # 與 get_current_user 同一道閘：被要求強制 2FA 但尚未綁定的帳號，
        # 在完成綁定前不可開任何 WebSocket（VNC／終端機／教室／任務推送）。
        if user.totp_required and not user.totp_enabled:
            logger.warning(
                "WebSocket auth failed: two-factor setup required for user %s",
                user.email,
            )
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
        return user, session
    except Exception:
        session.close()
        raise
