"""登入憑證管理：重設密碼、重新產生平台金鑰、匯入／移除自己的公鑰。

QEMU 走 cloud-init（``cipassword`` / ``sshkeys``），設定寫進 Proxmox 後要
重新開機才會套進 guest；執行中且 guest agent 有回應時，密碼會順便用
``chpasswd`` 直接改掉。LXC 沒有 cloud-init，一律用 ``pct exec`` 進容器改，
所以容器必須在執行中。
"""

from __future__ import annotations

import logging
import shlex
import uuid
from typing import Any
from urllib.parse import quote, unquote

from sqlmodel import Session

from app.core.i18n import t
from app.core.security import encrypt_value
from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.infrastructure.proxmox import guest
from app.infrastructure.ssh.client import generate_ed25519_keypair
from app.repositories import resource as resource_repo
from app.schemas.resource_settings import (
    AuthorizedKeysResponse,
    CredentialsPublic,
    PasswordResetResponse,
    SshKeyRegenerateResponse,
)
from app.services.proxmox import proxmox_service
from app.services.user import audit_service

logger = logging.getLogger(__name__)

_LXC_AUTHORIZED_KEYS = "/root/.ssh/authorized_keys"


def _rtype(resource_info: dict[str, Any]) -> str:
    return "lxc" if str(resource_info.get("type") or "") == "lxc" else "qemu"


def _is_running(resource_info: dict[str, Any]) -> bool:
    return str(resource_info.get("status") or "") == "running"


def _key_identity(key: str) -> str:
    """比對公鑰只看「類型 + base64」，忽略尾端的註解。"""
    parts = key.strip().split()
    return " ".join(parts[:2]) if len(parts) >= 2 else key.strip()


def _split_keys(text: str) -> list[str]:
    keys: list[str] = []
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            keys.append(line)
    return keys


