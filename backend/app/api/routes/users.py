import logging
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.api.deps import (
    CurrentUser,
    SessionDep,
    get_current_active_superuser,
)
from app.core.config import settings
from app.core.i18n import t
from app.models import User
from app.schemas import (
    Message,
    TotpCodeRequest,
    TotpSetupPublic,
    TotpStatusPublic,
    UpdatePassword,
    UserCreate,
    UserPublic,
    UserRegister,
    UsersPublic,
    UserUpdate,
    UserUpdateMe,
)
from app.services.user import totp_service, user_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])

# 頭像檔案存放目錄（repo 根的 data/avatars，與 teacher-judge 慣例一致），
# 檔名固定為 {user_id}.{ext}
AVATAR_DIR = Path(__file__).resolve().parents[4] / "data" / "avatars"
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _delete_avatar_files(user_id: uuid.UUID) -> None:
    """帳號刪除後移除頭像檔：頭像端點不驗證身分，留著就會被任何知道 UUID 的人下載。"""
    for path in AVATAR_DIR.glob(f"{user_id}.*"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove avatar file %s", path, exc_info=True)


def _store_avatar(user_id: uuid.UUID, ext: str, data: bytes) -> None:
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    for old in AVATAR_DIR.glob(f"{user_id}.*"):
        old.unlink(missing_ok=True)
    (AVATAR_DIR / f"{user_id}{ext}").write_bytes(data)


@router.get(
    "/",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=UsersPublic,
)
def read_users(
    session: SessionDep,
    # 負值會讓 PostgreSQL OFFSET/LIMIT 報錯（500）；上限須 ≥ 前端分頁的 200
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> Any:
    return user_service.list_users(session=session, skip=skip, limit=limit)


@router.post(
    "/", dependencies=[Depends(get_current_active_superuser)], response_model=UserPublic
)
def create_user(
    *, session: SessionDep, current_user: CurrentUser, user_in: UserCreate
) -> Any:
    return user_service.create_user(
        session=session, user_in=user_in, current_user_id=current_user.id
    )


@router.patch("/me", response_model=UserPublic)
def update_user_me(
    *, session: SessionDep, user_in: UserUpdateMe, current_user: CurrentUser
) -> Any:
    return user_service.update_me(
        session=session, user_in=user_in, current_user=current_user
    )


@router.patch("/me/password", response_model=Message)
def update_password_me(
    *, session: SessionDep, body: UpdatePassword, current_user: CurrentUser
) -> Any:
    user_service.update_password(
        session=session,
        current_password=body.current_password,
        new_password=body.new_password,
        current_user=current_user,
    )
    return Message(message="Password updated successfully")


@router.get("/me", response_model=UserPublic)
def read_user_me(current_user: CurrentUser) -> Any:
    me = UserPublic.model_validate(current_user)
    # 管理員要求此帳號啟用 2FA 但尚未綁定：前端只顯示綁定畫面
    me.totp_setup_required = current_user.totp_required and not current_user.totp_enabled
    return me


@router.post("/me/onboarding/complete", response_model=UserPublic)
def complete_onboarding_me(*, session: SessionDep, current_user: CurrentUser) -> Any:
    """首次登入引導精靈走完或略過：之後登入不再顯示引導畫面。"""
    return user_service.complete_onboarding(session=session, current_user=current_user)


# ── 兩步驟驗證（TOTP，可綁定 Google Authenticator） ──


@router.post("/me/totp/setup", response_model=TotpSetupPublic)
def setup_totp_me(*, session: SessionDep, current_user: CurrentUser) -> Any:
    """產生金鑰與 otpauth URI（待確認狀態；確認前登入不會要求驗證碼）。"""
    return totp_service.begin_setup(session=session, user=current_user)


@router.post("/me/totp/confirm", response_model=TotpStatusPublic)
def confirm_totp_me(
    *, session: SessionDep, body: TotpCodeRequest, current_user: CurrentUser
) -> Any:
    """用 Authenticator 產生的驗證碼確認綁定，正式啟用。"""
    totp_service.confirm_setup(session=session, user=current_user, code=body.code)
    return TotpStatusPublic(totp_enabled=True)


@router.post("/me/totp/disable", response_model=TotpStatusPublic)
def disable_totp_me(
    *, session: SessionDep, body: TotpCodeRequest, current_user: CurrentUser
) -> Any:
    """停用兩步驟驗證（需一組目前有效的驗證碼）。"""
    totp_service.disable(session=session, user=current_user, code=body.code)
    return TotpStatusPublic(totp_enabled=False)


@router.post("/me/avatar", response_model=UserPublic)
async def upload_avatar_me(
    session: SessionDep, current_user: CurrentUser, file: UploadFile = File(...)
) -> Any:
    """上傳頭像圖片，存檔後把 avatar_url 指向本服務的頭像端點。"""
    ext = AVATAR_CONTENT_TYPES.get((file.content_type or "").lower())
    if not ext:
        raise HTTPException(
            status_code=400, detail=t("users.avatarUnsupportedFormat")
        )
    # 邊讀邊檢查大小，超過上限立即中止，避免先把整個檔案讀進記憶體
    buffer = bytearray()
    while chunk := await file.read(64 * 1024):
        buffer.extend(chunk)
        if len(buffer) > AVATAR_MAX_BYTES:
            raise HTTPException(
                status_code=400, detail=t("users.avatarTooLarge")
            )
    data = bytes(buffer)

    # 檔案 I/O 與同步 DB commit 都丟到 worker thread，不佔住 event loop
    await run_in_threadpool(_store_avatar, current_user.id, ext, data)

    # v= 時間戳讓 <img> 換圖時不吃瀏覽器快取
    avatar_url = (
        f"{settings.API_V1_STR}/users/{current_user.id}/avatar?v={int(time.time())}"
    )
    return await run_in_threadpool(
        user_service.update_me,
        session=session,
        user_in=UserUpdateMe(avatar_url=avatar_url),
        current_user=current_user,
    )


@router.get("/{user_id}/avatar")
def get_user_avatar(user_id: uuid.UUID, session: SessionDep) -> FileResponse:
    """頭像檔案。<img> 標籤無法帶 Authorization header，因此不做驗證；
    user_id 由路由強制為 UUID，不會有路徑穿越問題。帳號已刪除時一律 404
    （涵蓋刪除前殘留的舊檔）。"""
    matches = sorted(AVATAR_DIR.glob(f"{user_id}.*"))
    if not matches or session.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="Avatar not found")
    return FileResponse(matches[0])


@router.delete("/me", response_model=Message)
def delete_user_me(session: SessionDep, current_user: CurrentUser) -> Any:
    user_id = current_user.id
    user_service.delete_me(session=session, current_user=current_user)
    _delete_avatar_files(user_id)
    return Message(message="User deleted successfully")


@router.post("/signup", response_model=UserPublic)
def register_user(session: SessionDep, user_in: UserRegister) -> Any:
    return user_service.register_user(session=session, user_in=user_in)


@router.get("/{user_id}", response_model=UserPublic)
def read_user_by_id(
    user_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
) -> Any:
    return user_service.get_user_by_id(
        session=session, user_id=user_id, current_user=current_user
    )


@router.patch(
    "/{user_id}",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=UserPublic,
)
def update_user(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    user_id: uuid.UUID,
    user_in: UserUpdate,
) -> Any:
    return user_service.update_user(
        session=session,
        user_id=user_id,
        user_in=user_in,
        current_user_id=current_user.id,
    )


@router.delete("/{user_id}", dependencies=[Depends(get_current_active_superuser)])
def delete_user(
    session: SessionDep, current_user: CurrentUser, user_id: uuid.UUID
) -> Message:
    user_service.delete_user(
        session=session, user_id=user_id, current_user=current_user
    )
    _delete_avatar_files(user_id)
    return Message(message="User deleted successfully")


@router.delete(
    "/{user_id}/totp",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=TotpStatusPublic,
)
def reset_user_totp(
    session: SessionDep, current_user: CurrentUser, user_id: uuid.UUID
) -> Any:
    """管理員解除某使用者的兩步驟驗證（手機遺失救援；對方需重新登入）。"""
    totp_service.admin_reset(session=session, user_id=user_id, actor=current_user)
    return TotpStatusPublic(totp_enabled=False)
