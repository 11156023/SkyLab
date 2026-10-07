"""機器登入密碼的單一規則來源。

開出來的機器用哪一組密碼，所有建立入口都走這裡，避免各自為政：

====================  ======================  ==========================
入口                   平台設得了密碼           平台設不了密碼
====================  ======================  ==========================
學生申請               申請人自訂（必填）       沿用範本內的密碼
範本克隆               自訂，未填發隨機         沿用範本內的密碼
快速練習／班級機器      隨機                     沿用範本內的密碼
====================  ======================  ==========================

「設不設得了」是 ``VMTemplate.password_settable``：轉範本時偵測出來的事實
（VM 沒有 cloud-init 就寫不進去），不是老師可以選的選項。設不了時平台完全
不碰密碼：機器轉成範本時裡面是什麼，克隆出來就是什麼，由老師告知學生。

保存規則：

- 系統隨機產生的密碼：加密後存 ``resources.login_password_encrypted``，
  擁有者可在資源頁看到。
- 使用者自訂的密碼：只留 SHA-512 crypt 雜湊，平台解不回來，忘記只能重設。
  唯一例外是 Windows —— cloudbase-init 只收明文，所以建機前加密暫存在
  申請單／任務 payload，建完即清。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlmodel import Session, select

from app.core.i18n import t
from app.core.security import encrypt_value
from app.exceptions import BadRequestError
from app.models import VMTemplate, VMTemplateStatus
from app.utils.login_password import generate_login_password, hash_login_password


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
    """這個來源開出來的機器是否沿用範本內建密碼（平台設不了、不記錄）。"""
    return template is not None and not template.password_settable


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


@dataclass(frozen=True)
class SealedPassword:
    """自訂密碼在建機前的保存形式；兩欄至多一個有值。"""

    encrypted: str | None = None  # 加密後的明文（只有 Windows）
    crypt_hash: str | None = None  # SHA-512 crypt 雜湊（其餘）


def seal_custom_password(password: str | None, *, windows: bool) -> SealedPassword:
    """把使用者自訂的密碼轉成可以落 DB 的形式；``None`` 回空的結果。"""
    if not password:
        return SealedPassword()
    if windows:
        return SealedPassword(encrypted=encrypt_value(password))
    return SealedPassword(crypt_hash=hash_login_password(password))


__all__ = [
    "SealedPassword",
    "find_template",
    "keeps_template_credentials",
    "resolve_login_password",
    "seal_custom_password",
]
