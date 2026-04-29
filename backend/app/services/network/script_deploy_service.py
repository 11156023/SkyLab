"""服務模板腳本部署服務

透過 SSH 連線到 Proxmox 節點，下載並以無人值守方式執行 community-scripts 腳本。
部署完成後自動刪除腳本；失敗時完全回滾（銷毀已建立的容器）。
"""

from __future__ import annotations

import base64
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlmodel import Session

from app.infrastructure.proxmox import (
    get_active_host,
    get_proxmox_api,
    get_proxmox_settings,
)
from app.infrastructure.ssh import (
    create_password_client,
    exec_command,
    exec_command_streaming,
)
from app.infrastructure.worker import ExpiringStore
from app.models.proxmox_config import ProxmoxConfig
from app.services.network import firewall_service
from app.services.proxmox import proxmox_service

logger = logging.getLogger(__name__)

GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main"
)


# ---------------------------------------------------------------------------
# In-memory task tracking
# ---------------------------------------------------------------------------


@dataclass
class DeploymentTask:
    task_id: str
    status: str = "running"  # running | completed | failed
    progress: str = "初始化中…"
    vmid: int | None = None
    message: str | None = None
    error: str | None = None
    output: str = ""
    user_id: str = ""
    template_name: str = ""
    template_slug: str = ""
    script_path: str | None = None
    hostname: str | None = None
    request_id: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    _last_persist_at: float = 0.0


# Active deploys keyed by VM request id — used to refuse duplicate launches
_ACTIVE_BY_REQUEST: dict[str, str] = {}
_ACTIVE_LOCK = threading.Lock()

# Cancel events keyed by task_id — set by cancel_task() to interrupt running deploys
_CANCEL_EVENTS: dict[str, threading.Event] = {}
_CANCEL_LOCK = threading.Lock()


def _make_cancel_event(task_id: str) -> threading.Event:
    with _CANCEL_LOCK:
        ev = _CANCEL_EVENTS.get(task_id)
        if ev is None:
            ev = threading.Event()
            _CANCEL_EVENTS[task_id] = ev
    return ev


def _drop_cancel_event(task_id: str) -> None:
    with _CANCEL_LOCK:
        _CANCEL_EVENTS.pop(task_id, None)


def _is_cancelled(task_id: str) -> bool:
    with _CANCEL_LOCK:
        ev = _CANCEL_EVENTS.get(task_id)
    return bool(ev and ev.is_set())


def _try_claim_request(request_id: str | None, task_id: str) -> bool:
    """Reserve a request_id for this task. Returns False if already in flight."""
    if not request_id:
        return True
    with _ACTIVE_LOCK:
        existing = _ACTIVE_BY_REQUEST.get(request_id)
        if existing and existing != task_id:
            return False
        _ACTIVE_BY_REQUEST[request_id] = task_id
        return True


def _release_request(request_id: str | None, task_id: str) -> None:
    if not request_id:
        return
    with _ACTIVE_LOCK:
        if _ACTIVE_BY_REQUEST.get(request_id) == task_id:
            _ACTIVE_BY_REQUEST.pop(request_id, None)



# 已完成/失敗任務的保留時間
_TASK_TTL = timedelta(hours=2)
_TASK_STORE = ExpiringStore[DeploymentTask](
    ttl=_TASK_TTL,
    is_expired=lambda task, now, ttl: task.status in {"completed", "failed"}
    and now - task.created_at > ttl,
)

# DB 持久化節流（秒）—— running 狀態下最快 N 秒寫一次，
# status 轉為 completed/failed 時一律強制寫入
_PERSIST_MIN_INTERVAL_SEC = 2.0


