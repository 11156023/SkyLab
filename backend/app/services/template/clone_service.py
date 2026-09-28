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
import re
import shlex
import time
import uuid
from datetime import date
from typing import Any
from urllib.parse import quote

from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from app.core.authorizers import require_template_manage
from app.core.db import engine
from app.core.i18n import t
from app.core.security import decrypt_value, encrypt_value
from app.exceptions import BadRequestError, ConflictError, NotFoundError
from app.infrastructure.proxmox import (
    get_connection_id_for_node,
    get_proxmox_settings_for_node,
)
from app.infrastructure.proxmox import operations as proxmox_ops
from app.infrastructure.queue import enqueue_task, report_progress
from app.infrastructure.ssh.client import generate_ed25519_keypair
from app.models import TaskRecord, User, VMTemplate, VMTemplateStatus
from app.repositories import resource as resource_repo
from app.schemas.template import TemplateCloneRequest
from app.services.network import firewall_service, ip_management_service, nic_config
from app.services.resource import quota_service
from app.services.template import template_service
from app.utils.hostname import to_punycode_hostname
from app.utils.login_password import (
    generate_login_password,
)

logger = logging.getLogger(__name__)

TASK_CLONE = "template.clone"

_LXC_PASSWORD_ATTEMPTS = 6
_LXC_PASSWORD_RETRY_SECONDS = 5.0


# ---------------------------------------------------------------------------
# 請求端：校驗 + 入列
# ---------------------------------------------------------------------------

# DNS label 上限 63；批次時要留 "-NN" 四個字元給序號
_LABEL_MAX = 63
_BATCH_LABEL_MAX = 59


def _slugify_template_name(name: str, max_ace_len: int) -> str:
    """把範本名稱（自由文字）轉成單一合法 hostname label。

    保留 Unicode 字母與數字（之後轉 Punycode），其餘字元一律換成 ``-``；
    從尾端逐字截短，直到 ACE 形式不超過 ``max_ace_len``。
    """
    slug = re.sub(r"[^\w-]+", "-", name.lower()).replace("_", "-")
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    while slug:
        try:
            if len(to_punycode_hostname(slug)) <= max_ace_len:
                return slug
        except ValueError:
            pass  # 單一 label 轉完超過 63 字元時 to_punycode_hostname 會丟錯
        slug = slug[:-1].rstrip("-")
    return "vm"


def _build_hostnames(
    base: str | None, template_name: str, count: int
) -> list[str]:
    if not base:
        max_len = _LABEL_MAX if count == 1 else _BATCH_LABEL_MAX
        base = _slugify_template_name(template_name, max_len)
    try:
        hostname = to_punycode_hostname(base)
    except ValueError as exc:
        raise BadRequestError(t("clone.invalidHostname")) from exc
    if count == 1:
        return [hostname]
    # 批量時加序號，並保留 63 字元上限；截斷後不可留下結尾的 - 或 .
    prefix = hostname[:_BATCH_LABEL_MAX].rstrip("-.")
    return [f"{prefix}-{i + 1:02d}" for i in range(count)]


async def request_clone(
    *,
    session: Session,
    user: User,
    template_id: uuid.UUID,
    data: TemplateCloneRequest,
) -> list[TaskRecord]:
    # 校驗（DB、GPU mapping、配額）都是同步查詢，丟到 threadpool；
    # event loop 上只留入列
    template, payloads = await run_in_threadpool(
        _prepare_clone, session, user, template_id, data
    )
    records: list[TaskRecord] = []
    for payload in payloads:
        record = await enqueue_task(
            session=session,
            task_type=TASK_CLONE,
            user_id=user.id,
            template_id=template.id,
            payload=payload,
        )
        records.append(record)
    return records


