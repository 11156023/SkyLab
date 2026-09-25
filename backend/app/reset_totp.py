"""救援指令：在伺服器端解除某個帳號的兩步驟驗證（TOTP）。

用途：唯一的管理員遺失 Authenticator 裝置、沒有其他管理員能在網頁上替他重設時，
由有主機權限的人在 backend 容器內執行：

    docker compose exec backend uv run python app/reset_totp.py admin@example.com

效果與管理員在網頁上的「重設兩步驟驗證」相同：清掉金鑰、token_version +1
（既有登入與挑戰 token 全部失效），並留下稽核紀錄（user_id 為 None、內容註明
是由 CLI 執行）。之後該帳號登入只需密碼，可再自行重新綁定。
"""

from __future__ import annotations

import logging
import sys

from sqlmodel import Session, select

from app.core.db import engine
from app.models import AuditAction, User
from app.services.user import audit_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def reset_totp(email: str) -> int:
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        if user is None:
            logger.error("找不到使用者：%s", email)
            return 1
        if not user.totp_enabled and not user.totp_secret_encrypted:
            logger.info("使用者 %s 未啟用兩步驟驗證，無需重設", email)
            return 0
        user.totp_enabled = False
        user.totp_secret_encrypted = None
        user.totp_last_used_step = None
        user.token_version += 1
        session.add(user)
        audit_service.log_action(
            session=session,
            user_id=None,
            action=AuditAction.totp_admin_reset,
            details=f"CLI (app/reset_totp.py) reset two-factor authentication for {email}",
            commit=False,
        )
        session.commit()
        logger.info("已解除 %s 的兩步驟驗證；該帳號既有登入已全部失效", email)
        return 0


def main() -> None:
    if len(sys.argv) != 2:
        logger.error("用法：python app/reset_totp.py <使用者 email>")
        sys.exit(2)
    sys.exit(reset_totp(sys.argv[1].strip()))


if __name__ == "__main__":
    main()