def _persist_task(task: DeploymentTask, *, force: bool = False) -> None:
    """將任務狀態/輸出寫入資料庫。

    - force=True 會忽略節流（用於 status 變更、終結狀態）
    - 任何 DB 錯誤都只記 log、不影響部署流程
    """
    import time

    terminal = task.status in {"completed", "failed"}
    now = time.monotonic()
    if not force and not terminal:
        if now - task._last_persist_at < _PERSIST_MIN_INTERVAL_SEC:
            return

    try:
        from sqlmodel import Session, select

        from app.core.db import engine  # 延遲匯入避免循環
        from app.models.base import get_datetime_utc
        from app.models.script_deploy_log import ScriptDeployLog

        user_uuid: uuid.UUID | None = None
        if task.user_id:
            try:
                user_uuid = uuid.UUID(task.user_id)
            except ValueError:
                user_uuid = None

        with Session(engine) as session:
            existing = session.exec(
                select(ScriptDeployLog).where(ScriptDeployLog.task_id == task.task_id)
            ).first()
            if existing is None:
                existing = ScriptDeployLog(
                    task_id=task.task_id,
                    user_id=user_uuid,
                    template_slug=task.template_slug or task.template_name or "unknown",
                    template_name=task.template_name or None,
                    script_path=task.script_path,
                    hostname=task.hostname,
                )
                session.add(existing)

            existing.vmid = task.vmid
            existing.status = task.status
            existing.progress = task.progress
            existing.message = task.message
            existing.error = task.error
            existing.output = task.output
            existing.updated_at = get_datetime_utc()
            if terminal and existing.completed_at is None:
                existing.completed_at = get_datetime_utc()
            session.commit()
        task._last_persist_at = now
    except Exception:
        logger.exception("Persist ScriptDeployLog failed (task_id=%s)", task.task_id)


def _store_task(task: DeploymentTask) -> None:
    _TASK_STORE.upsert(task.task_id, task)
    _persist_task(task)


def get_task(task_id: str) -> DeploymentTask | None:
    return _TASK_STORE.get(task_id)


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------


def _ssh_connect():
    """建立到 Proxmox 節點的 SSH 連線。"""
    cfg = get_proxmox_settings()
    host = get_active_host()
    ssh_user = cfg.user.split("@")[0] if "@" in cfg.user else cfg.user

    return create_password_client(
        host,
        22,
        ssh_user,
        cfg.password,
        timeout=30,
        host_key_policy="warning",
    )


def _ssh_exec(client, command: str, timeout: int = 600, retries: int = 2) -> tuple[int, str, str]:
    """Execute an SSH command with limited retry on transient failure."""
    import time as _time
    last_exc: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            return exec_command(client, command, timeout=timeout)
        except Exception as exc:
            last_exc = exc
            if attempt >= retries:
                break
            wait = 2 ** attempt
            logger.warning(
                "SSH command failed (attempt %d/%d): %s — retrying in %ds",
                attempt + 1, retries + 1, exc, wait,
            )
            _time.sleep(wait)
    raise last_exc if last_exc else RuntimeError("SSH exec failed")


def _ssh_exec_streaming(
    client,
    command: str,
    task: DeploymentTask,
    timeout: int = 900,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str, str]:
    stdout_chunks: list[str] = []

    def _on_stdout(chunk: str) -> None:
        stdout_chunks.append(chunk)
        task.output = "".join(stdout_chunks)
        _store_task(task)

    return exec_command_streaming(
        client,
        command,
        timeout=timeout,
        on_stdout=_on_stdout,
        cancel_event=cancel_event,
    )


# ---------------------------------------------------------------------------
# VMID detection
# ---------------------------------------------------------------------------


def _get_all_vmids() -> set[int]:
    """取得目前所有 VM/CT 的 VMID 集合（不限 pool）。"""
    try:
        proxmox = get_proxmox_api()
        resources = proxmox.cluster.resources.get(type="vm")
        return {r["vmid"] for r in resources}
    except Exception as e:
        # 不可 silent fallback：PVE 查詢失敗時不能默默回空集，
        # 否則 _find_new_vmid 會認為沒有新 VM 而變成部署失敗難切
        logger.error(
            "_get_all_vmids 查詢 PVE cluster resources 失敗，將影響部署完成後的 VMID 偵測: %s",
            e,
        )
        raise


def _find_new_vmid(before: set[int]) -> int | None:
    """比較部署前後的 VMID 差異，找出新建的容器。"""
    after = _get_all_vmids()
    new_ids = after - before
    if new_ids:
        return max(new_ids)
    return None


def _find_vmid_by_hostname(hostname: str) -> int | None:
    """透過 hostname 在所有叢集資源中尋找 VMID。"""
    try:
        proxmox = get_proxmox_api()
        all_resources = proxmox.cluster.resources.get(type="vm")
        for r in all_resources:
            if r.get("name") == hostname:
                return r["vmid"]
    except Exception:
        logger.warning("透過 hostname 尋找 VMID 失敗: %s", hostname)
    return None


# ---------------------------------------------------------------------------
# Post-deployment setup
# ---------------------------------------------------------------------------


