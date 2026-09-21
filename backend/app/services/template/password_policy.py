"""機器登入密碼的單一規則來源。

範本的 ``allow_password_change``（UI：「允許自訂登入密碼」）決定開出來的機器
用哪一組密碼，所有建立入口都走這裡，避免各自為政：

==================  ====================  ==========================
入口                 範本不勾              範本勾選 / 一般映像
==================  ====================  ==========================
學生申請             沿用範本內的密碼      申請人自訂（必填）
快速練習             沿用範本內的密碼      隨機
班級機器             沿用範本內的密碼      隨機
==================  ====================  ==========================

Course Lab（課程實驗機）不走這張表：它一律沿用範本內的密碼（見
``deployment_service._build_request``），是該功能原本的設計。

「沿用」＝平台完全不碰密碼：機器轉成範本時裡面是什麼，克隆出來就是什麼。
平台不知道那組密碼（系統只存雜湊），因此不記錄、不顯示，由老師告知學生。
"""

from __future__ import annotations

import uuid

from sqlmodel import Session, select

from app.core.i18n import t
from app.exceptions import BadRequestError
from app.models import VMTemplate, VMTemplateStatus
from app.utils.login_password import generate_login_password


def find_template(
    session: Session,
    *,
    template_id: uuid.UUID | str | None = None,
    pve_vmid: int | None = None,
) -> VMTemplate | None:
    """以範本 id 或 PVE VMID 找已註冊的範本；找不到（一般映像 / 原生 PVE 範本）回 None。"""
    if template_id:
        return session.get(VMTemplate, uuid.UUID(str(template_id)))
    if pve_vmid is None:
        return None
    # VMID 會被回收再利用，排除已刪除的舊範本列
    return session.exec(
        select(VMTemplate).where(
            VMTemplate.pve_vmid == pve_vmid,
            VMTemplate.status != VMTemplateStatus.deleted,
        )
    ).first()


def keeps_template_credentials(template: VMTemplate | None) -> bool:
    """這個來源開出來的機器是否沿用範本內建密碼（平台不設、不記錄）。"""
    return template is not None and not template.allow_password_change


def resolve_login_password(
    *,
    template: VMTemplate | None,
    custom: str | None = None,
    require_custom: bool = False,
) -> str | None:
    """回傳要寫進機器的密碼；``None`` 代表沿用範本內建密碼。

    ``require_custom``：申請人必須自己輸入（學生申請）。其餘入口未給 ``custom``
    時發隨機密碼。
    """
    if keeps_template_credentials(template):
        return None
    if custom:
        return custom
    if require_custom:
        raise BadRequestError(t("vm_request.password_required"))
    return generate_login_password()


__all__ = [
    "find_template",
    "keeps_template_credentials",
    "resolve_login_password",
]
