"""統一克隆開通服務：所有「從範本開機器」都走這條路徑。

請求端（request_clone）做權限/配額校驗與任務入列；worker 端（run_clone_task）
執行 PVE 克隆：linked clone 優先、失敗自動退 full clone，克隆後重配置
hostname / IP / SSH 金鑰 / 隨機登入密碼 / 防火牆並寫入 Resource 紀錄。

登入密碼：每台克隆機各發一組隨機密碼。qemu 走 cloud-init ``cipassword``
（首次開機由 guest 內 cloud-init / cloudbase-init 套用到預設使用者）；
LXC 無 cloud-init，開機後 best-effort 以 ``pct exec chpasswd`` 設定 root
密碼，失敗則沿用範本內建憑證且不記錄密碼。
"""

from __future__ import annotations

import logging
import secrets
import shlex
import time
import uuid
from datetime import date
from typing import Any
from urllib.parse import quote

from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.core.i18n import t
from app.core.permissions import is_admin
from app.core.security import decrypt_value, encrypt_value
from app.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.infrastructure.proxmox import get_proxmox_settings_for_node
from app.infrastructure.proxmox import operations as proxmox_ops
from app.infrastructure.queue import enqueue_task, report_progress
from app.infrastructure.ssh.client import generate_ed25519_keypair
from app.models import TaskRecord, User, VMTemplate, VMTemplateStatus
from app.repositories import resource as resource_repo
from app.schemas.template import TemplateCloneRequest
from app.services.network import firewall_service, ip_management_service
from app.services.template import template_service
from app.utils.hostname import to_punycode_hostname

logger = logging.getLogger(__name__)

TASK_CLONE = "template.clone"

# 排除易混淆字元（0O1lI）的英數字母表；密碼須可在 VNC console 徒手輸入
_PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ23456789"
_PASSWORD_LENGTH = 12

_LXC_PASSWORD_ATTEMPTS = 6
_LXC_PASSWORD_RETRY_SECONDS = 5.0


def generate_login_password() -> str:
    return "".join(
        secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_LENGTH)
    )


# ---------------------------------------------------------------------------
# 請求端：校驗 + 入列
# ---------------------------------------------------------------------------

def _build_hostnames(
    base: str | None, template_name: str, count: int
) -> list[str]:
    raw = base or template_name
    hostname = to_punycode_hostname(raw)
    if count == 1:
        return [hostname]
    # 批量時加序號，並保留 63 字元上限
    return [f"{hostname[:59]}-{i + 1:02d}" for i in range(count)]


async def request_clone(
    *,
    session: Session,
    user: User,
    template_id: uuid.UUID,
    data: TemplateCloneRequest,
) -> list[TaskRecord]:
    template = template_service._get_or_404(session, template_id)
    template_service._require_view(session, user, template)
    if template.status != VMTemplateStatus.ready:
        raise ConflictError(
            t("clone.templateNotReady", status=template.status.value)
        )

    can_manage = template_service._can_manage(user)
    if data.count > 1 and not can_manage:
        raise PermissionDeniedError(t("clone.batchRequiresManager"))

    if data.login_password and not template.allow_password_change:
        raise BadRequestError(t("clone.passwordChangeNotAllowed"))
    if template.requires_gpu and not data.gpu_mapping_id:
        raise BadRequestError(t("clone.gpuRequired"))
    if data.gpu_mapping_id:
        if template.resource_type == "lxc":
            raise BadRequestError(t("clone.lxcGpuUnsupported"))
        from app.services.proxmox.provisioning_service import (  # noqa: PLC0415
            _gpu_mapping_nodes,
        )

        gpu_nodes = _gpu_mapping_nodes(data.gpu_mapping_id)
        if template.node not in gpu_nodes:
            raise BadRequestError(
                t("clone.gpuNodeMismatch", node=template.node)
            )

    if not can_manage and not is_admin(user):
        owned = len(
            resource_repo.get_resources_by_user(session=session, user_id=user.id)
        )
        limit = settings.TEMPLATE_CLONE_STUDENT_MAX_INSTANCES
        if owned + data.count > limit:
            raise ConflictError(
                t("clone.quotaExceeded", owned=owned, limit=limit)
            )

    hostnames = _build_hostnames(data.hostname, template.name, data.count)
    records: list[TaskRecord] = []
    for hostname in hostnames:
        record = await enqueue_task(
            session=session,
            task_type=TASK_CLONE,
            user_id=user.id,
            template_id=template.id,
            payload={
                "template_id": str(template.id),
                "user_id": str(user.id),
                "hostname": hostname,
                "cores": data.cores,
                "memory": data.memory,
                # 磁碟不開放調整：固定沿用範本磁碟（batch 路徑仍可帶 disk）
                "start": data.start,
                "allow_password_reset": template.allow_password_change,
                # payload 會落 DB（TaskRecord.payload），密碼必須加密存放
                "login_password_enc": (
                    encrypt_value(data.login_password)
                    if data.login_password
                    else None
                ),
                "gpu_mapping_id": data.gpu_mapping_id,
                "gpu_mdev_profile": data.gpu_mdev_profile,
            },
        )
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# worker 端：克隆 + 重配置（同步，tasks.py 以 to_thread 呼叫）
# ---------------------------------------------------------------------------