def _find_resource_any(vmid: int) -> dict | None:
    """不限 pool 地查找資源，回傳含 node 和 type 的 dict。"""
    try:
        proxmox = get_proxmox_api()
        for r in proxmox.cluster.resources.get(type="vm"):
            if r["vmid"] == vmid:
                return r
    except Exception as e:
        logger.warning(
            "_find_resource_any(vmid=%s) 查詢 cluster resources 失敗: %s",
            vmid, e,
        )
    return None


def _add_to_pool(vmid: int) -> None:
    """將容器加入 Campus Cloud Pool。"""
    pool_name = get_proxmox_settings().pool_name
    proxmox = get_proxmox_api()
    proxmox.pools(pool_name).put(vms=str(vmid))
    logger.info("已將 VMID=%s 加入 Pool %s", vmid, pool_name)


def _clear_description(vmid: int) -> None:
    """清除 community-scripts 自動產生的 description/notes。"""
    resource = _find_resource_any(vmid)
    if resource is None:
        logger.warning("找不到 VMID=%s 的資源，略過清除 description", vmid)
        return
    node = resource["node"]
    rtype = resource["type"]
    proxmox_service.update_config(node, vmid, rtype, description="")
    logger.info("已清除 VMID=%s 的 description", vmid)


def _apply_default_firewall(vmid: int) -> None:
    """對新部署的容器套用預設防火牆規則。"""
    resource = _find_resource_any(vmid)
    if resource is None:
        logger.warning("找不到 VMID=%s 的資源，略過防火牆設定", vmid)
        return
    node = resource["node"]
    rtype = resource["type"]  # "lxc" or "qemu"
    firewall_service.setup_default_rules(node, vmid, rtype)
    logger.info("已對 VMID=%s (%s) 套用預設防火牆規則", vmid, rtype)