def _dedupe(keys: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for key in keys:
        ident = _key_identity(key)
        if ident in seen:
            continue
        seen.add(ident)
        result.append(key)
    return result


def _get_db_resource(session: Session, vmid: int):
    db_resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if db_resource is None:
        raise NotFoundError(t("resource_settings.resourceNotRegistered", vmid=vmid))
    return db_resource


# ─── QEMU（cloud-init） ────────────────────────────────────────────────────────


def _qemu_config(resource_info: dict[str, Any], vmid: int) -> dict[str, Any]:
    try:
        return proxmox_service.get_config(resource_info["node"], vmid, "qemu")
    except Exception as exc:
        logger.error("Failed to read config for %s: %s", vmid, exc)
        raise ProxmoxError(t("resource_settings.readConfigFailed", vmid=vmid))


def _qemu_authorized_keys(config: dict[str, Any]) -> list[str]:
    raw = config.get("sshkeys")
    if not raw:
        return []
    return _split_keys(unquote(str(raw)))


def _qemu_write_keys(resource_info: dict[str, Any], vmid: int, keys: list[str]) -> None:
    node = resource_info["node"]
    try:
        if keys:
            proxmox_service.update_config(
                node, vmid, "qemu", sshkeys=quote("\n".join(keys), safe="")
            )
        else:
            proxmox_service.update_config(node, vmid, "qemu", delete="sshkeys")
    except Exception as exc:
        logger.error("Failed to update sshkeys for %s: %s", vmid, exc)
        raise ProxmoxError(
            t("resource_settings.updateConfigFailed", vmid=vmid, error=exc)
        )


def _qemu_try_chpasswd(
    resource_info: dict[str, Any], vmid: int, username: str, password: str
) -> bool:
    """執行中且 agent 有回應時直接改密碼；任何失敗都只回 False（重開機後仍會生效）。"""
    if not _is_running(resource_info):
        return False
    try:
        code, _out, err = guest.exec_qemu(
            resource_info["node"],
            vmid,
            [
                "/bin/sh",
                "-c",
                f"echo {shlex.quote(f'{username}:{password}')} | chpasswd",
            ],
            timeout=30.0,
        )
    except Exception as exc:
        logger.info("VM %s: immediate chpasswd skipped: %s", vmid, exc)
        return False
    if code != 0:
        logger.info("VM %s: immediate chpasswd failed: %s", vmid, (err or "")[:200])
        return False
    return True


# ─── LXC（pct exec） ───────────────────────────────────────────────────────────


def _require_lxc_running(resource_info: dict[str, Any]) -> None:
    if not _is_running(resource_info):
        raise BadRequestError(t("resource_settings.lxcMustBeRunning"))


def _lxc_exec(resource_info: dict[str, Any], vmid: int, command: str) -> str:
    try:
        code, out, err = guest.exec_lxc(
            resource_info["node"], vmid, command, timeout=60.0
        )
    except Exception as exc:
        logger.error("CT %s exec failed: %s", vmid, exc)
        raise ProxmoxError(t("resource_settings.lxcExecFailed", vmid=vmid, error=exc))
    if code != 0:
        raise ProxmoxError(
            t(
                "resource_settings.lxcExecFailed",
                vmid=vmid,
                error=(err or out or "").strip()[:300],
            )
        )
    return out


def _lxc_authorized_keys(resource_info: dict[str, Any], vmid: int) -> list[str]:
    if not _is_running(resource_info):
        return []
    try:
        out = _lxc_exec(
            resource_info,
            vmid,
            f"cat {_LXC_AUTHORIZED_KEYS} 2>/dev/null || true",
        )
    except ProxmoxError:
        return []
    return _split_keys(out)


def _lxc_write_keys(resource_info: dict[str, Any], vmid: int, keys: list[str]) -> None:
    content = "\n".join(keys) + ("\n" if keys else "")
    script = (
        "mkdir -p /root/.ssh && chmod 700 /root/.ssh && "
        f"printf %s {shlex.quote(content)} > {_LXC_AUTHORIZED_KEYS} && "
        f"chmod 600 {_LXC_AUTHORIZED_KEYS}"
    )
    _lxc_exec(resource_info, vmid, script)


# ─── 公開操作 ─────────────────────────────────────────────────────────────────


def get_credentials(
    *, session: Session, vmid: int, resource_info: dict[str, Any]
) -> CredentialsPublic:
    db_resource = _get_db_resource(session, vmid)
    rtype = _rtype(resource_info)
    running = _is_running(resource_info)
    if rtype == "qemu":
        config = _qemu_config(resource_info, vmid)
        ciuser = config.get("ciuser")
        return CredentialsPublic(
            vmid=vmid,
            resource_type="qemu",
            running=running,
            username=str(ciuser) if ciuser else None,
            has_login_password=bool(db_resource.login_password_encrypted),
            supports_password_reset=True,
            supports_ssh_keys=True,
            requires_running=False,
            platform_public_key=db_resource.ssh_public_key,
            authorized_keys=_qemu_authorized_keys(config),
        )
    return CredentialsPublic(
        vmid=vmid,
        resource_type="lxc",
        running=running,
        username="root",
        has_login_password=bool(db_resource.login_password_encrypted),
        supports_password_reset=True,
        supports_ssh_keys=True,
        requires_running=True,
        platform_public_key=db_resource.ssh_public_key,
        authorized_keys=_lxc_authorized_keys(resource_info, vmid),
    )


def reset_password(
    *,
    session: Session,
    vmid: int,
    resource_info: dict[str, Any],
    user_id: uuid.UUID,
    password: str | None,
) -> PasswordResetResponse:
    from app.services.template.clone_service import (
        generate_login_password,  # noqa: PLC0415
    )

    db_resource = _get_db_resource(session, vmid)
    rtype = _rtype(resource_info)
    new_password = password or generate_login_password()

    if rtype == "qemu":
        config = _qemu_config(resource_info, vmid)
        node = resource_info["node"]
        try:
            proxmox_service.update_config(node, vmid, "qemu", cipassword=new_password)
        except Exception as exc:
            logger.error("Failed to set cipassword for %s: %s", vmid, exc)
            raise ProxmoxError(
                t("resource_settings.updateConfigFailed", vmid=vmid, error=exc)
            )
        username = str(config.get("ciuser") or "").strip()
        applied = bool(username) and _qemu_try_chpasswd(
            resource_info, vmid, username, new_password
        )
        message = (
            t("resource_settings.passwordAppliedNow")
            if applied
            else t("resource_settings.passwordAppliedOnReboot")
        )
    else:
        _require_lxc_running(resource_info)
        _lxc_exec(
            resource_info,
            vmid,
            f"echo {shlex.quote(f'root:{new_password}')} | chpasswd",
        )
        applied = True
        message = t("resource_settings.passwordAppliedNow")

    db_resource.login_password_encrypted = encrypt_value(new_password)
    session.add(db_resource)
    audit_service.log_action(
        session=session,
        user_id=user_id,
        vmid=vmid,
        action="credential_update",
        details=f"Login password reset on {rtype} {vmid} (applied_immediately={applied})",
    )
    session.commit()
    return PasswordResetResponse(
        vmid=vmid, password=new_password, applied_immediately=applied, message=message
    )


def regenerate_ssh_key(
    *,
    session: Session,
    vmid: int,
    resource_info: dict[str, Any],
    user_id: uuid.UUID,
) -> SshKeyRegenerateResponse:
    db_resource = _get_db_resource(session, vmid)
    rtype = _rtype(resource_info)
    old_public = db_resource.ssh_public_key
    private_pem, public_key = generate_ed25519_keypair(comment=f"SkyLab-vm{vmid}")

    if rtype == "qemu":
        config = _qemu_config(resource_info, vmid)
        keys = _qemu_authorized_keys(config)
        keys = [
            k
            for k in keys
            if not old_public or _key_identity(k) != _key_identity(old_public)
        ]
        keys.append(public_key)
        _qemu_write_keys(resource_info, vmid, _dedupe(keys))
        applied = False
        message = t("resource_settings.sshKeyAppliedOnReboot")
    else:
        _require_lxc_running(resource_info)
        keys = _lxc_authorized_keys(resource_info, vmid)
        keys = [
            k
            for k in keys
            if not old_public or _key_identity(k) != _key_identity(old_public)
        ]
        keys.append(public_key)
        _lxc_write_keys(resource_info, vmid, _dedupe(keys))
        applied = True
        message = t("resource_settings.sshKeyAppliedNow")

    db_resource.ssh_public_key = public_key
    db_resource.ssh_private_key_encrypted = encrypt_value(private_pem)
    session.add(db_resource)
    audit_service.log_action(
        session=session,
        user_id=user_id,
        vmid=vmid,
        action="credential_update",
        details=f"Platform SSH key regenerated on {rtype} {vmid} (applied_immediately={applied})",
    )
    session.commit()
    return SshKeyRegenerateResponse(
        vmid=vmid,
        ssh_public_key=public_key,
        ssh_private_key=private_pem,
        applied_immediately=applied,
        message=message,
    )


def _current_keys(resource_info: dict[str, Any], vmid: int, rtype: str) -> list[str]:
    if rtype == "qemu":
        return _qemu_authorized_keys(_qemu_config(resource_info, vmid))
    _require_lxc_running(resource_info)
    return _lxc_authorized_keys(resource_info, vmid)


def _write_keys(
    resource_info: dict[str, Any], vmid: int, rtype: str, keys: list[str]
) -> bool:
    """回傳是否立即生效。"""
    if rtype == "qemu":
        _qemu_write_keys(resource_info, vmid, keys)
        return False
    _lxc_write_keys(resource_info, vmid, keys)
    return True


def add_authorized_key(
    *,
    session: Session,
    vmid: int,
    resource_info: dict[str, Any],
    user_id: uuid.UUID,
    public_key: str,
) -> AuthorizedKeysResponse:
    _get_db_resource(session, vmid)
    rtype = _rtype(resource_info)
    keys = _current_keys(resource_info, vmid, rtype)
    if any(_key_identity(k) == _key_identity(public_key) for k in keys):
        raise BadRequestError(t("resource_settings.keyAlreadyAuthorized"))
    keys.append(public_key)
    applied = _write_keys(resource_info, vmid, rtype, _dedupe(keys))
    audit_service.log_action(
        session=session,
        user_id=user_id,
        vmid=vmid,
        action="credential_update",
        details=f"Authorized key added on {rtype} {vmid}: {_key_identity(public_key)[:80]}",
    )
    return AuthorizedKeysResponse(
        vmid=vmid,
        authorized_keys=_dedupe(keys),
        applied_immediately=applied,
        message=(
            t("resource_settings.sshKeyAppliedNow")
            if applied
            else t("resource_settings.sshKeyAppliedOnReboot")
        ),
    )


def remove_authorized_key(
    *,
    session: Session,
    vmid: int,
    resource_info: dict[str, Any],
    user_id: uuid.UUID,
    public_key: str,
) -> AuthorizedKeysResponse:
    db_resource = _get_db_resource(session, vmid)
    rtype = _rtype(resource_info)
    ident = _key_identity(public_key)
    if (
        db_resource.ssh_public_key
        and _key_identity(db_resource.ssh_public_key) == ident
    ):
        raise BadRequestError(t("resource_settings.cannotRemovePlatformKey"))
    keys = _current_keys(resource_info, vmid, rtype)
    remaining = [k for k in keys if _key_identity(k) != ident]
    if len(remaining) == len(keys):
        raise NotFoundError(t("resource_settings.keyNotFound"))
    applied = _write_keys(resource_info, vmid, rtype, remaining)
    audit_service.log_action(
        session=session,
        user_id=user_id,
        vmid=vmid,
        action="credential_update",
        details=f"Authorized key removed on {rtype} {vmid}: {ident[:80]}",
    )
    return AuthorizedKeysResponse(
        vmid=vmid,
        authorized_keys=remaining,
        applied_immediately=applied,
        message=(
            t("resource_settings.sshKeyAppliedNow")
            if applied
            else t("resource_settings.sshKeyAppliedOnReboot")
        ),
    )


__all__ = [
    "add_authorized_key",
    "get_credentials",
    "regenerate_ssh_key",
    "remove_authorized_key",
    "reset_password",
]