def clone_with_fallback(
    *,
    node: str,
    template_vmid: int,
    new_vmid: int,
    hostname: str,
    resource_type: proxmox_ops.ResourceType,
    full_kwargs: dict[str, Any] | None = None,
) -> str:
    """linked clone 優先，失敗退 full clone。回傳實際模式（linked/full）。

    ``full_kwargs`` 只在退 full clone 時併入（例如指定 storage——
    linked clone 必須與範本同 storage，不能帶該參數）。
    """
    pool = get_proxmox_settings_for_node(node).pool_name
    name_key = "hostname" if resource_type == "lxc" else "name"
    clone_fn = (
        proxmox_ops.clone_lxc if resource_type == "lxc" else proxmox_ops.clone_vm
    )
    base_config: dict[str, Any] = {
        "newid": new_vmid,
        name_key: hostname,
        "pool": pool,
    }
    try:
        clone_fn(node, template_vmid, full=0, **base_config)
        return "linked"
    except Exception as exc:
        logger.warning(
            "Linked clone of template %s -> %s failed (%s); falling back to full clone",
            template_vmid,
            new_vmid,
            exc,
        )
        # linked clone 失敗可能留下殘骸，先盡力清掉再以同 VMID full clone
        try:
            from app.services.proxmox import provisioning_service

            provisioning_service.cleanup_provisioned_resource(new_vmid)
        except Exception:
            # 清理殘留失敗不阻擋重試克隆，僅留 debug 紀錄
            logger.debug(
                "Cleanup of leftover resource %s before retry failed",
                new_vmid,
                exc_info=True,
            )
        clone_fn(node, template_vmid, full=1, **base_config, **(full_kwargs or {}))
        return "full"


def _reconfigure_qemu(
    *,
    node: str,
    vmid: int,
    hostname: str,
    cores: int | None,
    memory: int | None,
    disk: int | None,
    public_key: str,
    login_password: str | None,
    net_cfg: dict[str, Any],
    allocated_ip: str,
) -> None:
    config_updates: dict[str, Any] = {
        "name": hostname,
        "sshkeys": quote(public_key, safe=""),
        "ciupgrade": 0,
        "net0": f"virtio,bridge={net_cfg['bridge_name']},firewall=1",
        "ipconfig0": (
            f"ip={allocated_ip}/{net_cfg['prefix_len']},gw={net_cfg['gateway']}"
        ),
    }
    if login_password is not None:
        # cloud-init 首次開機套用密碼（PVE 存 hash）；範本禁止改密碼時
        # 完全不帶 cipassword，沿用範本內建帳密
        config_updates["cipassword"] = login_password
    if cores:
        config_updates["cores"] = cores
    if memory:
        config_updates["memory"] = memory
    if net_cfg.get("dns_servers"):
        config_updates["nameserver"] = net_cfg["dns_servers"]
    proxmox_ops.update_config(node, vmid, "qemu", **config_updates)
    if disk:
        proxmox_ops.resize_disk(node, vmid, "qemu", "scsi0", f"{disk}G")