def _enforce_static_network(vmid: int, net_config: dict) -> None:
    """部署完成後強制套用我們預先分配的靜態 IP 到容器 net0。

    流程：
      1. 透過 PVE API 把 net0 改成靜態 IP（pct set net0=...）
      2. 停掉容器（若還在跑）
      3. SSH 到 PVE 節點，用 `pct exec` 直接覆寫容器內 /etc/network/interfaces
         （Debian/Ubuntu）以及 netplan（如果有），確保 OS 內部不會用 DHCP 蓋掉
      4. 重新啟動容器
      5. 驗證實際 IP

    任何步驟失敗會 raise，caller 應視為非致命但要記錄到 task.message。
    """
    ip_cidr = net_config.get("ip_cidr")
    if not ip_cidr:
        return

    resource = _find_resource_any(vmid)
    if resource is None:
        raise RuntimeError(f"找不到 VMID={vmid} 的資源，無法套用靜態 IP")

    node = resource["node"]
    rtype = resource["type"]
    if rtype != "lxc":
        return

    # Verify first: if community-script's var_net already configured the IP, skip the
    # disruptive reconfigure (which would stop/start the container).
    ip_only = ip_cidr.split("/", 1)[0]
    try:
        verify_cmd = (
            f"pct exec {vmid} -- ip -4 -o addr show 2>/dev/null "
            "| awk '{print $4}' | cut -d/ -f1 || true"
        )
        client = _ssh_connect()
        try:
            _ec, out, _err = _ssh_exec(client, verify_cmd, timeout=20)
        finally:
            try:
                client.close()
            except Exception:
                pass
        actual = (out or "").strip().split()
        if ip_only in actual:
            logger.info(
                "VMID=%s 已具備預期 IP %s（由 var_net 設定）跳過重新配置",
                vmid, ip_only,
            )
            return
        logger.info(
            "VMID=%s 目前 IP=%s，預期 %s，將強制重新套用靜態網路",
            vmid, actual, ip_only,
        )
    except Exception as exc:
        logger.warning("驗證現有 IP 失敗，將進行強制重新配置: %s", exc)

    bridge = net_config.get("bridge") or "vmbr0"
    gateway = net_config.get("gateway") or ""
    nameserver = net_config.get("nameserver") or ""

    ip_only, _, prefix = ip_cidr.partition("/")
    prefix = prefix or "24"

    # 1) pct set net0 via PVE API（先做，這會寫到 /etc/pve/lxc/<vmid>.conf）
    net0_parts = ["name=eth0", f"bridge={bridge}", f"ip={ip_cidr}"]
    if gateway:
        net0_parts.append(f"gw={gateway}")
    net0_parts.append("firewall=1")
    net0 = ",".join(net0_parts)
    updates: dict = {"net0": net0}
    if nameserver:
        updates["nameserver"] = nameserver

    try:
        proxmox_service.update_config(node, vmid, rtype, **updates)
        logger.info("已寫入 net0=%s 到 VMID=%s 設定檔", net0, vmid)
    except Exception as exc:
        raise RuntimeError(f"pct set net0 失敗: {exc}") from exc

    # 2-5) 停容器、寫 OS 內部設定、重啟、驗證 — 全部透過 SSH 在 PVE 上執行
    interfaces_content = (
        "auto lo\n"
        "iface lo inet loopback\n"
        "\n"
        "auto eth0\n"
        f"iface eth0 inet static\n"
        f"    address {ip_only}/{prefix}\n"
    )
    if gateway:
        interfaces_content += f"    gateway {gateway}\n"
    if nameserver:
        ns_list = nameserver.replace(",", " ").split()
        if ns_list:
            interfaces_content += f"    dns-nameservers {' '.join(ns_list)}\n"

    iface_b64 = base64.b64encode(interfaces_content.encode("utf-8")).decode("ascii")

    # 一次性在 PVE 節點上執行：stop → 覆寫 interfaces / 移除 netplan / 移除 systemd-networkd dhcp → start → 取得 IP
    remote_script = f"""#!/bin/bash
set -e
CTID={vmid}
IFACE_B64='{iface_b64}'

# 強制停止
pct stop "$CTID" 2>/dev/null || true
sleep 2

# 啟動到 ready 狀態才能 pct exec？不，pct exec 需要 running。先寫檔再啟動。
pct start "$CTID"

# 等 CT 起來
for i in $(seq 1 20); do
  if pct exec "$CTID" -- /bin/true 2>/dev/null; then
    break
  fi
  sleep 1
done

# 覆寫 /etc/network/interfaces
echo "$IFACE_B64" | base64 -d | pct exec "$CTID" -- tee /etc/network/interfaces >/dev/null

# 移除可能干擾的 netplan / cloud-init 網路設定
pct exec "$CTID" -- sh -c 'rm -f /etc/netplan/*.yaml /etc/netplan/*.yml 2>/dev/null || true'
pct exec "$CTID" -- sh -c 'rm -f /etc/systemd/network/*.network 2>/dev/null || true'
pct exec "$CTID" -- sh -c '[ -d /etc/cloud/cloud.cfg.d ] && echo "network: {{config: disabled}}" > /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg || true'

# 重啟網路：先嘗試 ifdown/ifup，失敗就重啟容器
pct exec "$CTID" -- sh -c 'ifdown eth0 2>/dev/null; ifup eth0 2>/dev/null' || true

# 不論成不成功都重啟 CT 確保乾淨套用
pct reboot "$CTID" 2>/dev/null || (pct stop "$CTID" 2>/dev/null; sleep 2; pct start "$CTID")

# 等 CT 起來再回報 IP
for i in $(seq 1 30); do
  if pct exec "$CTID" -- /bin/true 2>/dev/null; then
    break
  fi
  sleep 1
done
pct exec "$CTID" -- hostname -I 2>/dev/null || true
"""

    client = None
    try:
        client = _ssh_connect()
        exit_code, stdout, stderr = _ssh_exec(client, remote_script, timeout=180)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    if exit_code != 0:
        raise RuntimeError(
            f"套用靜態 IP 到 VMID={vmid} 失敗 (exit={exit_code}): {stderr or stdout}"
        )

    actual_ips = (stdout or "").strip().split()
    if ip_only not in actual_ips:
        logger.warning(
            "VMID=%s 套用靜態 IP 後實際 IP 未包含預期 %s（取得：%s）",
            vmid, ip_only, actual_ips,
        )
    else:
        logger.info("VMID=%s 已套用靜態 IP %s 並驗證成功", vmid, ip_only)


def _inject_ssh_public_key(vmid: int, public_key: str) -> None:
    """部署完成後將平台公鑰寫入容器內 /root/.ssh/authorized_keys。"""
    pub = (public_key or "").strip()
    if not pub:
        return
    pub_b64 = base64.b64encode(pub.encode("utf-8")).decode("ascii")
    script = f"""#!/bin/bash
set -e
CTID={vmid}
PUB_B64='{pub_b64}'
pct exec "$CTID" -- mkdir -p /root/.ssh
pct exec "$CTID" -- chmod 700 /root/.ssh
echo "$PUB_B64" | base64 -d | pct exec "$CTID" -- tee -a /root/.ssh/authorized_keys >/dev/null
pct exec "$CTID" -- chmod 600 /root/.ssh/authorized_keys
"""
    client = None
    try:
        client = _ssh_connect()
        exit_code, _stdout, stderr = _ssh_exec(client, script, timeout=60)
        if exit_code != 0:
            raise RuntimeError(f"注入 SSH 公鑰失敗 (exit={exit_code}): {stderr}")
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Cleanup / rollback
# ---------------------------------------------------------------------------


