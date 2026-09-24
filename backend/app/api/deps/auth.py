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
from app.core.db import engine
from app.core.i18n import t
from app.core.permissions import Permission, require_permission
from app.exceptions import AuthenticationError, PermissionDeniedError
from app.infrastructure.redis import get_redis, is_jti_revoked
from app.models import User
from app.repositories import auth_policy as auth_policy_repo
from app.schemas import TokenPayload

logger = logging.getLogger(__name__)

reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token"
)

TokenDep = Annotated[str, Depends(reusable_oauth2)]


# 強制 2FA 開啟時，尚未綁定的使用者仍可用的路徑前綴：看自己的資料、綁定
# 兩步驟驗證、讀政策、登出／續期；管理員還能關掉政策（避免自己被鎖在外面）。
# 其餘 API 一律 403，直到綁定完成。
_TOTP_ENROLLMENT_ALLOWED_PREFIXES = (
    f"{settings.API_V1_STR}/users/me",
    f"{settings.API_V1_STR}/login/",
    f"{settings.API_V1_STR}/auth-policy",
    f"{settings.API_V1_STR}/admin/auth-policy",
)


def _totp_enrollment_allowed(path: str) -> bool:
    return path.startswith(_TOTP_ENROLLMENT_ALLOWED_PREFIXES)


async def get_current_user(
    session: SessionDep, token: TokenDep, request: Request
) -> User:
    # All failures here are authentication problems (bad/expired/revoked token,
    # missing or inactive user), so they must return 401 to trigger the
    # frontend refresh-token flow. Never raise 403 from this function — that
    # would incorrectly signal "authenticated but forbidden". The frontend
    # treats 403 as forbidden without logging the user out; 401 is what drives
    # token refresh and eventual logout if refresh fails.
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
    # token_version global kill switch enforced below).
    if token_data.jti:
        redis = await get_redis()
        if await is_jti_revoked(redis, token_data.jti):
            raise AuthenticationError(t("auth.token_revoked"))
    # 同步 DB 查詢不可直接在 event loop 上執行：連線池耗盡時會凍結整個
    # loop，使已完成的請求無法歸還連線而形成死結（見 tests/performance）。
    user = await run_in_threadpool(session.get, User, token_data.sub)
    if not user:
        raise AuthenticationError(t("auth.user_not_found"))
    if not user.is_active:
        raise AuthenticationError(t("auth.user_inactive"))
    if user.token_version != token_data.ver:
        raise AuthenticationError(t("auth.token_revoked"))
    # 管理員強制全站 2FA：尚未綁定的使用者只能走綁定相關端點（403 不會觸發
    # 前端登出流程；前端依 /users/me 的 totp_setup_required 顯示綁定畫面）。
    if not user.totp_enabled and not _totp_enrollment_allowed(request.url.path):
        totp_required = await run_in_threadpool(
            auth_policy_repo.is_totp_required, session=session
        )
        if totp_required:
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

    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except (InvalidTokenError, ValidationError):
        logger.warning("WebSocket connection with invalid token")
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    # Mirror get_current_user: only access tokens may open WebSocket
    # connections; revoked (blacklisted) tokens are rejected as well.
    if token_data.type != "access":
        logger.warning("WebSocket connection attempted with a non-access token")
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    if token_data.jti:
        redis = await get_redis()
        if await is_jti_revoked(redis, token_data.jti):
            logger.warning("WebSocket connection attempted with a revoked token")
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    session = Session(engine)
    try:
        user = await run_in_threadpool(session.get, User, token_data.sub)
        if not user or not user.is_active:
            logger.warning(f"WebSocket auth failed: user not found or inactive (sub={token_data.sub})")
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
        if user.token_version != token_data.ver:
            logger.warning(f"WebSocket auth failed: token version mismatch for user {user.email}")
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
        return user, session
    except Exception:
        session.close()
        raise