def _reconfigure_lxc(
    *,
    node: str,
    vmid: int,
    hostname: str,
    cores: int | None,
    memory: int | None,
    net_cfg: dict[str, Any],
    allocated_ip: str,
) -> None:
    # LXC 無 cloud-init：SSH 金鑰無法在克隆後注入；root 密碼於開機後
    # 以 pct exec 設定（見 _set_lxc_root_password），失敗才沿用範本內建憑證
    config_updates: dict[str, Any] = {
        "hostname": hostname,
        "net0": (
            f"name=eth0,bridge={net_cfg['bridge_name']},"
            f"ip={allocated_ip}/{net_cfg['prefix_len']},"
            f"gw={net_cfg['gateway']},firewall=1"
        ),
    }
    if cores:
        config_updates["cores"] = cores
    if memory:
        config_updates["memory"] = memory
    if net_cfg.get("dns_servers"):
        config_updates["nameserver"] = net_cfg["dns_servers"]
    proxmox_ops.update_config(node, vmid, "lxc", **config_updates)


def _set_lxc_root_password(node: str, vmid: int, password: str) -> bool:
    """開機後以 ``pct exec chpasswd`` 設定 root 密碼（容器啟動需時，重試等待）。

    LXC config API 不接受 password（僅限建立時），只能進容器內改。
    回傳是否成功；失敗方（呼叫端）不得記錄未生效的密碼。
    """
    from app.infrastructure.proxmox import guest

    command = f"echo {shlex.quote(f'root:{password}')} | chpasswd"
    last_error: str = ""
    for attempt in range(_LXC_PASSWORD_ATTEMPTS):
        if attempt:
            time.sleep(_LXC_PASSWORD_RETRY_SECONDS)
        try:
            code, _out, err = guest.exec_lxc(node, vmid, command)
        except Exception as exc:
            last_error = str(exc)
            continue
        if code == 0:
            return True
        last_error = (err or "").strip()
    logger.warning(
        "Failed to set root password for CT %d: %s", vmid, last_error[:300]
    )
    return False