def _destroy_container(vmid: int) -> None:
    """銷毀失敗的容器（最大努力）。"""
    try:
        resource = proxmox_service.find_resource(vmid)
        node = resource["node"]
        rtype = resource["type"]

        try:
            status = proxmox_service.get_status(node, vmid, rtype)
            if status.get("status") == "running":
                proxmox_service.control(node, vmid, rtype, "stop")
        except Exception as e:
            logger.debug(
                "預期內的停機失敗 (VMID=%s)，仍將繼續 destroy: %s", vmid, e
            )

        delete_params: dict[str, int] = {"purge": 1}
        if rtype == "qemu":
            delete_params["destroy-unreferenced-disks"] = 1
        proxmox_service.delete_resource(node, vmid, rtype, **delete_params)
        logger.info("已清除失敗部署的容器 VMID=%s", vmid)
    except Exception:
        logger.exception("清除容器 VMID=%s 失敗", vmid)


def _cleanup_script_on_node(client, script_path: str) -> None:
    """刪除 Proxmox 節點上的暫存腳本。"""
    try:
        _ssh_exec(client, f"rm -f {script_path}")
        logger.info("已刪除暫存腳本: %s", script_path)
    except Exception:
        logger.warning("刪除暫存腳本失敗: %s", script_path)


# ---------------------------------------------------------------------------
# Main deployment logic
# ---------------------------------------------------------------------------


def _get_storage_settings() -> tuple[str, str]:
    """從資料庫讀取 Proxmox storage 設定，回傳 (iso_storage, data_storage)。

    若 DB 沒設定就向 PVE 詢問可用的 storage：第一個支援 vztmpl 的當 iso，
    第一個支援 rootdir/images 的當 container storage。最差才 fallback 為 local/local-lvm。
    """
    from app.core.db import engine

    with Session(engine) as session:
        config = session.get(ProxmoxConfig, 1)
        if config:
            return config.iso_storage, config.data_storage

    iso = "local"
    data = "local-lvm"
    try:
        proxmox = get_proxmox_api()
        storages = proxmox.storage.get()
        iso_match = next(
            (s for s in storages if "vztmpl" in (s.get("content") or "")),
            None,
        )
        data_match = next(
            (s for s in storages if "rootdir" in (s.get("content") or "")
             or "images" in (s.get("content") or "")),
            None,
        )
        if iso_match and iso_match.get("storage"):
            iso = iso_match["storage"]
        if data_match and data_match.get("storage"):
            data = data_match["storage"]
    except Exception as exc:
        logger.warning("無法從 PVE 取得 storage 列表，使用預設 local/local-lvm: %s", exc)
    return iso, data


def _build_inline_env(
    hostname: str,
    password: str,
    cpu: int,
    ram: int,
    disk: int,
    unprivileged: bool,
    ssh: bool,
    template_storage: str = "local",
    container_storage: str = "local-lvm",
    net_config: dict | None = None,
) -> str:
    r"""產生 inline 環境變數字串（官方 community-scripts 模式）。

    官方用法：var_cpu=4 var_ram=4096 ... bash -c "\$(curl ...)"
    變數以空格分隔放在 bash -c 前面，讓 shell 直接設定到同一進程。
    密碼用單引號包裹以避免特殊字元問題。

    net_config（選填）支援靜態 IP 配置：
        ip_cidr: "10.0.0.5/24" — 要指派給容器的 IPv4/CIDR
        gateway: "10.0.0.1"    — 預設閘道
        bridge:  "vmbr0"       — 要使用的橋接介面
        nameserver: "1.1.1.1"  — DNS（空白分隔多個）
    未提供時 fallback 為 DHCP。
    """
    # 密碼用單引號，內部的單引號用 '\'' 轉義
    safe_password = password.replace("'", "'\\''")
    parts = [
        "TERM=xterm",
        "mode=generated",
        f"var_hostname='{hostname}'",
        f"var_password='{safe_password}'",
        f"var_cpu={cpu}",
        f"var_ram={ram}",
        f"var_disk={disk}",
        f"var_unprivileged={1 if unprivileged else 0}",
        f"var_ssh={'yes' if ssh else 'no'}",
        "var_tags=''",
        "var_ipv6_method='none'",
        f"var_template_storage='{template_storage}'",
        f"var_container_storage='{container_storage}'",
    ]

    if net_config and net_config.get("ip_cidr"):
        parts.append(f"var_net='{net_config['ip_cidr']}'")
        if net_config.get("gateway"):
            parts.append(f"var_gateway='{net_config['gateway']}'")
        if net_config.get("bridge"):
            parts.append(f"var_brg='{net_config['bridge']}'")
        if net_config.get("nameserver"):
            parts.append(f"var_ns='{net_config['nameserver']}'")
    else:
        parts.append("var_net='dhcp'")
        if net_config and net_config.get("bridge"):
            parts.append(f"var_brg='{net_config['bridge']}'")

    return " ".join(parts)