def _prepare_clone(
    session: Session,
    user: User,
    template_id: uuid.UUID,
    data: TemplateCloneRequest,
) -> tuple[VMTemplate, list[dict[str, Any]]]:
    """request_clone 的同步部分：權限、GPU、配額校驗與每台機器的任務 payload。"""
    template = template_service.get_or_404(session, template_id)
    template_service.require_view(session, user, template)
    # 克隆開通僅限教師與管理員；學生要機器一律走申請審核流程。
    require_template_manage(user)
    if template.status != VMTemplateStatus.ready:
        raise ConflictError(
            t("clone.templateNotReady", status=template.status.value)
        )

    if data.login_password and not template.allow_password_change:
        raise BadRequestError(t("clone.passwordChangeNotAllowed"))
    if template.requires_gpu and not data.gpu_mapping_id:
        raise BadRequestError(t("clone.gpuRequired"))
    if data.gpu_mapping_id:
        if template.resource_type == "lxc":
            raise BadRequestError(t("clone.lxcGpuUnsupported"))
        from app.services.proxmox.provisioning_service import (
            _gpu_mapping_nodes,
        )

        gpu_nodes = _gpu_mapping_nodes(data.gpu_mapping_id)
        if template.node not in gpu_nodes:
            raise BadRequestError(
                t("clone.gpuNodeMismatch", node=template.node)
            )

    # 配額與其他開通路徑走同一個執法點。cores/memory 未指定時沿用範本規格，
    # 所以配額要以「實際會開出來的規格」計算，不能只看使用者填了什麼。
    spec_cores, spec_memory, spec_disk = template_service.resolve_effective_spec(
        template
    )
    quota_service.check_quota(
        session,
        user.id,
        delta_cores=(data.cores or spec_cores or 0) * data.count,
        delta_memory_mb=(data.memory or spec_memory or 0) * data.count,
        delta_disk_gb=(spec_disk or 0) * data.count,
        delta_instances=data.count,
    )

    hostnames = _build_hostnames(data.hostname, template.name, data.count)
    payloads = [
        {
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
                encrypt_value(data.login_password) if data.login_password else None
            ),
            "gpu_mapping_id": data.gpu_mapping_id,
            "gpu_mdev_profile": data.gpu_mdev_profile,
        }
        for hostname in hostnames
    ]
    return template, payloads


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
        # A VMID collision is not a linked-clone capability failure.  Retrying
        # full clone with the same ID would fail again, and the old cleanup
        # path could delete the other worker's already-created machine.
        if _is_vmid_collision(exc):
            logger.error(
                "Proxmox rejected clone %s -> %s because the VMID already exists; "
                "skip fallback/cleanup and let the caller roll back its own reservation",
                template_vmid,
                new_vmid,
            )
            raise
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


def _is_vmid_collision(exc: Exception) -> bool:
    """判斷 PVE 的「CT/VM <id> already exists」而非一般克隆失敗。"""
    return bool(
        re.search(
            r"\b(?:ct|vm)\s+\d+\s+already\s+exists\b",
            str(exc),
            flags=re.IGNORECASE,
        )
    )


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
        "net0": nic_config.qemu_net0(net_cfg),
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
        _grow_qemu_boot_disk(node=node, vmid=vmid, disk_gb=disk)