def _parse_expiry(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def run_clone_task(task_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
    """克隆一台：分配 IP → clone（linked→full）→ 重配置 → 防火牆 → Resource 紀錄。

    選用 payload 鍵（batch provision 走同一條路徑時傳入）：
    batch_job_id / environment_type / expiry_date。
    """
    template_id = uuid.UUID(payload["template_id"])
    user_id = uuid.UUID(payload["user_id"])
    hostname = str(payload["hostname"])
    cores = payload.get("cores")
    memory = payload.get("memory")
    disk = payload.get("disk")
    start = bool(payload.get("start", True))
    allow_password_reset = bool(payload.get("allow_password_reset", True))
    login_password_enc = payload.get("login_password_enc")
    gpu_mapping_id = payload.get("gpu_mapping_id")
    gpu_mdev_profile = payload.get("gpu_mdev_profile")
    raw_batch = payload.get("batch_job_id")
    batch_job_id = uuid.UUID(str(raw_batch)) if raw_batch else None
    environment_type = payload.get("environment_type")
    expiry_date = _parse_expiry(payload.get("expiry_date"))
    ip_reservation_key = payload.get("ip_reservation_key")

    with Session(engine) as session:
        template = session.get(VMTemplate, template_id)
        if template is None or template.status != VMTemplateStatus.ready:
            raise NotFoundError(t("clone.templateMissingOrNotReady"))
        template_vmid = template.pve_vmid
        template_name = template.name
        node = template.node
        resource_type: proxmox_ops.ResourceType = (
            "lxc" if template.resource_type == "lxc" else "qemu"
        )
        cores = cores or template.default_cores
        memory = memory or template.default_memory
        disk = disk or template.default_disk

        new_vmid = proxmox_ops.next_vmid()
        net_cfg = ip_management_service.get_network_config_for_vm(session)
        purpose = "lxc" if resource_type == "lxc" else "vm"
        allocated_ip = ip_management_service.allocate_ip(
            session,
            new_vmid,
            purpose,
            reservation_key=ip_reservation_key,
        )
        # 先提交 IP 分配，避免克隆期間（可能數分鐘）併發任務撞 IP
        session.commit()

    created = False
    clone_mode = "linked"
    try:
        report_progress(task_id, 10)
        clone_mode = clone_with_fallback(
            node=node,
            template_vmid=template_vmid,
            new_vmid=new_vmid,
            hostname=hostname,
            resource_type=resource_type,
        )
        created = True
        report_progress(task_id, 60)

        private_key_pem, public_key = generate_ed25519_keypair()
        # 範本禁止改密碼時完全不重設，沿用範本內建帳密；
        # 允許時優先用使用者自訂密碼，未填才發隨機密碼
        login_password: str | None = None
        if allow_password_reset:
            login_password = (
                decrypt_value(str(login_password_enc))
                if login_password_enc
                else generate_login_password()
            )
        password_applied = False
        if resource_type == "qemu":
            _reconfigure_qemu(
                node=node,
                vmid=new_vmid,
                hostname=hostname,
                cores=cores,
                memory=memory,
                disk=disk,
                public_key=public_key,
                login_password=login_password,
                net_cfg=net_cfg,
                allocated_ip=allocated_ip,
            )
            # cipassword 已寫入 config，首次開機由 cloud-init 套用
            password_applied = login_password is not None
            if gpu_mapping_id:
                # 容量與 vGPU 規格以掛載當下重新驗證（與申請流程同一套檢查）
                from app.services.proxmox.provisioning_service import (  # noqa: PLC0415
                    _build_gpu_hostpci,
                )

                proxmox_ops.update_config(
                    node,
                    new_vmid,
                    "qemu",
                    hostpci0=_build_gpu_hostpci(
                        str(gpu_mapping_id),
                        str(gpu_mdev_profile) if gpu_mdev_profile else None,
                    ),
                )
        else:
            _reconfigure_lxc(
                node=node,
                vmid=new_vmid,
                hostname=hostname,
                cores=cores,
                memory=memory,
                net_cfg=net_cfg,
                allocated_ip=allocated_ip,
            )
        report_progress(task_id, 75)

        firewall_service.setup_default_rules(node, new_vmid, resource_type)
        if start:
            proxmox_ops.control(node, new_vmid, resource_type, "start")
            if resource_type == "lxc" and login_password is not None:
                password_applied = _set_lxc_root_password(
                    node, new_vmid, login_password
                )
        report_progress(task_id, 90)

        with Session(engine) as session:
            resource_repo.create_resource(
                session=session,
                vmid=new_vmid,
                user_id=user_id,
                environment_type=environment_type or f"範本 {template_name}",
                expiry_date=expiry_date,
                template_id=template_vmid,
                ssh_private_key_encrypted=(
                    encrypt_value(private_key_pem)
                    if resource_type == "qemu"
                    else None
                ),
                ssh_public_key=public_key if resource_type == "qemu" else None,
                login_password_encrypted=(
                    encrypt_value(login_password)
                    if password_applied and login_password is not None
                    else None
                ),
                batch_job_id=batch_job_id,
            )
    except Exception:
        # 失敗清理：釋放 IP → 撤防火牆規則 → 刪除半成品
        try:
            with Session(engine) as cleanup_session:
                ip_management_service.release_ip(
                    cleanup_session,
                    new_vmid,
                    restore_reservation=bool(ip_reservation_key),
                )
                cleanup_session.commit()
        except Exception:
            logger.warning("Failed to release IP for VMID %d", new_vmid)
        if created:
            try:
                rules = firewall_service.get_vm_firewall_rules(
                    node, new_vmid, resource_type
                )
                for rule in sorted(
                    rules, key=lambda r: r.get("pos", 0), reverse=True
                ):
                    pos = rule.get("pos")
                    if pos is not None:
                        try:
                            firewall_service.delete_rule_by_pos(
                                node, new_vmid, resource_type, int(pos)
                            )
                        except Exception:
                            # 單條規則刪除失敗不影響其他規則的回滾
                            logger.debug(
                                "Rollback of firewall rule pos=%s on %s failed",
                                pos,
                                new_vmid,
                                exc_info=True,
                            )
            except Exception:
                # 回滾階段的清單查詢失敗只能略過，VM 隨後會被整個銷毀
                logger.debug(
                    "Rollback of firewall rules on %s failed", new_vmid, exc_info=True
                )
            try:
                from app.services.proxmox import provisioning_service

                provisioning_service.cleanup_provisioned_resource(new_vmid)
            except Exception:
                logger.warning("Failed to clean up half-cloned VMID %d", new_vmid)
        raise

    return {
        "vmid": new_vmid,
        "clone_mode": clone_mode,
        "ip": allocated_ip,
        "hostname": hostname,
        "login_password_set": password_applied,
    }


__all__ = [
    "TASK_CLONE",
    "clone_with_fallback",
    "generate_login_password",
    "request_clone",
    "run_clone_task",
]