def _run_deployment(task: DeploymentTask, request_data: dict) -> None:  # noqa: C901
    """在背景執行緒中執行腳本部署。"""
    client = None
    temp_script = f"/tmp/campus-cloud-deploy-{task.task_id}.sh"
    vmids_before = _get_all_vmids()
    new_vmid: int | None = None

    try:
        # Ensure cancel event exists (idempotent — start_deployment already creates one)
        cancel_event = _make_cancel_event(task.task_id)
        if cancel_event.is_set():
            raise RuntimeError("部署在啟動前即被取消")

        # 1. SSH 連線
        task.progress = "正在連線到 Proxmox 節點…"
        _store_task(task)
        client = _ssh_connect()
        logger.info("SSH 連線成功，開始部署 %s", request_data["script_path"])

        # 2. 下載腳本到暫存檔
        task.progress = "正在下載部署腳本…"
        _store_task(task)

        script_url = f"{GITHUB_RAW_BASE}/{request_data['script_path']}"
        if not script_url.startswith(GITHUB_RAW_BASE + "/"):
            raise RuntimeError(f"非法腳本 URL（必須以 {GITHUB_RAW_BASE}/ 開頭）: {script_url}")
        # 防護：路徑不可包含 .. 跳脫或 query string
        rel_path = request_data["script_path"]
        if ".." in rel_path or "?" in rel_path or "\n" in rel_path or "\r" in rel_path:
            raise RuntimeError(f"非法腳本路徑: {rel_path!r}")
        download_cmd = f'curl -fsSL -o {temp_script} "{script_url}"'
        exit_code, stdout, stderr = _ssh_exec(client, download_cmd, timeout=60)
        if exit_code != 0:
            raise RuntimeError(f"下載腳本失敗: {stderr}")

        # 2.5 組合 inline 環境變數（官方 community-scripts 模式）
        task.progress = "正在設定無人值守參數…"
        _store_task(task)

        iso_storage, data_storage = _get_storage_settings()
        logger.info("Storage 設定: template=%s, container=%s", iso_storage, data_storage)

        inline_env = _build_inline_env(
            hostname=request_data["hostname"],
            password=request_data["password"],
            cpu=request_data["cpu"],
            ram=request_data["ram"],
            disk=request_data["disk"],
            unprivileged=request_data["unprivileged"],
            ssh=request_data["ssh"],
            template_storage=iso_storage,
            container_storage=data_storage,
            net_config=request_data.get("net_config"),
        )
        # 安全地記錄 inline env（移除 password 與 ssh keys 的值）以利除錯
        try:
            safe_env = " ".join(
                p if not (p.startswith("var_password=") or p.startswith("var_ssh_keys="))
                else p.split("=", 1)[0] + "=***"
                for p in inline_env.split(" ")
            )
            logger.info("Inline env (sanitised): %s", safe_env)
        except Exception as e:
            logger.debug("Sanitised env logging failed (non-fatal): %s", e)

        # 3. 以官方模式執行：VAR=value bash -c "$(cat script)"
        task.progress = "正在執行無人值守部署（可能需要幾分鐘）…"
        _store_task(task)

        deploy_cmd = f'{inline_env} bash -c "$(cat {temp_script})"'
        # 注意：不記錄完整的 deploy_cmd，以免敏感資訊（例如密碼）洩漏到日誌
        logger.info("開始執行無人值守部署腳本: %s", request_data["script_path"])
        exit_code, stdout, stderr = _ssh_exec_streaming(
            client, deploy_cmd, task, timeout=900, cancel_event=cancel_event,
        )

        task.output = stdout
        if stderr:
            task.output += f"\n--- STDERR ---\n{stderr}"

        # 4. 清除暫存檔案
        task.progress = "正在清除暫存檔案…"
        _store_task(task)
        _cleanup_script_on_node(client, temp_script)

        # 5. 檢查執行結果
        if exit_code != 0:
            raise RuntimeError(
                f"腳本執行失敗 (exit code {exit_code}):\n{stderr or stdout}"
            )

        # 6. 找出新建的 VMID
        task.progress = "正在確認部署結果…"
        _store_task(task)

        new_vmid = _find_new_vmid(vmids_before)
        if new_vmid is None:
            new_vmid = _find_vmid_by_hostname(request_data["hostname"])

        if new_vmid is None:
            raise RuntimeError(
                "腳本執行完成但無法找到新建的容器。請檢查 Proxmox 控制台。"
            )

        # 7. 加入 Campus Cloud Pool
        task.progress = "正在加入 Campus Cloud 集區…"
        _store_task(task)
        try:
            _add_to_pool(new_vmid)
        except Exception as e:
            logger.warning("加入 Pool 失敗 (非致命): %s", e)

        # 8. 套用預設防火牆規則
        task.progress = "正在套用預設防火牆規則…"
        _store_task(task)
        try:
            _apply_default_firewall(new_vmid)
        except Exception as e:
            logger.warning("套用防火牆規則失敗 (非致命): %s", e)

        # 8.5 強制套用靜態 IP（如果有分配）— 失敗視為致命，避免使用者拿到錯 IP
        net_config = request_data.get("net_config")
        if net_config and net_config.get("ip_cidr"):
            task.progress = "正在套用靜態 IP 設定…"
            _store_task(task)
            _enforce_static_network(new_vmid, net_config)

        # 8.6 注入平台 SSH 公鑰（若提供），讓平台之後能 SSH 進去管理
        ssh_pub = request_data.get("ssh_public_key")
        if ssh_pub:
            try:
                _inject_ssh_public_key(new_vmid, ssh_pub)
            except Exception as e:
                logger.warning("注入 SSH 公鑰失敗 (非致命): %s", e)
                task.message = (
                    (task.message or "") + f"\n[警告] 注入 SSH 公鑰失敗：{e}"
                ).strip()
                _store_task(task)

        # 9. 清除 community-scripts 自動產生的 description
        try:
            _clear_description(new_vmid)
        except Exception as e:
            logger.warning("清除 description 失敗 (非致命): %s", e)

        task.vmid = new_vmid
        task.status = "completed"
        task.progress = "部署完成"
        task.message = (
            f"服務 {request_data['template_slug']} 已成功部署，"
            f"VMID: {new_vmid}，主機名稱: {request_data['hostname']}"
        )
        _store_task(task)
        logger.info(
            "部署成功: template=%s, vmid=%s",
            request_data["template_slug"],
            new_vmid,
        )

    except Exception as e:
        error_msg = str(e)
        cancelled = _is_cancelled(task.task_id)
        if cancelled:
            logger.warning("部署被使用者取消: %s", error_msg)
        else:
            logger.error("部署失敗: %s", error_msg)

        # 回滾：清除暫存檔案
        if client:
            _cleanup_script_on_node(client, temp_script)

        # 回滾：銷毀已建立的容器
        rollback_vmid = new_vmid or _find_new_vmid(vmids_before)
        if rollback_vmid is None:
            rollback_vmid = _find_vmid_by_hostname(request_data["hostname"])

        if rollback_vmid:
            logger.info("回滾：正在銷毀容器 VMID=%s", rollback_vmid)
            _destroy_container(rollback_vmid)
            error_msg += f"\n已自動回滾：銷毀了部分建立的容器 (VMID: {rollback_vmid})"

        # 回滾：釋放預分配的 IP（若 caller 提供 candidate_vmid）
        candidate_vmid = request_data.get("candidate_vmid")
        if candidate_vmid:
            try:
                from app.core.db import engine as _engine
                from app.services.network import ip_management_service as _ip
                with Session(_engine) as _s:
                    released = _ip.release_ip(_s, int(candidate_vmid))
                    _s.commit()
                    if released:
                        error_msg += f"\n已釋放 IP {released}"
            except Exception as ip_exc:
                logger.warning("釋放 IP 失敗 (VMID=%s): %s", candidate_vmid, ip_exc)

        task.status = "failed"
        task.error = error_msg
        task.progress = "已取消，已回滾" if cancelled else "部署失敗，已回滾"
        _store_task(task)

    finally:
        _drop_cancel_event(task.task_id)
        if client:
            try:
                client.close()
            except Exception:
                pass