def _grow_qemu_boot_disk(*, node: str, vmid: int, disk_gb: int) -> None:
    """把克隆機的開機磁碟放大到 disk_gb；已經夠大就不動。

    開機磁碟不一定是 scsi0（virtio0／sata0／ide0 的範本也存在），寫死
    scsi0 會讓 PVE 找不到磁碟而整台回滾；PVE 也不接受縮小磁碟，要求的
    大小不大於現況時直接略過。
    """
    config = proxmox_ops.get_config(node, vmid, "qemu")
    boot_disk = template_service.qemu_boot_disk(config)
    if boot_disk is None:
        logger.warning(
            "Clone %s has no recognizable boot disk; skipping resize to %sG",
            vmid, disk_gb,
        )
        return
    disk_key, raw = boot_disk
    current_gb = template_service._parse_disk_size_gb(raw)
    if current_gb is not None and disk_gb <= current_gb:
        return
    proxmox_ops.resize_disk(node, vmid, "qemu", disk_key, f"{disk_gb}G")


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
    # LXC 無 cloud-init：PVE config API 無法在克隆後注入 SSH 金鑰，root 密碼
    # 亦只能於開機後以 pct exec 設定（見 _set_lxc_root_password）；平台公鑰
    # 於開機後以 pct exec 寫入 authorized_keys（見 inject_lxc_platform_key）。
    config_updates: dict[str, Any] = {
        "hostname": hostname,
        "net0": nic_config.lxc_net0(net_cfg, allocated_ip),
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
    密碼由 stdin 餵給 ``chpasswd``，不放進指令列 —— 指令列會出現在節點的
    ps 與 shell 紀錄裡，同一台節點上的其他人看得到。
    回傳是否成功；失敗方（呼叫端）不得記錄未生效的密碼。
    """
    return _exec_lxc_with_retry(
        node,
        vmid,
        "chpasswd",
        stdin=f"root:{password}\n",
        what="set root password",
    )


def _exec_lxc_with_retry(
    node: str,
    vmid: int,
    command: str,
    *,
    stdin: str | None = None,
    what: str,
) -> bool:
    """以 ``pct exec`` 在剛開機的容器內執行指令，容器還沒起來就重試等待。

    最多試 ``_LXC_PASSWORD_ATTEMPTS`` 次、每次間隔 ``_LXC_PASSWORD_RETRY_SECONDS``；
    全部失敗時以 ``what`` 描述記 warning 並回 False。
    """
    from app.infrastructure.proxmox import guest

    exec_kwargs: dict[str, Any] = {} if stdin is None else {"stdin": stdin}
    last_error: str = ""
    for attempt in range(_LXC_PASSWORD_ATTEMPTS):
        if attempt:
            time.sleep(_LXC_PASSWORD_RETRY_SECONDS)
        try:
            code, _out, err = guest.exec_lxc(node, vmid, command, **exec_kwargs)
        except Exception as exc:
            last_error = str(exc)
            continue
        if code == 0:
            return True
        last_error = (err or "").strip()
    logger.warning("Failed to %s for CT %d: %s", what, vmid, last_error[:300])
    return False


def _inject_lxc_platform_key(node: str, vmid: int, public_key: str) -> bool:
    """開機後以 ``pct exec`` 寫入平台公鑰（容器啟動需時，重試等待）。

    LXC 無 cloud-init，PVE config API 無法在克隆後注入 ``ssh-public-keys``，
    故在此沿用 credentials_service 的 authorized_keys 寫法直接寫檔。
    已存在則不重複追加；回傳是否成功，失敗由呼叫端記 warning（DB 仍落庫，
    管理員可用 regenerate-ssh-key 補救）。
    """
    key = public_key.strip()
    if not key:
        return False
    script = (
        "mkdir -p /root/.ssh && chmod 700 /root/.ssh && "
        "touch /root/.ssh/authorized_keys && "
        f"grep -qxF {shlex.quote(key)} /root/.ssh/authorized_keys 2>/dev/null || "
        f"printf %s {shlex.quote(key + chr(10))} >> /root/.ssh/authorized_keys; "
        "chmod 600 /root/.ssh/authorized_keys"
    )
    return _exec_lxc_with_retry(
        node, vmid, script, what="inject platform SSH key"
    )


def set_lxc_root_password(node: str, vmid: int, password: str) -> bool:
    """Public wrapper used by managed LXC start / reset paths."""
    return _set_lxc_root_password(node, vmid, password)


def inject_lxc_platform_key(node: str, vmid: int, public_key: str) -> bool:
    """Public entry point for start paths that need to sync a guest key."""
    return _inject_lxc_platform_key(node, vmid, public_key)


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

    new_vmid: int | None = None
    allocated_ip: str | None = None
    created = False
    try:
        # ``next_vmid`` 只讀 PVE 的 nextid，``allocate_free_vmid`` 另外跳過 DB
        # 已預留（IP 配發紀錄／資源列）的 VMID：排程在鎖內只把 VMID 寫進 DB
        # 就放鎖，clone 稍後才送出，這段時間 nextid 仍會回同一個號碼。
        # 把鎖一路持有到 clone 完成，才能避免不同 backend worker 在 PVE 尚未
        # 反映新 CT 前拿到同一個 VMID。IP 預留也放在同一個臨界區，失敗時再用
        # reservation key 精準回滾。
        # provisioning_service 會延遲 import 本模組，這裡也延遲 import 避免循環
        from app.services.proxmox.provisioning_service import allocate_free_vmid

        with proxmox_ops.vmid_allocation_lock():
            with Session(engine) as session:
                new_vmid = allocate_free_vmid(session)
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

            report_progress(task_id, 10)
            clone_mode = clone_with_fallback(
                node=node,
                template_vmid=template_vmid,
                new_vmid=new_vmid,
                hostname=hostname,
                resource_type=resource_type,
            )
            created = True

        # clone 已由鎖保護完成；後續 guest 重配置不再阻塞其他 VMID。
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
                from app.services.proxmox.provisioning_service import (
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
            if resource_type == "lxc":
                # 範本 LXC 無 cloud-init：開機後以 pct exec 注入平台公鑰。
                # 注入失敗僅警告（DB 仍落庫，Teacher Judge 的缺 key 檢查會過，
                # 後續可用 regenerate-ssh-key 補寫 guest 內 authorized_keys）。
                _inject_lxc_platform_key(
                    node, new_vmid, public_key
                )
        elif resource_type == "lxc":
            logger.warning(
                "CT %s not started at clone time; platform SSH key recorded in DB "
                "only; a later LXC start must sync guest authorized_keys",
                new_vmid,
            )
        report_progress(task_id, 90)

        with Session(engine) as session:
            resource_repo.create_resource(
                session=session,
                vmid=new_vmid,
                connection_id=get_connection_id_for_node(node),
                user_id=user_id,
                environment_type=environment_type or f"範本 {template_name}",
                expiry_date=expiry_date,
                template_id=template_vmid,
                ssh_private_key_encrypted=encrypt_value(private_key_pem),
                ssh_public_key=public_key,
                login_password_encrypted=(
                    encrypt_value(login_password)
                    if password_applied and login_password is not None
                    else None
                ),
                # 沒寫進機器（LXC 建立時未開機、或 chpasswd 失敗）就留作待套用，
                # 下次受管開機由 ensure_lxc_login_password 補設；不直接當成
                # 已生效的密碼顯示，避免給出一組登不進去的密碼
                login_password_pending_encrypted=(
                    encrypt_value(login_password)
                    if not password_applied and login_password is not None
                    else None
                ),
                batch_job_id=batch_job_id,
                commit=False,
            )
            ip_management_service.link_ip_to_resource(
                session,
                new_vmid,
                reservation_key=ip_reservation_key,
            )
            session.commit()
    except Exception:
        # 失敗清理：釋放 IP → 撤防火牆規則 → 刪除半成品
        if new_vmid is not None:
            try:
                with Session(engine) as cleanup_session:
                    ip_management_service.release_ip(
                        cleanup_session,
                        new_vmid,
                        restore_reservation=bool(ip_reservation_key),
                        reservation_key=ip_reservation_key,
                    )
                    cleanup_session.commit()
            except Exception:
                logger.warning("Failed to release IP for VMID %d", new_vmid)
        if created and new_vmid is not None:
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
    "inject_lxc_platform_key",
    "request_clone",
    "run_clone_task",
]