def cancel_task(task_id: str) -> bool:
    """請求取消正在執行的部署任務。

    觸發 streaming 迴圈中斷 → exception 走 rollback 流程，
    自動 destroy 容器 + 釋放預分配 IP。
    回傳 True 表示已標記取消（任務正在跑），False 表示任務已結束或不存在。
    """
    task = _TASK_STORE.get(task_id)
    if task is None:
        return False
    if task.status != "running":
        return False
    with _CANCEL_LOCK:
        ev = _CANCEL_EVENTS.get(task_id)
    if ev is None:
        # Task hasn't reached the streaming step yet — create a placeholder so
        # the deploy thread will pick it up when it calls _make_cancel_event.
        # In practice the streaming step is reached within seconds of start.
        return False
    ev.set()
    task.progress = "正在取消…"
    _store_task(task)
    logger.info("已請求取消部署任務 %s", task_id)
    return True


def start_deployment(
    request_data: dict,
    user_id: str,
) -> str:
    """啟動背景部署任務，回傳 task_id。"""
    task_id = str(uuid.uuid4())
    task = DeploymentTask(
        task_id=task_id,
        user_id=user_id,
        template_name=request_data.get("os_info") or request_data.get("template_slug", ""),
        template_slug=request_data.get("template_slug", ""),
        script_path=request_data.get("script_path"),
        hostname=request_data.get("hostname"),
    )
    _store_task(task)
    # Pre-create cancel event so cancel_task() works even before _run_deployment
    # reaches the streaming step.
    _make_cancel_event(task_id)

    thread = threading.Thread(
        target=_run_deployment,
        args=(task, request_data),
        daemon=True,
        name=f"deploy-{task_id[:8]}",
    )
    thread.start()

    return task_id


def deploy_for_vm_request_sync(
    *,
    user_id: str,
    template_slug: str,
    script_path: str | None,
    hostname: str,
    password: str,
    cpu: int,
    ram: int,
    disk: int,
    unprivileged: bool = True,
    ssh: bool = True,
    environment_type: str = "服務模板",
    os_info: str | None = None,
    net_config: dict | None = None,
    ssh_public_key: str | None = None,
    request_id: str | None = None,
    candidate_vmid: int | None = None,
) -> tuple[int, DeploymentTask]:
    """同步執行 community-scripts 部署，成功回傳 (vmid, task)。

    用於 VM 請求自動核准後的 LXC 建立（由腳本建立容器）。
    net_config 若提供會以靜態 IP 建立容器（參見 _build_inline_env）。
    ssh_public_key 若提供會在部署完成後注入到容器 /root/.ssh/authorized_keys。
    request_id 若提供會做去重，避免同一個 VM 請求被觸發多次部署。
    失敗會拋出 RuntimeError 且 task 內含錯誤訊息。
    """
    task_id = str(uuid.uuid4())
    if not _try_claim_request(request_id, task_id):
        raise RuntimeError(
            f"VM 請求 {request_id} 已有部署任務在進行中，拒絕重複觸發"
        )
    task = DeploymentTask(
        task_id=task_id,
        user_id=user_id,
        template_name=os_info or template_slug,
        template_slug=template_slug,
        script_path=script_path or f"ct/{template_slug}.sh",
        hostname=hostname,
        request_id=request_id,
    )
    _store_task(task)
    # Pre-create cancel event so cancel_task() can interrupt before streaming starts
    _make_cancel_event(task_id)

    request_data = {
        "template_slug": template_slug,
        "script_path": script_path or f"ct/{template_slug}.sh",
        "hostname": hostname,
        "password": password,
        "cpu": cpu,
        "ram": ram,
        "disk": max(int(disk), 1),
        "unprivileged": unprivileged,
        "ssh": ssh,
        "environment_type": environment_type,
        "os_info": os_info,
        "net_config": net_config,
        "ssh_public_key": ssh_public_key,
        "candidate_vmid": candidate_vmid,
    }

    try:
        _run_deployment(task, request_data)
    finally:
        _release_request(request_id, task_id)

    if task.status == "completed" and task.vmid is not None:
        return task.vmid, task

    raise RuntimeError(task.error or "Script deployment failed")
